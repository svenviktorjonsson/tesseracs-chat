# app/auth.py
import os
import secrets
import sqlite3
import traceback # Added from earlier version
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from fastapi import Depends, HTTPException, status, Response, Request # Added Request
from fastapi.security import APIKeyCookie, OAuth2PasswordBearer # Added APIKeyCookie

from passlib.context import CryptContext

# Assuming your project structure allows these relative imports
from . import models # For type hinting if needed (e.g., User model for return types)
from . import config
# Import necessary functions from database.py
from .database import get_db_connection, generate_secure_token, hash_value as hash_session_token

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Constants ---
SESSION_COOKIE_NAME = "tesseracs_session_token"
SESSION_DURATION_DAYS = 7 # From earlier version

# OAuth2 scheme for API endpoints (if you use Bearer tokens for some APIs)
# This is for FastAPI's dependency injection to get token from Authorization header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token") # tokenUrl points to your login endpoint

# Dependency for getting the session token from the cookie
cookie_scheme = APIKeyCookie(name=SESSION_COOKIE_NAME, auto_error=False)


# --- Password Utilities ---
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a stored hash using passlib."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hashes a plain password using passlib (for user passwords)."""
    return pwd_context.hash(password)

# --- User Authentication (Database Interaction for login) ---
def authenticate_user_from_db(
    conn: sqlite3.Connection, email: str, password: str
) -> Optional[Dict[str, Any]]:
    """
    Authenticates a user by fetching from DB and verifying password.
    This is typically called by the /token login route.

    Args:
        conn: Active SQLite database connection (passed from the route).
        email: User's email.
        password: User's plain text password.

    Returns:
        A dictionary containing user details if successful, otherwise None.
    """
    if not conn:
        print("ERROR (auth.authenticate_user_from_db): Database connection is None.")
        return None
    
    try:
        cursor = conn.cursor()
        # Ensure email is normalized (e.g., lowercased) for lookup
        normalized_email = email.lower().strip()
        cursor.execute(
            "SELECT id, name, email, password_hash, is_active FROM users WHERE email = ?",
            (normalized_email,)
        )
        user_row = cursor.fetchone()

        if not user_row:
            print(f"DEBUG (auth.authenticate_user_from_db): User not found for email: {normalized_email}")
            return None 

        user_data = dict(user_row)
        stored_password_hash = user_data.get("password_hash")

        if not stored_password_hash or not verify_password(password, stored_password_hash):
            print(f"DEBUG (auth.authenticate_user_from_db): Password verification failed for email: {normalized_email}")
            return None

        # Return relevant user details, excluding the password hash
        return {
            "id": user_data["id"],
            "name": user_data["name"],
            "email": user_data["email"],
            "is_active": user_data["is_active"],
        }
    except sqlite3.Error as e:
        print(f"ERROR (auth.authenticate_user_from_db): Database error - {e}")
        traceback.print_exc()
        return None
    except Exception as e: # Catch any other unexpected errors
        print(f"ERROR (auth.authenticate_user_from_db): Unexpected error - {e}")
        traceback.print_exc()
        return None


# --- Session Token Management (Database-backed sessions) ---
async def create_user_session(response: Response, user_id: int) -> str:
    """
    Creates a new session token, stores its HASH in the database,
    and sets the RAW token as an HttpOnly cookie in the response.
    Uses `hash_session_token` (from database.py) for session tokens.
    """
    session_token_raw = generate_secure_token(32) # Generate a new raw token
    # Use the specific hash function for session tokens (e.g., SHA256 from database.py)
    session_token_hashed = hash_session_token(session_token_raw) 
    
    expires_delta = timedelta(days=SESSION_DURATION_DAYS)
    expires_at = datetime.now(timezone.utc) + expires_delta
    
    conn = None
    try:
        conn = get_db_connection() # Get a new connection
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO auth_tokens (user_id, token_hash, token_type, created_at, expires_at)
               VALUES (?, ?, ?, datetime('now', 'utc'), ?)""",
            (user_id, session_token_hashed, 'session', expires_at.isoformat())
        )
        conn.commit()
        
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token_raw, # Set the RAW token in the cookie
            httponly=True,
            max_age=int(expires_delta.total_seconds()),
            expires=expires_at,
            samesite="Lax",
            secure=config.BASE_URL.startswith("https://"), # Secure cookie if served over HTTPS
            path="/"
        )
        print(f"AUTH: Session cookie '{SESSION_COOKIE_NAME}' set for user_id {user_id}. Expires: {expires_at.isoformat()}")
        return session_token_raw # Return the raw token (e.g., for /token response model)
        
    except sqlite3.Error as e:
        print(f"ERROR (auth.create_user_session): Database error - {e}")
        traceback.print_exc()
        if conn: conn.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not create user session due to a database issue.")
    except Exception as e:
        print(f"ERROR (auth.create_user_session): Unexpected error - {e}")
        traceback.print_exc()
        if conn: conn.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not create user session due to an unexpected server error.")
    finally:
        if conn:
            conn.close()

async def get_user_by_session_token_internal(token_raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Internal helper: Retrieves user details based on a RAW session token.
    Hashes the raw token and verifies it against hashed tokens in the database.
    Uses `hash_session_token` (from database.py).
    """
    if not token_raw:
        return None
    
    session_token_hashed = hash_session_token(token_raw) # Hash the raw token for DB lookup
    conn = None
    try:
        conn = get_db_connection() # Get a new connection
        cursor = conn.cursor()
        now_utc_iso = datetime.now(timezone.utc).isoformat()
        
        cursor.execute(
            """SELECT u.id, u.name, u.email, u.is_active
               FROM users u JOIN auth_tokens at ON u.id = at.user_id
               WHERE at.token_hash = ? AND at.token_type = 'session' 
               AND at.expires_at > ? AND at.used_at IS NULL
            """, (session_token_hashed, now_utc_iso)
        )
        user_row = cursor.fetchone()

        if user_row:
            user_data = dict(user_row)
            return {
                "id": user_data["id"],
                "name": user_data["name"],
                "email": user_data["email"],
                "is_active": user_data["is_active"]
            }
        return None
        
    except sqlite3.Error as e:
        print(f"ERROR (auth.get_user_by_session_token_internal): Database error - {e}")
        traceback.print_exc()
        return None
    except Exception as e:
        print(f"ERROR (auth.get_user_by_session_token_internal): Unexpected error - {e}")
        traceback.print_exc()
        return None
    finally:
        if conn:
            conn.close()

# This `get_current_user` is for cookie-based authentication (Optional user)
async def get_current_user(
    session_token_raw: Optional[str] = Depends(cookie_scheme)
) -> Optional[Dict[str, Any]]:
    """
    FastAPI Dependency: Retrieves the current authenticated user based on the
    session token from the cookie. Returns user dict or None if not authenticated/active.
    Does not raise HTTPException, allowing routes to handle optional authentication.
    """
    if not session_token_raw:
        return None
    
    user = await get_user_by_session_token_internal(session_token_raw)
    if user and user.get("is_active"):
        return user
    return None


# This `get_current_active_user` is for cookie-based authentication (Required active user)
async def get_current_active_user(
    session_token_raw: Optional[str] = Depends(cookie_scheme)
) -> Dict[str, Any]:
    """
    FastAPI Dependency: Retrieves the current authenticated AND active user.
    Raises HTTPException if not authenticated or user is inactive.
    This is typically used for routes that require a logged-in, active user.
    """
    if not session_token_raw:
        # This case might be hit if cookie_scheme's auto_error=False and cookie is missing
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (no session cookie)",
            headers={"WWW-Authenticate": "Session"}, 
        )
    
    user = await get_user_by_session_token_internal(session_token_raw)
    
    if not user:
        # Consider how to handle invalid cookie (e.g., if main.py should clear it on redirect)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
            headers={"WWW-Authenticate": "Session"},
        )
    if not user.get("is_active"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive")
    
    return user


# This `get_current_user_bearer` is a placeholder for API Bearer token authentication
# It uses oauth2_scheme, which looks for "Authorization: Bearer <token>" header.
async def get_current_user_bearer(token: str = Depends(oauth2_scheme)) -> Optional[Dict[str, Any]]:
    """
    Dependency to get the current user from a Bearer token (for API calls).
    This is an example if you were using JWTs or other Bearer tokens.
    It's kept separate from cookie-based session authentication.
    """
    if not token:
        return None
    # In a real scenario, you would:
    # 1. Decode the JWT token (if it's a JWT).
    # 2. Validate its signature, issuer, audience, expiry.
    # 3. Extract user identifier (e.g., user_id or sub) from token claims.
    # 4. Fetch user from database based on that identifier.
    # For now, this is a placeholder and returns None.
    print(f"DEBUG (auth.get_current_user_bearer): Received Bearer token: {token[:15]}...")
    # Example: user_id = my_jwt_decode_function(token).get("sub")
    # user = await fetch_user_from_db_by_id(user_id)
    # return user
    return None # Replace with actual Bearer token validation and user lookup


async def logout_user(response: Response, session_token_raw: Optional[str]):
    """
    Logs out the user by invalidating their session token in the database
    (if provided) and clearing the session cookie from the browser.
    Uses `hash_session_token` for session tokens.
    """
    if session_token_raw:
        session_token_hashed = hash_session_token(session_token_raw)
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            now_utc_iso = datetime.now(timezone.utc).isoformat()
            # Mark the specific token as used by setting used_at and making it expire immediately
            cursor.execute(
                """UPDATE auth_tokens 
                   SET used_at = ?, expires_at = ?
                   WHERE token_hash = ? AND token_type = 'session'""",
                (now_utc_iso, now_utc_iso, session_token_hashed)
            )
            conn.commit()
            print(f"AUTH: Session token (hash starting {session_token_hashed[:10]}...) marked as used/expired in DB for logout.")
        except sqlite3.Error as e:
            print(f"ERROR (auth.logout_user): Database error invalidating token - {e}")
            traceback.print_exc()
            if conn: conn.rollback()
            # Proceed with cookie deletion even if DB update fails
        except Exception as e:
            print(f"ERROR (auth.logout_user): Unexpected error invalidating token - {e}")
            traceback.print_exc()
            if conn: conn.rollback()
        finally:
            if conn:
                conn.close()

    # Always attempt to delete the cookie from the browser
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=config.BASE_URL.startswith("https://"), # Match secure attribute used when setting
        samesite="Lax", # Match samesite attribute
        path="/"
    )
    print(f"AUTH: Session cookie '{SESSION_COOKIE_NAME}' cleared from browser response.")
