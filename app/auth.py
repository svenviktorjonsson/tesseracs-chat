# app/auth.py
import datetime
import sqlite3
import traceback
from fastapi import Request, Response, HTTPException, Depends
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
from passlib.context import CryptContext # Import CryptContext
from starlette.status import HTTP_401_UNAUTHORIZED
from typing import Optional, Dict, Any

# Assuming database utilities are in the same directory
# We still need generate_secure_token for session tokens, but hash_value is removed for passwords
from .database import get_db_connection, generate_secure_token, hash_value as hash_session_token # Rename hash_value import

# --- Passlib Configuration ---
# Use bcrypt, the recommended default. Schemes lists hashing algorithms.
# deprecated="auto" will automatically upgrade hashes if needed in the future.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# --- End Passlib Configuration ---

# Configuration for the session cookie
SESSION_COOKIE_NAME = "tesseracs_chat_session_token"
SESSION_DURATION_DAYS = 7

# Dependency for getting the session token from the cookie
cookie_scheme = APIKeyCookie(name=SESSION_COOKIE_NAME, auto_error=False)

# Pydantic model for the token response (used by /token endpoint)
class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None

# --- Password Hashing Utilities ---
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a stored hash using passlib."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hashes a plain password using passlib."""
    return pwd_context.hash(password)
# --- End Password Hashing Utilities ---


async def create_user_session(response: Response, user_id: int) -> str:
    """
    Creates a new session token in the database for the given user_id,
    and sets the raw token value in an HTTPOnly cookie on the response object.
    (Uses hash_session_token for the session token itself, not passlib)
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        session_token_raw = generate_secure_token(32)
        # Use the specific hash function for session tokens (e.g., SHA256)
        session_token_hashed = hash_session_token(session_token_raw)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        expires_at = now_utc + datetime.timedelta(days=SESSION_DURATION_DAYS)
        cursor.execute(
            "INSERT INTO auth_tokens (user_id, token_hash, token_type, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, session_token_hashed, "session", expires_at.isoformat())
        )
        conn.commit()
        print(f"AUTH: Session token stored in DB for user_id: {user_id}")
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"AUTH ERROR: Storing session token failed: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Could not create session due to a database error.")
    except Exception as e:
        if conn: conn.rollback()
        print(f"AUTH ERROR: Unexpected error storing session token: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Unexpected server error during session creation.")
    finally:
        if conn: conn.close()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token_raw,
        httponly=True,
        secure=False, # TODO: Set to True in production (HTTPS)
        samesite="lax",
        max_age=int(datetime.timedelta(days=SESSION_DURATION_DAYS).total_seconds()),
        path="/",
        domain=None
    )
    print(f"AUTH: Session cookie '{SESSION_COOKIE_NAME}' set in response for user_id: {user_id}.")
    return session_token_raw


async def get_current_user(
    request: Request,
    session_token_raw: Optional[str] = Depends(cookie_scheme)
) -> Optional[Dict[str, Any]]:
    """
    FastAPI Dependency: Retrieves the current authenticated user based on the
    session token found in the request's cookie.
    (Uses hash_session_token for the session token itself, not passlib)
    """
    token_to_verify = session_token_raw
    if not token_to_verify:
        token_to_verify = request.cookies.get(SESSION_COOKIE_NAME)
    if not token_to_verify:
        return None

    # Use the specific hash function for session tokens
    session_token_hashed = hash_session_token(token_to_verify)
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
    except sqlite3.Error as db_err:
        print(f"AUTH ERROR: Database error during session token verification: {db_err}")
        traceback.print_exc()
        user_dict = None
    except Exception as e:
        print(f"AUTH ERROR: Unexpected error during session token verification: {e}")
        traceback.print_exc()
        user_dict = None
    finally:
        if conn: conn.close()
    return user_dict


async def get_current_active_user(
    user: Optional[Dict[str, Any]] = Depends(get_current_user)
) -> Optional[Dict[str, Any]]:
    """FastAPI Dependency: Gets the current active user."""
    return user


async def get_user_by_session_token(session_token_raw: str) -> Optional[Dict[str, Any]]:
    """
    Authenticates a user based purely on a raw session token string.
    (Uses hash_session_token for the session token itself, not passlib)
    """
    if not session_token_raw:
        return None

    # Use the specific hash function for session tokens
    session_token_hashed = hash_session_token(session_token_raw)
    conn = None
    user_dict = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
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
                user_dict = None
    except sqlite3.Error as db_err:
        print(f"AUTH ERROR (get_user_by_session_token): Database error: {db_err}")
        traceback.print_exc()
        user_dict = None
    except Exception as e:
        print(f"AUTH ERROR (get_user_by_session_token): Unexpected error: {e}")
        traceback.print_exc()
        user_dict = None
    finally:
        if conn: conn.close()
    return user_dict


async def logout_user(response: Response, session_token_raw: Optional[str]):
    """
    Logs out the user by invalidating the session token in the database
    (if provided) and clearing the session cookie from the browser.
    (Uses hash_session_token for the session token itself, not passlib)
    """
    if session_token_raw:
        # Use the specific hash function for session tokens
        session_token_hashed = hash_session_token(session_token_raw)
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute(
                """
                UPDATE auth_tokens SET used_at = ?, expires_at = ?
                WHERE token_hash = ? AND token_type = 'session'
                """,
                (now_utc_iso, now_utc_iso, session_token_hashed)
            )
            conn.commit()
            print(f"AUTH: Session token invalidated in DB for hash {session_token_hashed[:10]}.")
        except sqlite3.Error as db_err:
            if conn: conn.rollback()
            print(f"AUTH ERROR: Failed to invalidate session token in DB: {db_err}")
            traceback.print_exc()
        except Exception as e:
             if conn: conn.rollback()
             print(f"AUTH ERROR: Unexpected error invalidating session token: {e}")
             traceback.print_exc()
        finally:
            if conn: conn.close()

    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=False, # TODO: Set to True in production (HTTPS)
        samesite="lax",
        path="/"
    )
    print(f"AUTH: Session cookie '{SESSION_COOKIE_NAME}' cleared from browser.")
