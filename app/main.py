
# app/main.py

# Standard library imports
import os
import sys
import uvicorn
import webbrowser
import traceback
from pathlib import Path
import asyncio
import json
import sqlite3
import uuid
from urllib.parse import urlparse
import datetime
from typing import Optional, Dict, Any, List # For type hinting

# FastAPI and related imports
from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Form,
    Depends,
    Response as FastAPIResponse, # Aliased to avoid naming conflicts
    Path as FastApiPath,        # Aliased to avoid naming conflicts
    Body,
    status
)
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState

# Pydantic core imports (might still be needed for FastAPI's dependency injection if not all models are covered)
# However, specific model fields like EmailStr, constr are now expected to be used within app/models.py
from pydantic import BaseModel # BaseModel might be used if you define ad-hoc request/response models directly in routes
                             # If all models are in app/models.py, this specific line might become redundant.

# Project local imports
from . import config
from . import state
from . import llm
from . import docker_utils
from . import utils
from . import database
from . import auth
from . import email_utils
from . import models # Import your new models file

# --- FastAPI App Initialization ---
app = FastAPI(title="Tesseracs Chat")

# --- Database Initialization on Startup ---
# This event handler runs when the FastAPI application starts up.
@app.on_event("startup")
async def startup_event():
    """
    Performs initialization tasks when the application starts:
    - Ensures the database directory exists.
    - Initializes the database schema.
    - Checks Docker client availability.
    - Checks LLM model connection.
    """
    print("Application startup: Initializing database...")
    # Ensure the parent directory for the SQLite database file exists.
    database.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Initialize database tables if they don't already exist.
    database.init_db()
    print("Database initialization check complete.")

    # Check if the Docker client is available.
    if docker_utils.get_docker_client() is None:
        print("WARNING: Docker client unavailable during startup. Code execution features will be disabled.")
    else:
        print("Docker client confirmed available at startup.")

    # Attempt to connect to the LLM.
    try:
        llm.get_model() # This function should attempt to initialize/connect to the LLM.
        print("LLM model connection checked successfully.")
    except Exception as e:
        print(f"CRITICAL ERROR during startup LLM check: {e}")
        # Depending on severity, you might want to sys.exit(1) here if LLM is essential.


# --- Static Files Setup ---
# This section configures how static files (CSS, JavaScript, images) are served.

# Validate that the base static directory is correctly configured and exists.
if not config.STATIC_DIR or not config.STATIC_DIR.is_dir():
    print(f"CRITICAL ERROR: Base static directory invalid or not found: {config.STATIC_DIR}")
    sys.exit(1) # Exit if the static directory is not found.

# Define the path to the 'dist' directory, typically containing bundled frontend assets.
dist_dir = config.STATIC_DIR / "dist"
if not dist_dir.is_dir():
    print(f"CRITICAL ERROR: Bundled assets directory not found: {dist_dir}. Ensure frontend assets are built (e.g., 'npm run build').")
    sys.exit(1) # Exit if the distribution directory is not found.

# Mount the 'dist' directory to serve its contents under the '/dist' path.
# These are typically the compiled/minified CSS and JavaScript files.
app.mount("/dist", StaticFiles(directory=dist_dir), name="dist_assets")

# Mount the base static directory (e.g., 'app/static') to serve its contents under the '/static' path.
# This can be used for HTML files, images, or other assets not part of the frontend build process.
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static_pages")

print(f"Mounted bundled assets from '{dist_dir}' at '/dist'")
print(f"Mounted static pages directory '{config.STATIC_DIR}' at '/static'")


@app.post("/register", response_model=models.RegistrationResponse, tags=["Authentication"])
async def register_new_user(request_data: models.RegistrationRequest, request: Request):
    """
    Registers a new user, hashes their generated password, and emails it to them.
    The emailed password is their actual password for the account.
    Uses config.BASE_URL for the login link.
    """
    email = request_data.email.lower().strip()
    name = request_data.name.strip()

    if not name: # Basic validation for the name
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty.")

    conn = None # Initialize connection variable
    try:
        # Establish database connection
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # Check if email already exists
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

        # Generate a secure password for the user
        plain_password = database.generate_secure_token(12)
        hashed_password = auth.get_password_hash(plain_password)

        # Insert the new user into the database
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active) VALUES (?, ?, ?, ?)",
            (name, email, hashed_password, True)
        )
        user_id = cursor.lastrowid
        if not user_id:
            conn.rollback()
            raise sqlite3.Error("Failed to get lastrowid after user insertion.")

        # --- Construct login_page_url using config.BASE_URL ---
        # Get the URL from FastAPI's url_for for the path component
        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        
        # Parse the URL to reliably extract only the path and query parameters
        parsed_url_from_fastapi = urlparse(login_url_from_fastapi)
        login_path_component = parsed_url_from_fastapi.path # e.g., "/login"
        if parsed_url_from_fastapi.query: # Append query string if it exists
            login_path_component += "?" + parsed_url_from_fastapi.query
            
        # Use config.BASE_URL (which should not have a trailing slash)
        login_page_url = f"{config.BASE_URL.rstrip('/')}{login_path_component}"
        
        print(f"DEBUG (register): config.BASE_URL: {config.BASE_URL}")
        print(f"DEBUG (register): request.url_for('get_login_page_route') returned: {login_url_from_fastapi}")
        print(f"DEBUG (register): Extracted path component: {login_path_component}")
        print(f"DEBUG (register): Constructed login_page_url for email: {login_page_url}")
        # --- END OF URL CONSTRUCTION ---

        email_sent = await email_utils.send_registration_password_email(
            recipient_email=email,
            recipient_name=name,
            generated_password=plain_password,
            login_url=login_page_url
        )

        if not email_sent:
            conn.commit() 
            print(f"User {email} (ID: {user_id}) created, but password email FAILED to send.")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Account created, but failed to send password email. Please try resetting the password or contact support."
            )

        conn.commit()
        print(f"New user registered: {email} (ID: {user_id}). Password email sent with login URL: {login_page_url}")

        return models.RegistrationResponse(message="Account created! Your password has been sent to your email.")

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"API ERROR (/register): Database error for {email}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during registration.")
    except Exception as e:
        if conn: conn.rollback()
        print(f"API ERROR (/register): Unexpected error for {email}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred during registration.")
    finally:
        if conn: conn.close()

@app.post("/api/me/regenerate-password", response_model=models.RegeneratePasswordResponse, tags=["User Account Management"])
async def regenerate_user_password(
    request: Request, # To construct login URL for email
    response: FastAPIResponse, # To potentially clear cookies if we decide to logout from backend
    payload: models.RegeneratePasswordRequest = Body(...),
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """
    Allows an authenticated user to regenerate their password.
    Requires the user's current password for verification.
    A new password will be generated and emailed to the user.
    The client is expected to handle logout after a successful response.
    """
    user_id = current_user.get("id")
    user_email = current_user.get("email")
    user_name = current_user.get("name") # For the email

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # Fetch the current user's stored password hash for verification
        cursor.execute("SELECT password_hash FROM users WHERE id = ? AND email = ?", (user_id, user_email))
        user_record = cursor.fetchone()

        if not user_record:
            print(f"ACCOUNT ERROR (regenerate-password): User ID {user_id} / Email {user_email} not found in database despite being authenticated.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User record not found. Please try logging out and back in."
            )

        stored_password_hash = user_record["password_hash"]
        if not stored_password_hash:
             print(f"ACCOUNT ERROR (regenerate-password): User ID {user_id} ({user_email}) has no password hash stored.")
             raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cannot verify password due to an account data issue. Please contact support."
            )

        # Verify the provided current password
        if not auth.verify_password(payload.current_password, stored_password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect current password."
            )

        # If current password is correct, generate a new password
        new_plain_password = database.generate_secure_token(12) # Generate a new 12-character password
        new_hashed_password = auth.get_password_hash(new_plain_password)

        # Update the user's password hash in the database
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hashed_password, user_id))
        if cursor.rowcount == 0:
            conn.rollback()
            print(f"ACCOUNT ERROR (regenerate-password): Failed to update password hash for user ID {user_id} ({user_email}).")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update password due to a server error."
            )

        # Construct the login URL for the email using config.BASE_URL
        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        parsed_url_from_fastapi = urlparse(login_url_from_fastapi)
        login_path_component = parsed_url_from_fastapi.path
        if parsed_url_from_fastapi.query:
            login_path_component += "?" + parsed_url_from_fastapi.query
        login_page_url = f"{config.BASE_URL.rstrip('/')}{login_path_component}"

        # Send an email with the new password
        # We can reuse send_password_reset_email or create a new specific template/function
        # For now, reusing send_password_reset_email which sends the new password.
        email_sent = await email_utils.send_password_reset_email(
            recipient_email=user_email,
            recipient_name=user_name, # Pass the user's name
            new_password=new_plain_password,
            login_url=login_page_url
        )

        if not email_sent:
            # If email sending fails, we should roll back the password change
            # to avoid leaving the user in a state where their password changed
            # but they didn't receive the new one.
            conn.rollback()
            print(f"ACCOUNT ERROR (regenerate-password): Password for user {user_id} ({user_email}) was updated in DB, but FAILED to send email. DB changes rolled back.")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Password was reset, but failed to send the notification email. Please try again."
            )

        # Invalidate all existing session tokens for this user to force re-login on other devices.
        # This is an important security step after a password change.
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            """
            UPDATE auth_tokens 
            SET used_at = ?, expires_at = ? 
            WHERE user_id = ? AND token_type = 'session' AND used_at IS NULL
            """,
            (now_utc_iso, now_utc_iso, user_id)
        )
        print(f"ACCOUNT (regenerate-password): Invalidated {cursor.rowcount} active session tokens for user ID {user_id} ({user_email}).")


        conn.commit()
        print(f"ACCOUNT: User ID {user_id} ({user_email}) successfully regenerated their password. New password email sent.")
        
        # The client will handle the actual logout.
        # We do not clear the cookie here, but the session tokens are invalidated.
        # The current session cookie on the client's browser will become invalid on the next request
        # that requires auth, or the client can explicitly clear it.

        return models.RegeneratePasswordResponse(
            message="Password regenerated successfully. An email has been sent with your new password. You should now log out and log back in with the new password."
        )

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/regenerate-password): Database error for user {user_email}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error while regenerating password.")
    except Exception as e:
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/regenerate-password): Unexpected error for user {user_email}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred.")
    finally:
        if conn: conn.close()


@app.get("/settings", response_class=FileResponse, name="get_settings_page", tags=["Pages"])
async def get_settings_page(
    request: Request,
    user: Dict[str, Any] = Depends(auth.get_current_active_user) # Protect the route
):
    """
    Serves the user settings page.
    Requires authentication.
    """
    if not user:
        # This should be handled by the Depends(auth.get_current_active_user)
        # but as a safeguard, redirect to login if somehow user is None.
        return RedirectResponse(url=request.url_for('get_login_page_route'), status_code=status.HTTP_302_FOUND)

    settings_html_path = config.STATIC_DIR / "settings.html"
    if not settings_html_path.is_file():
        print(f"ERROR: settings.html not found at expected location: {settings_html_path}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settings page HTML file not found.")
    
    return FileResponse(settings_html_path)

# --- Add this new route for updating user email ---
@app.patch("/api/me/update-email", response_model=models.UpdateEmailResponse, tags=["User Account Management"])
async def update_user_email(
    response: FastAPIResponse, # To potentially clear cookies if we decide to logout from backend
    payload: models.UpdateEmailRequest = Body(...),
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """
    Allows an authenticated user to update their email address.
    Requires the user's current password for verification.
    Checks if the new email is already in use.
    If successful, invalidates existing sessions and the client is expected to handle logout.
    """
    user_id = current_user.get("id")
    current_email_for_logging = current_user.get("email") # For logging
    new_email_normalized = payload.new_email.lower().strip()

    if new_email_normalized == current_email_for_logging:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email address is the same as the current one."
        )

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # 1. Fetch the current user's stored password hash for verification
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user_record = cursor.fetchone()

        if not user_record:
            print(f"ACCOUNT ERROR (update-email): User ID {user_id} not found in database despite being authenticated.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User record not found. Please try logging out and back in."
            )

        stored_password_hash = user_record["password_hash"]
        if not stored_password_hash:
             print(f"ACCOUNT ERROR (update-email): User ID {user_id} ({current_email_for_logging}) has no password hash stored.")
             raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cannot verify password due to an account data issue. Please contact support."
            )

        # 2. Verify the provided current password
        if not auth.verify_password(payload.current_password, stored_password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect current password."
            )

        # 3. Check if the new email address is already in use by another user
        cursor.execute("SELECT id FROM users WHERE email = ? AND id != ?", (new_email_normalized, user_id))
        existing_user_with_new_email = cursor.fetchone()
        if existing_user_with_new_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email address is already in use by another account."
            )

        # 4. If password is correct and new email is available, update the email
        cursor.execute("UPDATE users SET email = ? WHERE id = ?", (new_email_normalized, user_id))
        if cursor.rowcount == 0:
            conn.rollback()
            print(f"ACCOUNT ERROR (update-email): Failed to update email for user ID {user_id} ({current_email_for_logging}) even after checks. Rowcount was 0.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update email due to a server error during the update operation."
            )

        # 5. Invalidate all existing session tokens for this user to force re-login.
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            """
            UPDATE auth_tokens 
            SET used_at = ?, expires_at = ? 
            WHERE user_id = ? AND token_type = 'session' AND used_at IS NULL
            """,
            (now_utc_iso, now_utc_iso, user_id)
        )
        print(f"ACCOUNT (update-email): Invalidated {cursor.rowcount} active session tokens for user ID {user_id} (old email: {current_email_for_logging}).")

        conn.commit()
        print(f"ACCOUNT: User ID {user_id} successfully updated email from '{current_email_for_logging}' to '{new_email_normalized}'.")
        
        # The client will handle the actual logout.
        # The current session cookie on the client's browser will become invalid on the next request
        # that requires auth, or the client can explicitly clear it.

        return models.UpdateEmailResponse(
            message="Email updated successfully. You will now be logged out to apply changes.",
            new_email=new_email_normalized
        )

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/update-email): Database error for user {current_email_for_logging}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="A database error occurred while updating your email.")
    except Exception as e:
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/update-email): Unexpected error for user {current_email_for_logging}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred while updating your email.")
    finally:
        if conn: conn.close()

# --- Add this new route for updating user name ---
@app.patch("/api/me/update-name", response_model=models.UpdateNameResponse, tags=["User Account Management"])
async def update_user_name(
    update_data: models.UpdateNameRequest = Body(...), # Use models.UpdateNameRequest
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user) # Dependency to get authenticated user
):
    """
    Allows an authenticated user to update their display name.
    Requires the user's current password for verification.
    """
    # current_user is already validated by auth.get_current_active_user,
    # which raises HTTPException(status_code=401) if not authenticated or inactive.

    user_id = current_user.get("id")
    user_email = current_user.get("email") # For logging purposes

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # Fetch the current user's stored password hash for verification
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user_record = cursor.fetchone()

        if not user_record:
            # This should ideally not happen if user is authenticated and their record exists
            print(f"ACCOUNT ERROR (update-name): User ID {user_id} not found in database despite being authenticated.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, # Or 500 if this indicates a severe inconsistency
                detail="User record not found. Please try logging out and back in."
            )

        stored_password_hash = user_record["password_hash"]
        if not stored_password_hash:
             # This indicates an issue with the user's account data (e.g., created without a password hash)
             print(f"ACCOUNT ERROR (update-name): User ID {user_id} ({user_email}) has no password hash stored.")
             raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cannot verify password due to an account data issue. Please contact support."
            )

        # Verify the provided current password against the stored hash
        if not auth.verify_password(update_data.current_password, stored_password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, # Incorrect password
                detail="Incorrect current password."
            )

        # If password is correct, update the name
        # new_name is already stripped and validated by Pydantic's constr in models.UpdateNameRequest
        new_name_stripped = update_data.new_name

        cursor.execute("UPDATE users SET name = ? WHERE id = ?", (new_name_stripped, user_id))
        if cursor.rowcount == 0:
            # This would be unusual if the user exists and password was verified,
            # might indicate a concurrent deletion or DB issue.
            conn.rollback()
            print(f"ACCOUNT ERROR (update-name): Failed to update name for user ID {user_id} ({user_email}) even after password verification. Rowcount was 0.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update name due to a server error during the update operation."
            )

        conn.commit()
        print(f"ACCOUNT: User ID {user_id} ({user_email}) successfully updated name to '{new_name_stripped}'.")
        
        # Return a success response including the new name
        return models.UpdateNameResponse(message="Name updated successfully.", new_name=new_name_stripped)

    except HTTPException as http_exc:
        # Re-raise HTTPExceptions that were intentionally raised (e.g., 401, 404)
        if conn: conn.rollback() # Ensure rollback if transaction was started
        raise http_exc
    except sqlite3.Error as db_err:
        # Handle database-specific errors
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/update-name): Database error for user {user_email}: {db_err}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="A database error occurred while updating your name."
        )
    except Exception as e:
        # Handle any other unexpected errors
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/update-name): Unexpected error for user {user_email}: {e}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected server error occurred while updating your name."
        )
    finally:
        # Always close the database connection if it was opened
        if conn:
            conn.close()

@app.post("/forgot_password", response_model=models.ForgotPasswordResponse, tags=["Authentication"])
async def handle_forgot_password(
    request_data: models.ForgotPasswordRequest,
    request: Request # To construct login URL for email and get client IP
):
    """
    Handles a forgot password request with rate limiting.
    Uses config.BASE_URL for the login link.
    Rate limiting parameters are sourced from config.py.
    """
    email = request_data.email.lower().strip()
    client_ip = request.client.host if request.client else None

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        time_window_start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=config.FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS)
        
        cursor.execute(
            "SELECT COUNT(*) FROM password_reset_attempts WHERE email = ? AND attempted_at >= ?",
            (email, time_window_start.isoformat())
        )
        attempt_count_row = cursor.fetchone()
        recent_attempts = attempt_count_row[0] if attempt_count_row else 0

        if recent_attempts >= config.FORGOT_PASSWORD_ATTEMPT_LIMIT:
            print(f"AUTH (/forgot_password): Rate limit exceeded for email {email}. Recent attempts: {recent_attempts} (Limit: {config.FORGOT_PASSWORD_ATTEMPT_LIMIT})")
            return models.ForgotPasswordResponse(message="If an account with this email exists, a password reset email has been sent.")

        cursor.execute(
            "INSERT INTO password_reset_attempts (email, ip_address) VALUES (?, ?)",
            (email, client_ip)
        )
        
        cursor.execute("SELECT id, name FROM users WHERE email = ? AND is_active = 1", (email,))
        user_row = cursor.fetchone()

        if not user_row:
            conn.commit() 
            print(f"AUTH (/forgot_password): Reset requested for non-existent/inactive email: {email}. Attempt logged.")
            return models.ForgotPasswordResponse(message="If an account with this email exists, a password reset email has been sent.")

        user = dict(user_row)
        user_id = user["id"]
        user_name = user["name"]

        new_plain_password = database.generate_secure_token(12)
        new_hashed_password = auth.get_password_hash(new_plain_password)

        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hashed_password, user_id))
        if cursor.rowcount == 0:
            conn.rollback()
            print(f"AUTH ERROR (/forgot_password): Failed to update password hash for user {user_id} ({email}).")
            return models.ForgotPasswordResponse(message="If an account with this email exists, a password reset email has been sent.")

        # --- Construct login_page_url using config.BASE_URL ---
        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        parsed_url_from_fastapi = urlparse(login_url_from_fastapi)
        login_path_component = parsed_url_from_fastapi.path
        if parsed_url_from_fastapi.query:
            login_path_component += "?" + parsed_url_from_fastapi.query
        
        # Use config.BASE_URL (which should not have a trailing slash)
        login_page_url = f"{config.BASE_URL.rstrip('/')}{login_path_component}"

        print(f"DEBUG (forgot_password): config.BASE_URL: {config.BASE_URL}")
        print(f"DEBUG (forgot_password): request.url_for('get_login_page_route') returned: {login_url_from_fastapi}")
        print(f"DEBUG (forgot_password): Extracted path component: {login_path_component}")
        print(f"DEBUG (forgot_password): Constructed login_page_url for email: {login_page_url}")
        # --- END OF URL CONSTRUCTION ---
        
        email_sent = await email_utils.send_password_reset_email(
            recipient_email=email,
            recipient_name=user_name,
            new_password=new_plain_password,
            login_url=login_page_url
        )

        if not email_sent:
            conn.rollback()
            print(f"AUTH ERROR (/forgot_password): Password reset email FAILED to send for user {user_id} ({email}). DB changes rolled back.")
            return models.ForgotPasswordResponse(message="If an account with this email exists, a password reset email has been sent.")

        conn.commit()
        print(f"AUTH (/forgot_password): Password reset successful for user {user_id} ({email}). New password email sent. Attempt logged.")
        return models.ForgotPasswordResponse(message="If an account with this email exists, a password reset email has been sent.")

    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"API ERROR (/forgot_password): DB error for {email}: {db_err}")
        traceback.print_exc()
        return models.ForgotPasswordResponse(message="An error occurred while processing your request. Please try again.")
    except Exception as e:
        if conn: conn.rollback()
        print(f"API ERROR (/forgot_password): Unexpected error for {email}: {e}")
        traceback.print_exc()
        return models.ForgotPasswordResponse(message="An unexpected error occurred. Please try again.")
    finally:
        if conn: conn.close()


@app.on_event("startup")
async def startup_event():
    print("Application startup: Initializing database...")
    database.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    database.init_db()
    print("Database initialization check complete.")
    if docker_utils.get_docker_client() is None:
        print("WARNING: Docker client unavailable during startup.")
    else:
        print("Docker client confirmed available at startup.")
    try:
        llm.get_model()
        print("LLM model connection checked successfully.")
    except Exception as e:
        print(f"CRITICAL ERROR during startup LLM check: {e}")


# --- Static Files Setup ---
if not config.STATIC_DIR or not config.STATIC_DIR.is_dir():
    sys.exit(f"CRITICAL ERROR: Base static directory invalid: {config.STATIC_DIR}")
dist_dir = config.STATIC_DIR / "dist"
if not dist_dir.is_dir():
    sys.exit(f"CRITICAL ERROR: Bundled assets dir not found: {dist_dir}. Run 'npm run build'.")
app.mount("/dist", StaticFiles(directory=dist_dir), name="dist_assets")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static_pages")
print(f"Mounted bundled assets from '{dist_dir}' at '/dist'")
print(f"Mounted static pages directory '{config.STATIC_DIR}' at '/static'")


# --- Authentication & User Routes ---

@app.post("/token", response_model=models.Token, tags=["Authentication"])
async def login_for_access_token(
    response: FastAPIResponse,
    form_data: OAuth2PasswordRequestForm = Depends()
):
    """Handles email/password login, sets session cookie."""
    email = form_data.username.lower().strip()
    password = form_data.password
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, email, password_hash, is_active FROM users WHERE email = ?", (email,))
        user_row = cursor.fetchone()
        if not user_row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")

        user = dict(user_row)
        stored_password_hash = user.get("password_hash")
        if not stored_password_hash or not auth.verify_password(password, stored_password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")

        if not user["is_active"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")

        session_token_raw = await auth.create_user_session(response=response, user_id=user["id"])
        print(f"AUTH (/token): User '{email}' (ID: {user['id']}) logged in successfully.")
        return models.Token(
            access_token=session_token_raw, token_type="bearer",
            user_id=user["id"], user_name=user["name"], user_email=user["email"]
        )
    except HTTPException as http_exc: raise http_exc
    except sqlite3.Error as db_err:
        print(f"API ERROR (/token): DB error for {email}: {db_err}"); traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during login.")
    except Exception as e:
        print(f"API ERROR (/token): Unexpected error for {email}: {e}"); traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected server error during login.")
    finally:
        if conn: conn.close()


@app.post("/check_email", response_model=models.EmailCheckResponse, tags=["Authentication"])
async def check_email_exists(request_data: models.EmailCheckRequest):
    """Checks if an email exists in the database."""
    email_to_check = request_data.email.lower()
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM users WHERE email = ?", (email_to_check,))
        user_row = cursor.fetchone()
        if user_row:
            return models.EmailCheckResponse(exists=True, user_name=user_row["name"])
        else:
            return models.EmailCheckResponse(exists=False)
    except sqlite3.Error as db_err:
        print(f"API ERROR (/check_email): DB error for {email_to_check}: {db_err}"); traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error checking email.")
    except Exception as e:
        print(f"API ERROR (/check_email): Unexpected error for {email_to_check}: {e}"); traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected server error checking email.")
    finally:
        if conn: conn.close()


@app.post("/register", response_model=models.RegistrationResponse, tags=["Authentication"])
async def register_new_user(request_data: models.RegistrationRequest, request: Request):
    """Registers a new user, hashes password, emails generated password."""
    email = request_data.email.lower().strip()
    name = request_data.name.strip()
    if not name: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty.")
    conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone(): raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

        plain_password = database.generate_secure_token(12)
        hashed_password = auth.get_password_hash(plain_password) # Use passlib

        cursor.execute("INSERT INTO users (name, email, password_hash, is_active) VALUES (?, ?, ?, ?)", (name, email, hashed_password, True))
        user_id = cursor.lastrowid
        if not user_id: conn.rollback(); raise sqlite3.Error("Failed to get lastrowid.")

        base_url = str(request.base_url).rstrip('/'); login_page_url = f"{base_url}{request.url_for('get_login_page_route')}"
        email_sent = await email_utils.send_registration_password_email(
            recipient_email=email, recipient_name=name, generated_password=plain_password, login_url=login_page_url
        )
        if not email_sent:
            conn.commit(); print(f"User {email} created (ID: {user_id}), but password email FAILED.")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account created, but failed to send password email.")

        conn.commit(); print(f"New user registered: {email} (ID: {user_id}). Password email sent.")
        return models.RegistrationResponse(message="Account created! Temporary password sent to your email.")
    except HTTPException as http_exc:
        if conn: conn.rollback(); raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback(); print(f"API ERROR (/register): DB error for {email}: {db_err}"); traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during registration.")
    except Exception as e:
        if conn: conn.rollback(); print(f"API ERROR (/register): Unexpected error for {email}: {e}"); traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected server error during registration.")
    finally:
        if conn: conn.close()


@app.get("/login", response_class=HTMLResponse, name="get_login_page_route", tags=["Pages"])
async def get_login_page_route(request: Request, user: Optional[Dict] = Depends(auth.get_current_user)):
    """Serves the login page or redirects if already logged in."""
    if user:
        return RedirectResponse(url=request.url_for("get_session_choice_page"), status_code=status.HTTP_302_FOUND)
    login_html_path = config.STATIC_DIR / "login.html"
    if not login_html_path.is_file(): raise HTTPException(status_code=404, detail="login.html not found.")
    return FileResponse(login_html_path)


@app.get("/logout", tags=["Authentication"])
async def logout_route(response: FastAPIResponse, session_token_value: Optional[str] = Depends(auth.cookie_scheme)):
    """Logs the user out by invalidating the session and clearing the cookie."""
    await auth.logout_user(response, session_token_value)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@app.get("/api/me", response_model=models.UserResponseModel, tags=["Users"])
async def get_current_user_details(user: Dict[str, Any] = Depends(auth.get_current_active_user)):
    """Gets details for the currently authenticated user."""
    if not user: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not all(key in user for key in ["id", "name", "email"]):
        print(f"ERROR /api/me: User dict missing keys. User: {user}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User data incomplete.")
    return models.UserResponseModel(id=user["id"], name=user["name"], email=user["email"])

# --- Main Application Routes (Protected) ---

@app.get("/", response_class=HTMLResponse, name="get_session_choice_page", tags=["Pages"])
async def get_session_choice_page(request: Request, user: Optional[Dict] = Depends(auth.get_current_active_user)):
    """Serves the session choice/dashboard page."""
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    session_choice_html_path = config.STATIC_DIR / "session-choice.html"
    if not session_choice_html_path.is_file():
        raise HTTPException(status_code=404, detail="session-choice.html not found.")
    try:
        with open(session_choice_html_path, "r", encoding="utf-8") as f: html_content = f.read()
        # Consider passing user data via context if using a template engine
        # For simple replacement:
        html_content = html_content.replace("[User Name]", user.get("name", "User"))
        return HTMLResponse(content=html_content)
    except Exception as e:
        print(f"Error reading/serving session-choice.html: {e}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error loading session choice page.")


@app.post("/sessions/create", status_code=status.HTTP_303_SEE_OTHER, tags=["Sessions"]) # Use 303 for POST-redirect-GET
async def create_new_session_route(
    request: Request,
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """Creates a new chat session and redirects to it."""
    if not user: # Should be handled by Depends, but belt-and-suspenders
        raise HTTPException(status_code=401, detail="Not authenticated")

    new_session_id = str(uuid.uuid4())
    host_user_id = user["id"]
    conn = None
    default_session_name = ""

    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # Insert session first
        cursor.execute(
            """INSERT INTO sessions (id, host_user_id, name, is_active) VALUES (?, ?, ?, ?)""",
            (new_session_id, host_user_id, None, True)
        )

        # Get creation timestamp to generate default name
        cursor.execute("SELECT created_at FROM sessions WHERE id = ?", (new_session_id,))
        session_row = cursor.fetchone()

        if not session_row or not session_row["created_at"]:
            default_session_name = f"Session ({new_session_id[:4]})"
            print(f"WARNING: Could not fetch created_at for session {new_session_id}. Using fallback name.")
        else:
            try:
                # Attempt to parse ISO format timestamp (adjust if your DB stores differently)
                created_at_str = session_row["created_at"].replace('Z', '+00:00') # Handle Z for UTC
                created_dt = datetime.datetime.fromisoformat(created_at_str)
                # Format timestamp for default name (adjust format as desired)
                default_session_name = created_dt.strftime("%b %d, %Y %I:%M %p")
            except (ValueError, TypeError) as fmt_err:
                print(f"WARNING: Error formatting timestamp '{session_row['created_at']}': {fmt_err}. Using fallback name.")
                default_session_name = f"Session ({new_session_id[:4]})"

        # Update session name with the generated default
        cursor.execute("UPDATE sessions SET name = ? WHERE id = ?", (default_session_name, new_session_id))

        # Add creator as participant
        cursor.execute(
            "INSERT INTO session_participants (session_id, user_id) VALUES (?, ?)",
            (new_session_id, host_user_id)
        )

        conn.commit()
        print(f"New session created: ID {new_session_id}, Name: '{default_session_name}', Hosted by User ID {host_user_id}")

    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"Database error creating session: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Could not create new session due to a database error.")
    except Exception as e:
        if conn: conn.rollback()
        print(f"Unexpected error creating session: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Could not create new session due to a server error.")
    finally:
        if conn: conn.close()

    chat_url = request.url_for("get_chat_page_for_session", session_id=new_session_id)
    # Use 303 See Other for redirect after POST
    return RedirectResponse(url=str(chat_url), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/chat/{session_id}", response_class=HTMLResponse, name="get_chat_page_for_session", tags=["Pages"])
async def get_chat_page_for_session(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the chat session to load."),
    user: Optional[Dict] = Depends(auth.get_current_active_user)
):
    """Serves the chat page for a specific session after verifying access."""
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    user_id = user['id']
    conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        # Verify session exists and is active
        cursor.execute("SELECT id, name FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        session_row = cursor.fetchone()
        if not session_row:
            raise HTTPException(status_code=404, detail="Chat session not found or is inactive.")
        # Verify user is a participant
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="You do not have access to this chat session.")

        # Update last_accessed_at for the session
        current_time_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute("UPDATE sessions SET last_accessed_at = ? WHERE id = ?", (current_time_utc_iso, session_id))
        conn.commit()

        print(f"User {user['email']} accessing chat for session: {session_id}. Last accessed updated.")
    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except Exception as e:
        if conn: conn.rollback()
        print(f"Error verifying session access or updating last_accessed_at: {e}"); traceback.print_exc();
        raise HTTPException(status_code=500, detail="Error verifying session access.")
    finally:
        if conn: conn.close()

    chat_html_path = config.STATIC_DIR / "chat-session.html"
    if not chat_html_path.is_file():
        raise HTTPException(status_code=404, detail="Chat interface file not found.")
    # Consider passing session_name or user_name to the template if needed
    return FileResponse(chat_html_path)


async def handle_chat_message(
    chain: Any,                     # Your LangChain chain
    memory: Any,                    # The conversation memory object
    websocket: WebSocket,
    client_js_id: str,              # Unique ID for the JS client instance
    current_user: Dict[str, Any],   # Authenticated user details
    session_id: str,                # The ID of the chat session
    user_input: str,
    turn_id: int                    # <<< NEW: The turn ID from the frontend
):
    """
    Handles processing a chat message with the LLM, streaming the response,
    and allowing the stream to be stopped.
    """
    user_name = current_user.get('name', 'User')
    user_db_id = current_user['id']
    full_response = ""
    thinking_content = None # Placeholder for future
    
    # --- MODIFIED: Create a unique stream ID and register for stopping ---
    stream_id = f"{client_js_id}_{turn_id}" # Use client_js_id and frontend's turn_id
    stop_event: asyncio.Event = None # Initialize to None

    db_conn_user_msg = None
    try:
        # Save user message to DB (existing logic)
        db_conn_user_msg = database.get_db_connection()
        db_cursor_user_msg = db_conn_user_msg.cursor()
        db_cursor_user_msg.execute(
            """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, client_id_temp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, user_db_id, user_name, 'user', user_input, client_js_id)
        )
        db_conn_user_msg.commit()
    except Exception as db_err:
        print(f"DB ERROR saving user message for session {session_id} (client {client_js_id}, turn {turn_id}): {db_err}")
        if db_conn_user_msg: db_conn_user_msg.rollback()
    finally:
        if db_conn_user_msg: db_conn_user_msg.close()

    try:
        # --- MODIFIED: Register stream and get stop_event ---
        stop_event = await state.register_ai_stream(stream_id)
        print(f"AI STREAM (handle_chat_message): Starting stream {stream_id} for session {session_id}")

        # Stream response using the chain
        async for chunk_data in chain.astream({"input": user_input}):
            # --- MODIFIED: Check stop_event in the loop ---
            if stop_event.is_set():
                print(f"AI STREAM (handle_chat_message): Stop event set for stream {stream_id}. Breaking loop.")
                break # Exit the streaming loop if stop is signaled

            # Extract content (your existing logic for handling chunk_data)
            if isinstance(chunk_data, dict):
                chunk_str = chunk_data.get("answer", "") # Or your relevant key
            else:
                chunk_str = str(chunk_data)

            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WS: WebSocket disconnected during LLM stream for session {session_id}, stream {stream_id}. Aborting send.")
                return 
            await websocket.send_text(chunk_str)
            full_response += chunk_str
        
        # --- MODIFIED: Check if loop was broken by stop_event before sending EOS ---
        if stop_event.is_set():
            print(f"AI STREAM (handle_chat_message): Stream {stream_id} was stopped by signal. Not sending EOS from here, frontend will finalize.")
            # Frontend's finalizeTurnOnErrorOrClose will handle UI. Backend might send EOS in stop_ai_stream handler.
        elif websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text("<EOS>") # Send End-Of-Stream marker if finished naturally
            print(f"AI STREAM (handle_chat_message): Finished streaming naturally for stream {stream_id} to session {session_id}")
        
        # Save context to memory and DB (existing logic)
        memory.save_context({"input": user_input}, {"output": full_response})
        if hasattr(state, 'save_memory_state_to_db'): # Check if function exists
            try:
                state.save_memory_state_to_db(session_id, memory)
            except Exception as save_mem_err:
                print(f"ERROR saving memory state to DB for session {session_id}: {save_mem_err}")
        
        db_conn_ai_msg = None
        try:
            db_conn_ai_msg = database.get_db_connection()
            db_cursor_ai_msg = db_conn_ai_msg.cursor()
            db_cursor_ai_msg.execute(
                """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, thinking_content, client_id_temp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, None, "AI", 'ai', full_response, thinking_content, client_js_id)
            )
            db_conn_ai_msg.commit()
        except Exception as db_err:
            print(f"DB ERROR saving AI message for session {session_id} (stream {stream_id}): {db_err}")
            if db_conn_ai_msg: db_conn_ai_msg.rollback()
        finally:
            if db_conn_ai_msg: db_conn_ai_msg.close()

    except Exception as chain_exc:
        error_msg = f"<ERROR>LLM Error: Processing your message failed. Details: {chain_exc}"
        print(f"LLM chain error for session {session_id} (stream {stream_id}): {chain_exc}")
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(error_msg)
                # Also send EOS after error to ensure frontend resets UI properly
                await websocket.send_text("<EOS>")
            except Exception as send_err:
                print(f"WS ERROR: Could not send LLM error/EOS to client {client_js_id} for stream {stream_id}: {send_err}")
    finally:
        # --- MODIFIED: Always unregister the stream ---
        if stream_id and stop_event: # Ensure stream_id was formed and event exists
            await state.unregister_ai_stream(stream_id)
            print(f"AI STREAM (handle_chat_message): Stream {stream_id} unregistered for session {session_id}")


@app.websocket("/ws/{session_id_ws}/{client_js_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id_ws: str = FastApiPath(..., title="Session ID", description="The ID of the chat session."),
    client_js_id: str = FastApiPath(..., title="Client JS ID", description="A unique ID generated by the client-side JavaScript.")
):
    """
    Handles WebSocket connections for chat, code execution, and AI stream stopping.
    """
    # Authenticate user via session cookie
    session_token_from_cookie = websocket.cookies.get(auth.SESSION_COOKIE_NAME)
    current_ws_user: Optional[Dict[str, Any]] = None
    if session_token_from_cookie:
        current_ws_user = await auth.get_user_by_session_token(session_token_from_cookie)

    ws_log_prefix_unauth = f"WS ({websocket.client.host}:{websocket.client.port}) session {session_id_ws}, client {client_js_id}:"
    if not current_ws_user:
        print(f"{ws_log_prefix_unauth} Authentication failed. Closing WebSocket.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = current_ws_user['id']
    user_email = current_ws_user.get('email', f'UserID_{user_id}')
    ws_log_prefix = f"WS (User: {user_email}, Session: {session_id_ws}, ClientJS: {client_js_id}):"
    print(f"{ws_log_prefix} User authenticated.")

    # Verify session existence and user participation (existing logic)
    conn_verify = None
    is_participant = False
    try:
        conn_verify = database.get_db_connection()
        cursor_verify = conn_verify.cursor()
        cursor_verify.execute("SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id_ws,))
        if not cursor_verify.fetchone():
            print(f"{ws_log_prefix} Session not found or inactive. Closing.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        cursor_verify.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id_ws, user_id))
        is_participant = cursor_verify.fetchone() is not None
        if not is_participant:
            print(f"{ws_log_prefix} User NOT participant. Closing.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception as e:
        print(f"{ws_log_prefix} DB error verifying participation: {e}")
        traceback.print_exc()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return
    finally:
        if conn_verify: conn_verify.close()
    print(f"{ws_log_prefix} User confirmed participant.")

    try:
        await websocket.accept()
        print(f"{ws_log_prefix} WebSocket connection accepted.")
    except Exception as accept_err:
        print(f"{ws_log_prefix} Error accepting WebSocket: {accept_err}")
        return

    memory = state.get_memory_for_client(session_id_ws)
    def load_memory_for_current_session(_ignored_input_map=None):
        return memory.load_memory_variables({}).get("history", [])
    
    chain: Any
    try:
        chain = llm.create_chain(load_memory_for_current_session)
        print(f"{ws_log_prefix} LLM chain created.")
    except Exception as chain_init_error:
        print(f"{ws_log_prefix} ERROR creating LCEL chain: {chain_init_error}")
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({"type": "error", "payload": {"message": "Server error: Could not initialize chat."}})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"{ws_log_prefix} WebSocket no longer connected. Breaking loop.")
                break

            received_data = await websocket.receive_text()
            
            try:
                message_data = json.loads(received_data)
                message_type = message_data.get("type")
                payload = message_data.get("payload")

                if message_type == "chat_message" and payload: # Expect chat messages in this format
                    user_input = payload.get("user_input")
                    turn_id = payload.get("turn_id") # Get turn_id from payload
                    if user_input is not None and turn_id is not None:
                        print(f"{ws_log_prefix} Received 'chat_message' for turn {turn_id}.")
                        # Create a task for handle_chat_message so it doesn't block the receive loop
                        asyncio.create_task(
                            handle_chat_message(chain, memory, websocket, client_js_id, current_ws_user, session_id_ws, user_input, turn_id)
                        )
                    else:
                        print(f"{ws_log_prefix} Invalid 'chat_message' payload: {payload}")
                        if websocket.client_state == WebSocketState.CONNECTED:
                             await websocket.send_text("<ERROR>Invalid chat message payload from client.")
                             await websocket.send_text("<EOS>")


                elif message_type == "run_code" and payload and payload.get("code_block_id"):
                    language = payload.get("language")
                    code = payload.get("code")
                    code_block_id = payload.get("code_block_id")
                    print(f"{ws_log_prefix} Received 'run_code' for block {code_block_id}.")
                    if language and code is not None:
                        asyncio.create_task(docker_utils.run_code_in_docker_stream(websocket, client_js_id, code_block_id, language, code))
                    else:
                        print(f"{ws_log_prefix} Invalid 'run_code' payload for block {code_block_id}.")
                        if websocket.client_state == WebSocketState.CONNECTED: # Check connection before sending
                            await websocket.send_json({"type": "code_finished", "payload": {"code_block_id": code_block_id, "exit_code": -1, "error": "Invalid run_code payload."}})
                
                elif message_type == "stop_code" and payload and payload.get("code_block_id"):
                    code_block_id = payload.get("code_block_id")
                    print(f"{ws_log_prefix} Received 'stop_code' for block {code_block_id}.")
                    asyncio.create_task(docker_utils.stop_docker_container(code_block_id))

                # --- ADDED: Handler for stop_ai_stream ---
                elif message_type == "stop_ai_stream" and payload:
                    stop_client_id = payload.get("client_id") # This should match client_js_id
                    stop_session_id = payload.get("session_id") # This should match session_id_ws
                    stop_turn_id = payload.get("turn_id")

                    if stop_client_id == client_js_id and stop_session_id == session_id_ws and stop_turn_id is not None:
                        stream_id_to_stop = f"{stop_client_id}_{stop_turn_id}"
                        print(f"{ws_log_prefix} Received 'stop_ai_stream' for stream ID: {stream_id_to_stop}")
                        stopped = await state.signal_stop_ai_stream(stream_id_to_stop)
                        if stopped:
                            print(f"{ws_log_prefix} Successfully signaled stop for stream {stream_id_to_stop}.")
                            # The stream itself in handle_chat_message will break and not send further chunks.
                            # The frontend's finalizeTurnOnErrorOrClose already handles UI reset.
                            # Optionally, send an explicit <EOS> here if the stream might not have sent one
                            # due to abrupt stop, though frontend finalization should cover it.
                            # if websocket.client_state == WebSocketState.CONNECTED:
                            #     await websocket.send_text("<EOS>") # Consider if this is needed or causes double EOS
                        else:
                            print(f"{ws_log_prefix} Failed to signal stop or stream {stream_id_to_stop} not found.")
                    else:
                        print(f"{ws_log_prefix} Received 'stop_ai_stream' with mismatched IDs or missing turn_id: {payload}")
                # --- END OF ADDED ---
                
                else:
                    print(f"{ws_log_prefix} Received unknown JSON command type '{message_type}' or invalid payload.")
                    # Optionally send an error back to the client
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_text(f"<ERROR>Unknown command type: {message_type}")
                        await websocket.send_text("<EOS>")


            except json.JSONDecodeError:
                # Handle plain text messages (old format, or if client sends non-JSON by mistake)
                # For simplicity, we might decide to phase out plain text and require JSON for chat.
                # If supporting plain text, you'd call handle_chat_message without a turn_id or generate one.
                print(f"{ws_log_prefix} Received non-JSON data, treating as legacy chat or error: {received_data[:100]}...")
                # For now, let's assume chat messages should be JSON. Send an error.
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text("<ERROR>Invalid message format. Expected JSON.")
                    await websocket.send_text("<EOS>")
            
            except Exception as handler_exc:
                print(f"{ws_log_prefix} ERROR handling received message: {handler_exc}")
                traceback.print_exc()
                if websocket.client_state == WebSocketState.CONNECTED:
                    try:
                        await websocket.send_text(f"<ERROR>Server error processing your request: {handler_exc}")
                        await websocket.send_text("<EOS>") # Ensure UI resets
                    except Exception as send_err_inner:
                         print(f"{ws_log_prefix} ERROR sending error to client: {send_err_inner}")


    except WebSocketDisconnect:
        print(f"{ws_log_prefix} WebSocket disconnected by client.")
    except Exception as e:
        print(f"{ws_log_prefix} ERROR in WebSocket main loop: {e}")
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            try: await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            except Exception: pass
    finally:
        print(f"{ws_log_prefix} Cleaning up resources...")
        # state.remove_memory_for_client(session_id_ws) # Memory is persisted per session, not removed on disconnect
        await docker_utils.cleanup_client_containers(client_js_id) # Cleanup Docker containers for this JS client instance
        
        # Ensure any AI streams specifically associated with this client_js_id are cleaned up
        # This is a more complex cleanup if multiple turns could be "active" for stopping.
        # For now, unregister_ai_stream is called in handle_chat_message's finally block.
        # A more robust cleanup might iterate through state.active_ai_streams if keys include client_js_id.

        if websocket.client_state == WebSocketState.CONNECTED:
            try: await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            except Exception as final_close_err: print(f"{ws_log_prefix} Error during final WebSocket close: {final_close_err}")
        print(f"{ws_log_prefix} Cleanup complete.")

# --- API Routes for Sessions & Messages ---

@app.patch("/api/sessions/{session_id}", response_model=Dict[str, Any], tags=["Sessions"])
async def rename_session(
    session_id: str = FastApiPath(..., description="The ID of the session to rename."),
    update_data: models.SessionUpdateRequest = Body(...),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """Updates the name of a specific chat session."""
    if not user: raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = user['id']; new_name = update_data.name.strip()
    if not new_name: raise HTTPException(status_code=400, detail="Session name cannot be empty.")
    conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        # Verify user is participant in the active session
        cursor.execute(
            """SELECT s.id FROM sessions s JOIN session_participants sp ON s.id = sp.session_id
               WHERE s.id = ? AND sp.user_id = ? AND s.is_active = 1""",
            (session_id, user_id)
        )
        if not cursor.fetchone(): raise HTTPException(status_code=404, detail="Session not found or user lacks permission.")
        # Update the name
        cursor.execute("UPDATE sessions SET name = ? WHERE id = ?", (new_name, session_id))
        if cursor.rowcount == 0:
            conn.rollback(); raise HTTPException(status_code=404, detail="Session not found during update.")
        conn.commit()
        print(f"API: Renamed session {session_id} to '{new_name}' for user ID {user_id}")
        return {"id": session_id, "name": new_name, "message": "Session renamed successfully"}
    except sqlite3.Error as db_err:
        if conn: conn.rollback(); print(f"API ERROR (/api/sessions/{session_id} PATCH): DB error: {db_err}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Database error renaming session.")
    except HTTPException as http_exc: raise http_exc
    except Exception as e:
        if conn: conn.rollback(); print(f"API ERROR (/api/sessions/{session_id} PATCH): Unexpected error: {e}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Server error renaming session.")
    finally:
        if conn: conn.close()


@app.get("/api/sessions", response_model=List[Dict[str, Any]], tags=["Sessions"])
async def get_user_sessions(
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """Fetches sessions the current user participates in, ordered by last active."""
    if not user: raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = user['id']; sessions_list = []
    conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        cursor.execute(
            """SELECT s.id, s.name, s.last_accessed_at AS last_active
               FROM sessions s JOIN session_participants sp ON s.id = sp.session_id
               WHERE sp.user_id = ? AND s.is_active = 1
               ORDER BY s.last_accessed_at DESC""",
            (user_id,)
        )
        rows = cursor.fetchall()
        for row in rows: sessions_list.append(dict(row))
        # print(f"API: Fetched {len(sessions_list)} sessions for user ID {user_id}") # Verbose
        return sessions_list
    except sqlite3.Error as db_err:
        print(f"API ERROR (/api/sessions): DB error for user {user_id}: {db_err}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Database error fetching sessions.")
    except Exception as e:
        print(f"API ERROR (/api/sessions): Unexpected error for user {user_id}: {e}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Server error fetching sessions.")
    finally:
        if conn: conn.close()


@app.get("/api/sessions/{session_id}/messages", response_model=List[Dict[str, Any]], tags=["Messages"])
async def get_chat_messages_for_session(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch messages for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """Fetches all messages for a session the user participates in."""
    if not user: raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = user['id']; messages_list = []
    conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        # Verify session exists and user is participant
        cursor.execute("SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        if not cursor.fetchone(): raise HTTPException(status_code=404, detail="Session not found or inactive.")
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone(): raise HTTPException(status_code=403, detail="Access denied to this session.")
        # Fetch messages
        cursor.execute(
            """SELECT id, session_id, user_id, sender_name, sender_type, content, client_id_temp, thinking_content, timestamp
               FROM chat_messages WHERE session_id = ? ORDER BY timestamp ASC""",
            (session_id,)
        )
        rows = cursor.fetchall()
        for row in rows: messages_list.append(dict(row))
        # print(f"API: Fetched {len(messages_list)} messages for session {session_id} for user ID {user_id}") # Verbose
        return messages_list
    except sqlite3.Error as db_err:
        print(f"API ERROR (/api/sessions/.../messages): DB error for user {user_id}, session {session_id}: {db_err}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Database error fetching messages.")
    except HTTPException as http_exc: raise http_exc
    except Exception as e:
        print(f"API ERROR (/api/sessions/.../messages): Unexpected error for user {user_id}, session {session_id}: {e}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Server error fetching messages.")
    finally:
        if conn: conn.close()


@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Sessions"])
async def delete_session_route(
    session_id: str = FastApiPath(..., description="The ID of the session to delete."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """Deletes a session if the user is the host."""
    if not user: raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = user['id']; conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        # Verify session exists, is active, and user is the host
        cursor.execute("SELECT host_user_id FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        session_row = cursor.fetchone()
        if not session_row: raise HTTPException(status_code=404, detail="Session not found or inactive.")
        if session_row["host_user_id"] != user_id: raise HTTPException(status_code=403, detail="Only the session host can delete it.")
        # Delete the session (cascades should handle participants, messages, memory)
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        if cursor.rowcount == 0:
            conn.rollback(); raise HTTPException(status_code=404, detail="Session found but could not be deleted.")
        conn.commit()
        print(f"API: Deleted session {session_id} by host user ID {user_id}")
        return # Return None for 204 No Content status
    except sqlite3.Error as db_err:
        if conn: conn.rollback(); print(f"API ERROR (DELETE /api/sessions/{session_id}): DB error: {db_err}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Database error deleting session.")
    except HTTPException as http_exc:
        if conn: conn.rollback() # Rollback on permission errors etc. if transaction started
        raise http_exc
    except Exception as e:
        if conn: conn.rollback(); print(f"API ERROR (DELETE /api/sessions/{session_id}): Unexpected error: {e}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Server error deleting session.")
    finally:
        if conn: conn.close()


def start_server():
    """Configures and starts the Uvicorn server, attempting to open the browser."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8001))
    url = f"http://{host}:{port}"

    print("-" * 30)
    print(f"Tesseracs Chat Server Starting...")
    print(f"Access via: {url}/login")
    print(f"Using Config: Model='{config.MODEL_ID}', Ollama='{config.OLLAMA_BASE_URL}'")
    # Make sure dist_dir is defined or accessible if needed here, or remove this print
    # print(f"Static files from: {config.STATIC_DIR}, Bundles from: {dist_dir}") # dist_dir might not be in scope here
    print("-" * 30)

    # --- UNCOMMENTED THIS SECTION ---
    try:
        # Attempt to open the login page in the default web browser
        print(f"Attempting to open {url}/login in default browser...")
        webbrowser.open(f"{url}/login")
    except Exception as browser_err:
        # Log a warning if opening the browser fails, but continue starting the server
        print(f"Warning: Could not automatically open browser: {browser_err}")
    # --- END UNCOMMENTED SECTION ---

    # Start the Uvicorn server
    # reload=True is useful for development, consider removing for production
    uvicorn.run("app.main:app", host=host, port=port, log_level="info", reload=True)

if __name__ == "__main__":
    start_server()
