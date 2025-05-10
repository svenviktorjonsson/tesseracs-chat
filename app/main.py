
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
from typing import Optional, Dict, Any, List, Optional # For type hinting

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
from . import encryption_utils

# --- FastAPI App Initialization ---
app = FastAPI(title="Tesseracs Chat")

@app.on_event("startup") # This decorator should already be above your existing startup_event
async def startup_event():
    """
    Performs initialization tasks when the application starts:
    - Ensures the database directory exists.
    - Initializes the database schema.
    - Checks Docker client availability.
    - Checks connection to the default LLM model.
    """
    print("Application startup: Initializing database...")
    database.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    database.init_db()
    print("Database initialization check complete.")

    # Check if the Docker client is available.
    # This uses your existing docker_utils.get_docker_client()
    docker_client = None
    try:
        docker_client = docker_utils.get_docker_client()
        if docker_client is None:
            print("WARNING: Could not connect to Docker daemon via get_docker_client(). Code execution features will be disabled.")
        else:
            print("Docker client confirmed available at startup.")
    except Exception as e:
        # Catching a broader exception here if get_docker_client() itself fails
        print(f"WARNING: Could not connect to Docker daemon: {e}")
        print("Code execution via Docker will be unavailable.")


    # Attempt to connect to the default LLM.
    try:
        print(f"Attempting to initialize default LLM: Provider='{config.DEFAULT_LLM_PROVIDER_ID}', Model='{config.DEFAULT_LLM_MODEL_ID}'")
        # Corrected call to llm.get_model with required arguments
        default_model_instance = llm.get_model(
            provider_id=config.DEFAULT_LLM_PROVIDER_ID,
            model_id=config.DEFAULT_LLM_MODEL_ID
            # api_key and base_url_override are not typically needed for the default system check,
            # as llm.get_model will use ENV vars or provider defaults.
        )
        if default_model_instance:
            print("Default LLM model connection checked successfully.")
        else:
            # This branch will be hit if llm.get_model returns None (e.g., config issue, Ollama not running for default)
            print(f"CRITICAL WARNING: Could not initialize default LLM (Provider: {config.DEFAULT_LLM_PROVIDER_ID}, Model: {config.DEFAULT_LLM_MODEL_ID}). Check LLM config and server.")
    except Exception as e:
        # This catches errors during the llm.get_model call itself
        print(f"CRITICAL ERROR during startup default LLM check: {e}")
        # import traceback # Optional for more detail
        # traceback.print_exc()
if not config.STATIC_DIR or not config.STATIC_DIR.is_dir():
    print(f"CRITICAL ERROR: Base static directory invalid or not found: {config.STATIC_DIR}")
    sys.exit(1)
dist_dir = config.STATIC_DIR / "dist"
if not dist_dir.is_dir():
    print(f"CRITICAL ERROR: Bundled assets directory not found: {dist_dir}. Ensure frontend assets are built (e.g., 'npm run build').")
    sys.exit(1)
app.mount("/dist", StaticFiles(directory=dist_dir), name="dist_assets")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static_pages")
print(f"Mounted bundled assets from '{dist_dir}' at '/dist'")
print(f"Mounted static pages directory '{config.STATIC_DIR}' at '/static'")


@app.get("/api/llm/providers", response_model=List[models.LLMProviderDetail], tags=["LLM Configuration"])
async def list_llm_providers(
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """
    Lists all configured LLM providers and their available models,
    indicating if they are configured at the system level (e.g., API key in .env).
    """
    response_providers = []
    for provider_id, provider_details_from_config_root in config.LLM_PROVIDERS.items():
        provider_runtime_config = config.get_provider_config(provider_id)
        if not provider_runtime_config:
            print(f"WARNING: Provider ID '{provider_id}' found in LLM_PROVIDERS but get_provider_config returned None.")
            continue

        requires_api_key = bool(provider_runtime_config.get("api_key_env_var_name"))
        is_system_key_set = False
        if requires_api_key:
            api_key_env_name = provider_runtime_config.get("api_key_env_var_name")
            if api_key_env_name and os.getenv(api_key_env_name):
                is_system_key_set = True
        
        is_system_configured = (not requires_api_key) or (requires_api_key and is_system_key_set)
        needs_api_key_config_for_user = requires_api_key # True if provider type needs a key, user might need to input one

        available_models_details = []
        for model_info in provider_details_from_config_root.get("available_models", []):
            available_models_details.append(
                models.LLMAvailableModel(
                    model_id=model_info.get("model_id"),
                    display_name=model_info.get("display_name"),
                    context_window=model_info.get("context_window")
                )
            )

        response_providers.append(
            models.LLMProviderDetail(
                id=provider_id,
                display_name=provider_details_from_config_root.get("display_name", provider_id),
                type=provider_runtime_config.get("type", "unknown"),
                is_system_configured=is_system_configured,
                requires_api_key=requires_api_key,
                needs_api_key_config_for_user=needs_api_key_config_for_user,
                available_models=available_models_details,
            )
        )
    return response_providers

@app.get("/api/me/llm-settings", response_model=models.UserLLMSettingsResponse, tags=["User Account Management", "LLM Configuration"])
async def get_user_llm_settings(
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """
    Retrieves the current authenticated user's LLM settings.
    """
    user_id = current_user["id"]
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?",
            (user_id,)
        )
        user_settings_row = cursor.fetchone()

        if not user_settings_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User settings not found.")

        encrypted_api_key = user_settings_row["user_llm_api_key_encrypted"]
        has_user_api_key = False
        if encrypted_api_key:
            if config.APP_SECRET_KEY: 
                decrypted_key_check = encryption_utils.decrypt_data(encrypted_api_key)
                if decrypted_key_check: 
                    has_user_api_key = True
            else:
                print(f"WARNING: User {user_id} has an encrypted API key stored, but APP_SECRET_KEY is not set. Cannot verify.")

        selected_base_url_str = user_settings_row["selected_llm_base_url"]
        valid_base_url: Optional[HttpUrl] = None
        if selected_base_url_str:
            try:
                valid_base_url = HttpUrl(selected_base_url_str) # Pydantic V2 uses this for HttpUrl
            except ValueError: # Catch general ValueError for invalid URL strings
                print(f"Warning: User {user_id} has an invalid base URL stored: {selected_base_url_str}")
        
        return models.UserLLMSettingsResponse(
            selected_llm_provider_id=user_settings_row["selected_llm_provider_id"],
            selected_llm_model_id=user_settings_row["selected_llm_model_id"],
            has_user_api_key=has_user_api_key,
            selected_llm_base_url=valid_base_url
        )
    except sqlite3.Error as db_err:
        print(f"API ERROR (/api/me/llm-settings GET): Database error for user {user_id}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error retrieving LLM settings.")
    except Exception as e:
        print(f"API ERROR (/api/me/llm-settings GET): Unexpected error for user {user_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected server error retrieving LLM settings.")
    finally:
        if conn:
            conn.close()

@app.put("/api/me/llm-settings", response_model=models.UserLLMSettingsResponse, tags=["User Account Management", "LLM Configuration"])
async def update_user_llm_settings(
    settings_update: models.UserLLMSettingsUpdateRequest,
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """
    Updates the current authenticated user's LLM settings.
    """
    user_id = current_user["id"]
    conn = None

    if settings_update.selected_llm_provider_id:
        provider_config_info = config.LLM_PROVIDERS.get(settings_update.selected_llm_provider_id)
        if not provider_config_info:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid provider ID: {settings_update.selected_llm_provider_id}")
        
        if settings_update.selected_llm_model_id:
            model_found = any(
                model.get("model_id") == settings_update.selected_llm_model_id
                for model in provider_config_info.get("available_models", [])
            )
            if not model_found:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model ID '{settings_update.selected_llm_model_id}' not found for provider '{settings_update.selected_llm_provider_id}'."
                )
        elif provider_config_info.get("available_models"):
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="If a provider is selected, a model ID must also be selected.")

    encrypted_api_key_to_store: Optional[str] = None
    if settings_update.user_llm_api_key is not None: 
        if settings_update.user_llm_api_key == "": 
            encrypted_api_key_to_store = None
            print(f"User {user_id} clearing API key.")
        else:
            if not config.APP_SECRET_KEY:
                print("CRITICAL SECURITY: APP_SECRET_KEY is not set. Cannot encrypt user API key.")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server configuration error: Cannot save API key securely.")
            encrypted_api_key_to_store = encryption_utils.encrypt_data(settings_update.user_llm_api_key)
            if not encrypted_api_key_to_store:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to encrypt API key. Check server logs.")
            print(f"User {user_id} updating API key (encrypted).")
    
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?",
            (user_id,)
        )
        current_db_settings = cursor.fetchone()
        if not current_db_settings:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found for settings update.")

        final_provider_id = settings_update.selected_llm_provider_id if settings_update.selected_llm_provider_id is not None else current_db_settings["selected_llm_provider_id"]
        final_model_id = settings_update.selected_llm_model_id if settings_update.selected_llm_model_id is not None else current_db_settings["selected_llm_model_id"]
        
        if settings_update.user_llm_api_key is not None:
            final_api_key_encrypted = encrypted_api_key_to_store
        else: 
            final_api_key_encrypted = current_db_settings["user_llm_api_key_encrypted"]

        final_base_url_str: Optional[str] = None
        if settings_update.selected_llm_base_url is not None: 
            if not settings_update.selected_llm_base_url : 
                 final_base_url_str = None
            else: 
                 final_base_url_str = str(settings_update.selected_llm_base_url)
        else: 
            final_base_url_str = current_db_settings["selected_llm_base_url"]

        if settings_update.selected_llm_provider_id and settings_update.selected_llm_provider_id != current_db_settings["selected_llm_provider_id"]:
            new_provider_config = config.get_provider_config(settings_update.selected_llm_provider_id)
            if new_provider_config:
                if new_provider_config.get("type") == "ollama":
                    if settings_update.user_llm_api_key is None: 
                        final_api_key_encrypted = None
                    if settings_update.selected_llm_base_url is None: 
                        final_base_url_str = None
        
        if final_provider_id is None: 
            final_model_id = None
            final_api_key_encrypted = None
            final_base_url_str = None

        cursor.execute(
            """UPDATE users SET
               selected_llm_provider_id = ?,
               selected_llm_model_id = ?,
               user_llm_api_key_encrypted = ?,
               selected_llm_base_url = ?
               WHERE id = ?""",
            (final_provider_id, final_model_id, final_api_key_encrypted, final_base_url_str, user_id)
        )
        conn.commit()

        has_user_api_key_after_update = bool(final_api_key_encrypted)
        if final_api_key_encrypted and config.APP_SECRET_KEY: 
            decrypted_check = encryption_utils.decrypt_data(final_api_key_encrypted)
            has_user_api_key_after_update = bool(decrypted_check)
        elif final_api_key_encrypted and not config.APP_SECRET_KEY:
            has_user_api_key_after_update = False 

        updated_base_url_obj: Optional[HttpUrl] = None
        if final_base_url_str:
            try: updated_base_url_obj = HttpUrl(final_base_url_str) # Pydantic V2 HttpUrl
            except ValueError: pass

        print(f"User {user_id} LLM settings updated successfully.")
        return models.UserLLMSettingsResponse(
            selected_llm_provider_id=final_provider_id,
            selected_llm_model_id=final_model_id,
            has_user_api_key=has_user_api_key_after_update,
            selected_llm_base_url=updated_base_url_obj
        )

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/llm-settings PUT): Database error for user {user_id}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error updating LLM settings.")
    except Exception as e:
        if conn: conn.rollback()
        print(f"API ERROR (/api/me/llm-settings PUT): Unexpected error for user {user_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected server error updating LLM settings.")
    finally:
        if conn:
            conn.close()


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
        llm.get_model(provider_id=config.DEFAULT_LLM_PROVIDER_ID,
            model_id=config.DEFAULT_LLM_MODEL_ID)
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



@app.post("/token", response_model=models.Token, tags=["Authentication"])
async def login_for_access_token(
    response: FastAPIResponse,
    form_data: OAuth2PasswordRequestForm = Depends()
):
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

@app.post("/register", response_model=models.RegistrationResponse, tags=["Authentication"])
async def register_new_user(request_data: models.RegistrationRequest, request: Request):
    email = request_data.email.lower().strip()
    name = request_data.name.strip()

    if not name: 
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty.")

    conn = None 
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

        plain_password = database.generate_secure_token(12)
        hashed_password = auth.get_password_hash(plain_password)

        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active) VALUES (?, ?, ?, ?)",
            (name, email, hashed_password, True)
        )
        user_id = cursor.lastrowid
        if not user_id:
            conn.rollback()
            raise sqlite3.Error("Failed to get lastrowid after user insertion.")

        # Construct login_page_url using config.BASE_URL
        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        parsed_url_from_fastapi = urlparse(login_url_from_fastapi)
        login_path_component = parsed_url_from_fastapi.path 
        if parsed_url_from_fastapi.query: 
            login_path_component += "?" + parsed_url_from_fastapi.query
        login_page_url = f"{config.BASE_URL.rstrip('/')}{login_path_component}"
        
        email_sent = await email_utils.send_registration_password_email(
            recipient_email=email,
            recipient_name=name,
            generated_password=plain_password,
            login_url=login_page_url
        )

        if not email_sent:
            conn.commit()  # Commit user creation even if email fails
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

@app.get("/login", response_class=HTMLResponse, name="get_login_page_route", tags=["Pages"])
async def get_login_page_route(request: Request, user: Optional[Dict] = Depends(auth.get_current_user)):
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
    chain: Any, 
    memory: Any, 
    websocket: WebSocket, 
    client_js_id: str,
    current_user: Dict[str, Any], 
    session_id: str, 
    user_input: str, 
    turn_id: int
):
    """
    Handles an individual chat message: saves user message, gets AI response, saves AI response.
    The 'chain' and 'memory' are pre-configured based on user/session settings.
    """
    user_name = current_user.get('name', 'User')
    user_db_id = current_user['id']
    full_response = ""
    thinking_content: Optional[str] = None 
    stream_id = f"{client_js_id}_{turn_id}"  
    stop_event: Optional[asyncio.Event] = None 

    # 1. Save User Message to DB
    db_conn_user_msg = None
    try:
        db_conn_user_msg = database.get_db_connection()
        db_cursor_user_msg = db_conn_user_msg.cursor()
        db_cursor_user_msg.execute(
            """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, client_id_temp, turn_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_db_id, user_name, 'user', user_input, client_js_id, turn_id)
        )
        db_conn_user_msg.commit()
    except Exception as db_err:
        print(f"DB ERROR saving user message for session {session_id}, turn {turn_id}: {db_err}")
        traceback.print_exc()
        if db_conn_user_msg: db_conn_user_msg.rollback()
    finally:
        if db_conn_user_msg: db_conn_user_msg.close()

    # 2. Process with LLM Chain and Stream Response
    try:
        stop_event = await state.register_ai_stream(stream_id)
        print(f"AI STREAM: Starting stream {stream_id} for session {session_id}, input: '{user_input[:70]}...'")

        if chain is None:
            print(f"CRITICAL ERROR in handle_chat_message: LLM chain is None for session {session_id}. Cannot process message.")
            error_msg_no_chain = "<ERROR>LLM Error: Chat model not available. Please check server configuration or user settings."
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(error_msg_no_chain)
                await websocket.send_text("<EOS>")
            return 

        async for chunk_data in chain.astream({"input": user_input}):
            if stop_event and stop_event.is_set():
                print(f"AI STREAM: Stop event set for stream {stream_id}. Breaking loop.")
                break  
            
            chunk_str = ""
            if isinstance(chunk_data, dict): 
                chunk_str = chunk_data.get("answer", "") 
            elif hasattr(chunk_data, 'content'): 
                chunk_str = chunk_data.content
            else: 
                chunk_str = str(chunk_data) 

            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WS: WebSocket disconnected during LLM stream for session {session_id}, stream {stream_id}. Aborting send.")
                return  
            await websocket.send_text(chunk_str)
            full_response += chunk_str
        
        if stop_event and stop_event.is_set():
            print(f"AI STREAM: Stream {stream_id} was stopped by signal.")
        elif websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text("<EOS>")  
            print(f"AI STREAM: Finished streaming naturally for stream {stream_id} to session {session_id}")
        
        if memory: 
            memory.save_context({"input": user_input}, {"output": full_response})
            if hasattr(state, 'save_memory_state_to_db'):  
                try: 
                    state.save_memory_state_to_db(session_id, memory)  
                except Exception as save_mem_err: 
                    print(f"ERROR saving memory state to DB for session {session_id}: {save_mem_err}")
                    traceback.print_exc()
        
        db_conn_ai_msg = None
        try:
            db_conn_ai_msg = database.get_db_connection()
            db_cursor_ai_msg = db_conn_ai_msg.cursor()
            db_cursor_ai_msg.execute(
                """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, thinking_content, client_id_temp, turn_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",  
                (session_id, None, "AI", 'ai', full_response, thinking_content, client_js_id, turn_id)
            )
            db_conn_ai_msg.commit()
        except Exception as db_err:
            print(f"DB ERROR saving AI message for session {session_id} (stream {stream_id}): {db_err}")
            traceback.print_exc()
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
                await websocket.send_text("<EOS>") 
            except Exception as send_err:
                print(f"WS ERROR: Could not send LLM error/EOS to client {client_js_id} for stream {stream_id}: {send_err}")
    finally:
        if stream_id and stop_event:  
            await state.unregister_ai_stream(stream_id)


@app.websocket("/ws/{session_id_ws}/{client_js_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id_ws: str = FastApiPath(..., title="Session ID", description="The ID of the chat session."),
    client_js_id: str = FastApiPath(..., title="Client JS ID", description="A unique ID generated by the client-side JavaScript.")
):
    """
    Handles WebSocket connections for chat.
    Authenticates user, verifies session participation, sets up LLM chain based on user preferences,
    and processes incoming/outgoing messages.
    """
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

    conn_verify = None
    try:
        conn_verify = database.get_db_connection()
        cursor_verify = conn_verify.cursor()
        cursor_verify.execute("SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id_ws,))
        if not cursor_verify.fetchone():
            print(f"{ws_log_prefix} Session not found or inactive. Closing.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION); return
        cursor_verify.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id_ws, user_id))
        if not cursor_verify.fetchone():
            print(f"{ws_log_prefix} User NOT participant. Closing.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION); return
    except Exception as e:
        print(f"{ws_log_prefix} DB error verifying participation: {e}"); traceback.print_exc()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR); return
    finally:
        if conn_verify: conn_verify.close()
    
    try:
        await websocket.accept()
        print(f"{ws_log_prefix} WebSocket connection accepted.")
    except Exception as accept_err:
        print(f"{ws_log_prefix} Error accepting WebSocket connection: {accept_err}")
        return 

    selected_provider_id: Optional[str] = None
    selected_model_id: Optional[str] = None
    user_api_key: Optional[str] = None 
    user_base_url: Optional[str] = None

    db_conn_settings = None
    try:
        db_conn_settings = database.get_db_connection()
        cursor_settings = db_conn_settings.cursor()
        cursor_settings.execute(
            "SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?",
            (user_id,)
        )
        user_llm_prefs = cursor_settings.fetchone()

        if user_llm_prefs:
            selected_provider_id = user_llm_prefs["selected_llm_provider_id"]
            selected_model_id = user_llm_prefs["selected_llm_model_id"]
            user_base_url = user_llm_prefs["selected_llm_base_url"] 
            
            encrypted_key = user_llm_prefs["user_llm_api_key_encrypted"]
            if encrypted_key:
                if config.APP_SECRET_KEY: 
                    user_api_key = encryption_utils.decrypt_data(encrypted_key)
                    if not user_api_key:
                         print(f"{ws_log_prefix} Failed to decrypt user's API key. Key might be invalid or APP_SECRET_KEY changed.")
                else:
                    print(f"{ws_log_prefix} User has an encrypted API key, but APP_SECRET_KEY is not set. Cannot use user's key.")

        if not selected_provider_id or not selected_model_id:
            print(f"{ws_log_prefix} User has no LLM preference or it's incomplete. Falling back to system defaults.")
            selected_provider_id = config.DEFAULT_LLM_PROVIDER_ID
            selected_model_id = config.DEFAULT_LLM_MODEL_ID
            user_api_key = None 
            user_base_url = None 
        else:
            print(f"{ws_log_prefix} Using user's LLM preference: Provider='{selected_provider_id}', Model='{selected_model_id}'")
            if user_base_url: print(f"{ws_log_prefix} User's custom base URL for this provider: {user_base_url}")
            if user_api_key: print(f"{ws_log_prefix} Using user's decrypted API key for this provider.")
            elif selected_provider_id != "ollama_local": 
                 provider_conf_check = config.get_provider_config(selected_provider_id)
                 if provider_conf_check and provider_conf_check.get("api_key_env_var_name"):
                    print(f"{ws_log_prefix} No user-specific API key; will attempt to use ENV var '{provider_conf_check.get('api_key_env_var_name')}' for provider '{selected_provider_id}'.")
    except Exception as e:
        print(f"{ws_log_prefix} Error fetching user LLM settings: {e}. Falling back to system defaults for LLM."); traceback.print_exc()
        selected_provider_id = config.DEFAULT_LLM_PROVIDER_ID
        selected_model_id = config.DEFAULT_LLM_MODEL_ID
        user_api_key = None 
        user_base_url = None 
    finally:
        if db_conn_settings: db_conn_settings.close()

    memory = state.get_memory_for_client(session_id_ws) 
    def load_memory_for_current_session(_ignored_input_map=None):
        return memory.load_memory_variables({}).get("history", [])
    
    chain: Optional[Any] = None 
    try:
        chain = llm.create_chain(
            provider_id=selected_provider_id,
            model_id=selected_model_id,
            memory_loader_func=load_memory_for_current_session,
            api_key=user_api_key, 
            base_url_override=user_base_url 
        )
        if not chain:
            raise ValueError(f"LLM chain creation failed for provider '{selected_provider_id}', model '{selected_model_id}'.")
        
        print(f"{ws_log_prefix} LLM chain created successfully using provider '{selected_provider_id}', model '{selected_model_id}', base_url_override '{user_base_url}'.")

    except Exception as chain_init_error:
        print(f"{ws_log_prefix} ERROR creating LCEL chain: {chain_init_error}"); traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_json({"type": "error", "payload": {"message": "Server error: Could not initialize chat with the selected model configuration."}})
            except Exception as send_json_err:
                 print(f"{ws_log_prefix} Error sending JSON error to client: {send_json_err}")
        if websocket.client_state != WebSocketState.CLOSED :
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

                if message_type == "chat_message" and payload:
                    user_input = payload.get("user_input")
                    turn_id = payload.get("turn_id") 
                    if user_input is not None and turn_id is not None:
                        print(f"{ws_log_prefix} Received 'chat_message' for turn {turn_id}.")
                        asyncio.create_task(
                            handle_chat_message(chain, memory, websocket, client_js_id, current_ws_user, session_id_ws, user_input, turn_id)
                        )
                    else:
                        print(f"{ws_log_prefix} Invalid 'chat_message' payload (missing input or turn_id): {payload}")
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_text("<ERROR>Invalid chat message payload from client.<EOS>")
                
                elif message_type == "run_code" and payload and payload.get("code_block_id"):
                    language = payload.get("language")
                    code = payload.get("code")
                    code_block_id = payload.get("code_block_id")
                    print(f"{ws_log_prefix} Received 'run_code' for block {code_block_id}.")
                    if language and code is not None:
                        asyncio.create_task(docker_utils.run_code_in_docker_stream(websocket, client_js_id, code_block_id, language, code))
                    else:
                        print(f"{ws_log_prefix} Invalid 'run_code' payload for block {code_block_id}.")
                        if websocket.client_state == WebSocketState.CONNECTED:
                             await websocket.send_json({"type": "code_finished", "payload": {"code_block_id": code_block_id, "exit_code": -1, "error": "Invalid run_code payload."}})

                elif message_type == "stop_code" and payload and payload.get("code_block_id"):
                    code_block_id = payload.get("code_block_id")
                    print(f"{ws_log_prefix} Received 'stop_code' for block {code_block_id}.")
                    asyncio.create_task(docker_utils.stop_docker_container(code_block_id))

                elif message_type == "stop_ai_stream" and payload:
                    stop_client_id = payload.get("client_id") 
                    stop_session_id = payload.get("session_id") 
                    stop_turn_id = payload.get("turn_id")
                    if stop_client_id == client_js_id and stop_session_id == session_id_ws and stop_turn_id is not None:
                        stream_id_to_stop = f"{stop_client_id}_{stop_turn_id}"
                        print(f"{ws_log_prefix} Received 'stop_ai_stream' for stream ID: {stream_id_to_stop}")
                        stopped = await state.signal_stop_ai_stream(stream_id_to_stop)
                        if stopped: print(f"{ws_log_prefix} Successfully signaled stop for stream {stream_id_to_stop}.")
                        else: print(f"{ws_log_prefix} Failed to signal stop or stream {stream_id_to_stop} not found.")
                    else: 
                        print(f"{ws_log_prefix} Received 'stop_ai_stream' with mismatched IDs or missing turn_id: {payload}")
                
                else:
                    print(f"{ws_log_prefix} Received unknown JSON command type '{message_type}' or invalid payload.")
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_text(f"<ERROR>Unknown command type: {message_type}<EOS>")

            except json.JSONDecodeError:
                print(f"{ws_log_prefix} Received non-JSON data, treating as legacy chat or error: {received_data[:100]}...")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text("<ERROR>Invalid message format. Expected JSON.<EOS>")
            
            except Exception as handler_exc: 
                print(f"{ws_log_prefix} ERROR handling received message: {handler_exc}")
                traceback.print_exc()
                if websocket.client_state == WebSocketState.CONNECTED:
                    try:
                        await websocket.send_text(f"<ERROR>Server error processing your request: {str(handler_exc)}<EOS>")
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
        print(f"{ws_log_prefix} Cleaning up WebSocket resources...")
        await docker_utils.cleanup_client_containers(client_js_id) 
        if websocket.client_state == WebSocketState.CONNECTED:
            try: await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            except Exception as final_close_err: print(f"{ws_log_prefix} Error during final WebSocket close: {final_close_err}")
        print(f"{ws_log_prefix} WebSocket cleanup complete for client {client_js_id}.")


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


@app.get("/api/sessions/{session_id}/messages", response_model=List[models.MessageItem], tags=["Messages"]) # Assuming models.MessageItem exists
async def get_chat_messages_for_session(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch messages for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """Fetches all messages for a session the user participates in."""
    if not user: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id = user['id']
    messages_list = []
    conn = None
    print(f"API (get_messages): Attempting to fetch messages for session {session_id}, user {user_id}.") # <<< ADDED LOG
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        # Verify session exists and user is participant (existing logic)
        cursor.execute("SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        if not cursor.fetchone(): 
            print(f"API (get_messages): Session {session_id} not found or inactive.") # <<< ADDED LOG
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or inactive.")
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone(): 
            print(f"API (get_messages): User {user_id} denied access to session {session_id}.") # <<< ADDED LOG
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this session.")
        
        # Fetch messages
        print(f"API (get_messages): Executing query for messages in session {session_id}.") # <<< ADDED LOG
        cursor.execute(
            """SELECT id, session_id, user_id, sender_name, sender_type, content, client_id_temp, thinking_content, timestamp, turn_id
               FROM chat_messages WHERE session_id = ? ORDER BY timestamp ASC""", # Added turn_id to SELECT
            (session_id,)
        )
        rows = cursor.fetchall()
        print(f"API (get_messages): Fetched {len(rows)} rows from DB for session {session_id}.") # <<< ADDED LOG
        
        for row_num, row_data in enumerate(rows):
            # print(f"API (get_messages): Processing row {row_num + 1}: {dict(row_data)}") # Optional: log each row
            messages_list.append(dict(row_data))
            
        print(f"API (get_messages): Returning {len(messages_list)} messages for session {session_id}.") # <<< ADDED LOG
        return messages_list
        
    except sqlite3.Error as db_err:
        print(f"API ERROR (get_messages): DB error for user {user_id}, session {session_id}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching messages.")
    except HTTPException as http_exc: 
        raise http_exc # Re-raise specific HTTP exceptions
    except Exception as e:
        print(f"API ERROR (get_messages): Unexpected error for user {user_id}, session {session_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error fetching messages.")
    finally:
        if conn: 
            conn.close()
            print(f"API (get_messages): DB connection closed for session {session_id}.") # <<< ADDED LOG



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
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8001))
    url = f"http://{host}:{port}"
    print("-" * 30)
    print(f"Tesseracs Chat Server Starting...")
    print(f"Access via: {url}/login")
    print(f"Default LLM: Provider='{config.DEFAULT_LLM_PROVIDER_ID}', Model='{config.DEFAULT_LLM_MODEL_ID}'")
    print(f"Static files from: {config.STATIC_DIR}, Bundles from: {dist_dir}")
    print("-" * 30)
    try:
        print(f"Attempting to open {url}/login in default browser...")
        webbrowser.open(f"{url}/login")
    except Exception as browser_err:
        print(f"Warning: Could not automatically open browser: {browser_err}")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info", reload=True)

if __name__ == "__main__":
    start_server()

