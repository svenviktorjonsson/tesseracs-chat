# app/auth.py
import datetime
import sqlite3
import traceback
from fastapi import Request, Response, HTTPException, Depends
from fastapi.security import APIKeyCookie
from starlette.status import HTTP_302_FOUND, HTTP_401_UNAUTHORIZED
from typing import Optional, Dict, Any

from .database import get_db_connection, hash_value, generate_secure_token

# Configuration for the session cookie
SESSION_COOKIE_NAME = "tesseracs_chat_session_token"
SESSION_DURATION_DAYS = 7
MAGIC_LINK_DURATION_MINUTES = 15

# Dependency for getting the session token from the cookie
cookie_scheme = APIKeyCookie(name=SESSION_COOKIE_NAME, auto_error=False)

async def create_user_session(response: Response, user_id: int) -> str:
    """
    Creates a new session token in the database, and sets the raw token
    in an HTTPOnly cookie.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        session_token_raw = generate_secure_token(32)
        session_token_hashed = hash_value(session_token_raw)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        expires_at = now_utc + datetime.timedelta(days=SESSION_DURATION_DAYS)
        cursor.execute(
            "INSERT INTO auth_tokens (user_id, token_hash, token_type, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, session_token_hashed, "session", expires_at.isoformat())
        )
        conn.commit()
        print(f"AUTH: Session token stored in DB for user_id: {user_id}")
    except Exception as e:
        if conn: conn.rollback()
        print(f"AUTH ERROR: Storing session token failed: {e}")
        raise HTTPException(status_code=500, detail="Could not create session due to a database error.")
    finally:
        if conn: conn.close()

    # Set the cookie in the response - being very explicit
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token_raw,
        httponly=True,         # MUST be True for security
        secure=False,          # MUST be False for local HTTP testing
        samesite="lax",        # 'lax' is usually best for logins, allows top-level navigation redirects
        max_age=int(datetime.timedelta(days=SESSION_DURATION_DAYS).total_seconds()),
        path="/",              # CRITICAL: Ensure cookie applies to the whole site ('/' path)
        domain=None            # Explicitly None uses the current host (127.0.0.1)
    )
    # Log exactly what was set
    print(f"AUTH: Session cookie '{SESSION_COOKIE_NAME}' set in response for user_id: {user_id}. Secure=False, HttpOnly=True, SameSite=lax, Path=/, Domain=None")
    return session_token_raw


async def get_current_user(
    request: Request, # Add Request object to directly inspect cookies
    session_token_raw: Optional[str] = Depends(cookie_scheme) # Keep Depends for standard way
) -> Optional[Dict[str, Any]]:
    """
    Dependency to get the current authenticated user based on the session token in the cookie.
    Returns a user dictionary if authenticated and active, otherwise None.
    """
    # --- ENHANCED DEBUG LOGGING ---
    print(f"AUTH get_current_user: Request path: {request.url.path}") # Log path being accessed
    print(f"AUTH get_current_user: All cookies received in request: {request.cookies}")
    cookie_from_headers = request.cookies.get(SESSION_COOKIE_NAME)
    print(f"AUTH get_current_user: Reading '{SESSION_COOKIE_NAME}' directly from request.cookies: {'Present' if cookie_from_headers else 'Not Found'}")
    print(f"AUTH get_current_user: Value via Depends(cookie_scheme): {'Present' if session_token_raw else 'Not Found'}")
    # --- END ENHANCED DEBUG LOGGING ---

    token_to_verify = session_token_raw
    if not token_to_verify:
         # print(f"AUTH get_current_user: NO session token found via Depends(cookie_scheme).")
         if cookie_from_headers:
              # print(f"AUTH get_current_user: WARNING - Token found directly in headers but not via Depends. Using header value.")
              token_to_verify = cookie_from_headers
         else:
              # print(f"AUTH get_current_user: Token not found via direct header reading either. Returning None.")
              return None
    # else:
         # print(f"AUTH get_current_user: Using token found via Depends (raw prefix): {token_to_verify[:5]}...")

    session_token_hashed = hash_value(token_to_verify)
    conn = None
    user_dict = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            """
            SELECT u.id, u.name, u.email, u.is_active
            FROM users u JOIN auth_tokens at ON u.id = at.user_id
            WHERE at.token_hash = ? AND at.token_type = 'session'
              AND at.expires_at > ? AND at.used_at IS NULL
            """,
            (session_token_hashed, now_utc_iso)
        )
        user_row = cursor.fetchone()
        if user_row:
            user_dict = dict(user_row)
            if not user_dict["is_active"]:
                print(f"AUTH get_current_user: User {user_dict.get('email')} found but is INACTIVE.")
                user_dict = None
            else:
                print(f"AUTH get_current_user: User {user_dict.get('email')} authenticated successfully via session token.")
        else:
            print(f"AUTH get_current_user: No valid, active user session found for provided token hash {session_token_hashed[:10]}...")
            user_dict = None
    except Exception as e:
        print(f"AUTH ERROR: Exception during token verification: {e}")
        traceback.print_exc()
        user_dict = None
    finally:
        if conn: conn.close()
    return user_dict


async def get_current_active_user(user: Optional[Dict[str, Any]] = Depends(get_current_user)) -> Optional[Dict[str, Any]]:
    """Convenience dependency returning the user dict if found and active."""
    return user


async def create_magic_link_token(user_id: int, conn: Optional[sqlite3.Connection] = None) -> str:
    """Creates a magic link token, storing hash in DB. Uses provided conn if available."""
    # (Implementation from previous step is correct)
    local_conn = False
    if conn is None:
        try: conn = get_db_connection(); local_conn = True
        except Exception as e: raise HTTPException(status_code=500, detail="Database connection error.")
    if conn is None: raise HTTPException(status_code=500, detail="DB connection is unexpectedly None.")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cursor.fetchone(): raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")
        magic_token_raw = generate_secure_token(24)
        magic_token_hashed = hash_value(magic_token_raw)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        expires_at = now_utc + datetime.timedelta(minutes=MAGIC_LINK_DURATION_MINUTES)
        cursor.execute(
            "INSERT INTO auth_tokens (user_id, token_hash, token_type, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, magic_token_hashed, "magic_login", expires_at.isoformat())
        )
        if local_conn: conn.commit()
        return magic_token_raw
    except HTTPException as http_exc:
        if local_conn: conn.rollback()
        raise http_exc
    except Exception as e:
        print(f"AUTH ERROR (Unexpected in create_magic_link): {type(e).__name__} - {e}")
        traceback.print_exc();
        if local_conn: conn.rollback()
        raise HTTPException(status_code=500, detail=f"Server error creating token ({type(e).__name__}).")
    finally:
        if local_conn and conn: conn.close()


async def verify_magic_link_token(token_raw: str) -> Optional[Dict[str, Any]]:
    """Verifies a raw magic link token, marks it as used, returns user info if valid."""
    # (Implementation from previous step is correct)
    token_hashed = hash_value(token_raw)
    conn = None
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        now_utc = datetime.datetime.now(datetime.timezone.utc); now_utc_iso = now_utc.isoformat()
        cursor.execute(
            """SELECT at.user_id, at.expires_at, at.used_at, u.is_active, u.name, u.email, u.id as user_db_id
               FROM auth_tokens at JOIN users u ON at.user_id = u.id
               WHERE at.token_hash = ? AND at.token_type = 'magic_login'""", (token_hashed,)
        )
        row = cursor.fetchone()
        if not row or row["used_at"] is not None: return None
        expires_at_dt = datetime.datetime.fromisoformat(row["expires_at"].replace('Z', '+00:00'))
        if expires_at_dt.tzinfo is None: expires_at_dt = expires_at_dt.replace(tzinfo=datetime.timezone.utc)
        if expires_at_dt < now_utc or not row["is_active"]: return None
        cursor.execute(
            "UPDATE auth_tokens SET used_at = ? WHERE token_hash = ? AND token_type = 'magic_login'",
            (now_utc_iso, token_hashed)
        )
        conn.commit()
        return { "id": row["user_db_id"], "name": row["name"], "email": row["email"], "is_active": row["is_active"] }
    except Exception as e:
        if conn: conn.rollback(); print(f"AUTH ERROR: Verifying magic link token: {e}")
        return None
    finally:
        if conn: conn.close()

async def get_user_by_session_token(session_token_raw: str) -> Optional[Dict[str, Any]]:
    """
    Authenticates a user based purely on a raw session token string.
    Returns a user dictionary if authenticated and active, otherwise None.
    Suitable for WebSocket authentication.
    """
    if not session_token_raw:
        print("AUTH (get_user_by_session_token): No session token provided.")
        return None

    session_token_hashed = hash_value(session_token_raw)
    conn = None
    user_dict = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Ensure datetime is timezone-aware for comparison if expires_at is stored as UTC
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_utc_iso = now_utc.isoformat()

        cursor.execute(
            """
            SELECT u.id, u.name, u.email, u.is_active
            FROM users u JOIN auth_tokens at ON u.id = at.user_id
            WHERE at.token_hash = ? AND at.token_type = 'session'
              AND at.expires_at > ? AND at.used_at IS NULL
            """,
            (session_token_hashed, now_utc_iso)
        )
        user_row = cursor.fetchone()
        if user_row:
            user_dict = dict(user_row)
            if not user_dict["is_active"]:
                print(f"AUTH (get_user_by_session_token): User {user_dict.get('email')} found but is INACTIVE.")
                user_dict = None 
            else:
                print(f"AUTH (get_user_by_session_token): User {user_dict.get('email')} authenticated successfully for WebSocket.")
        else:
            print(f"AUTH (get_user_by_session_token): No valid, active user session found for token hash {session_token_hashed[:10]}...")
    except Exception as e:
        print(f"AUTH ERROR (get_user_by_session_token): Exception during token verification: {e}")
        traceback.print_exc()
        user_dict = None
    finally:
        if conn: conn.close()
    return user_dict

async def logout_user(response: Response, session_token_raw: Optional[str] = Depends(cookie_scheme)):
    """Logs out user: invalidates session token in DB & clears browser cookie."""
    # (Implementation from previous step is correct)
    if session_token_raw:
        session_token_hashed = hash_value(session_token_raw); conn = None
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute(
                "UPDATE auth_tokens SET used_at = ?, expires_at = ? WHERE token_hash = ? AND token_type = 'session'",
                (now_utc_iso, now_utc_iso, session_token_hashed)
            )
            conn.commit(); # print(f"AUTH: Session token invalidated in DB.")
        except Exception as e:
            if conn: conn.rollback(); print(f"AUTH ERROR: Invalidating session token: {e}")
        finally:
            if conn: conn.close()
    response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, secure=False, samesite="lax", path="/")
    # print(f"AUTH: Session cookie cleared from browser.")
    return {"message": "Successfully logged out"}
