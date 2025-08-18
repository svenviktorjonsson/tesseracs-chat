# In app/main.py

# Ensure your imports at the top of app/main.py look like this:
import os
import sys
import traceback
from pathlib import Path
import asyncio
import json
import sqlite3
import uuid
from urllib.parse import urlparse
import datetime
from typing import Optional, Dict, Any, List, Union # Ensure List and Union are here if used elsewhere

from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Form,
    Depends,
    Response as FastAPIResponse, # This is an alias, not the Response class itself for type hinting
    Path as FastApiPath,
    Body,
    status
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    JSONResponse,
    FileResponse,
    Response  # *** ADDED Response here ***
)
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState

from pydantic import HttpUrl

from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

# Assuming your local modules are imported like this:
from . import config
from . import state
from . import llm
from . import docker_utils
from . import utils
from . import database
from . import auth
from . import email_utils
from . import models
from . import encryption_utils

app = FastAPI(title="Tesseracs Chat CSRF Example")

# CSRF Configuration Loader
@CsrfProtect.load_config
def get_csrf_config():
    loaded_secret_from_config = getattr(config, 'CSRF_PROTECT_SECRET_KEY', None)
    final_csrf_secret = None
    # IMPORTANT: This default fallback secret is INSECURE for production.
    # It MUST be overridden by a strong, unique secret in your environment/config.
    default_fallback_secret = "a_very_secure_fallback_secret_key_must_be_at_least_32_bytes_long_0123456789"

    if isinstance(loaded_secret_from_config, str) and len(loaded_secret_from_config) >= 32:
        final_csrf_secret = loaded_secret_from_config
        print(f"DEBUG CSRF: Using configured CSRF_PROTECT_SECRET_KEY (first 5 chars): '{final_csrf_secret[:5]}...'.")
    else:
        if loaded_secret_from_config is None:
            print("WARNING CSRF: CSRF_PROTECT_SECRET_KEY not found in config. Using a placeholder DEMO secret key. THIS IS INSECURE FOR PRODUCTION.")
        elif not isinstance(loaded_secret_from_config, str):
            print(f"CRITICAL CSRF ERROR: CSRF_PROTECT_SECRET_KEY from config is not a string (type: {type(loaded_secret_from_config).__name__}). Using placeholder. THIS IS INSECURE FOR PRODUCTION.")
        elif len(loaded_secret_from_config) < 32:
            print(f"WARNING CSRF: CSRF_PROTECT_SECRET_KEY from config is too short (length: {len(loaded_secret_from_config)}, requires >=32). Using placeholder. THIS IS INSECURE FOR PRODUCTION.")
        final_csrf_secret = default_fallback_secret
        print(f"WARNING CSRF: Using placeholder DEMO secret key (first 5 chars): '{final_csrf_secret[:5]}...'. Ensure CSRF_PROTECT_SECRET_KEY is correctly set and is at least 32 bytes long in your production environment/config.")
    
    print("CSRF CONFIG: Using list-based configuration to explicitly set all parameters.")
    return [
        ("secret_key", final_csrf_secret),
        ("cookie_key", "fastapi-csrf-token"),      # Name of the CSRF token cookie
        ("header_name", "X-CSRF-Token"),          # Name of the CSRF token header for AJAX
        ("httponly", True),                      # CSRF cookie should be HttpOnly
    ]

# CSRF Exception Handler
@app.exception_handler(CsrfProtectError)
async def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
    print(f"---- CSRF EXCEPTION HANDLER: Caught CsrfProtectError ----")
    print(f"---- CSRF EXCEPTION HANDLER: Request URL: {request.url}")
    print(f"---- CSRF EXCEPTION HANDLER: Request Method: {request.method}")
    print(f"---- CSRF EXCEPTION HANDLER: Exception message: {exc.message}") 
    print(f"---- CSRF EXCEPTION HANDLER: Exception status code: {exc.status_code}")
    relevant_headers = {
        "content-type": request.headers.get("content-type"),
        "x-csrf-token": request.headers.get("x-csrf-token"), 
        "referer": request.headers.get("referer"),
    }
    print(f"---- CSRF EXCEPTION HANDLER: Relevant Request Headers: {relevant_headers}")
    try:
        form_data = await request.form()
        print(f"---- CSRF EXCEPTION HANDLER: Form Data at time of exception: {dict(form_data)}")
    except Exception as e_form_log:
        print(f"---- CSRF EXCEPTION HANDLER: Could not log form data at time of exception: {e_form_log}")

    return JSONResponse(
        status_code=exc.status_code, 
        content={"detail": exc.message if exc.message else "CSRF Validation Failed"}
    )
# Helper function to serve HTML files with CSRF token injection for JavaScript
async def serve_html_with_csrf(
    file_path: Path,
    request: Request,
    csrf_protect: CsrfProtect,
    replacements: Optional[Dict[str, str]] = None
) -> HTMLResponse:
    if not file_path.is_file():
        print(f"---- SERVER LOG: HTML SERVER - CRITICAL ERROR: HTML file not found at '{file_path}'")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Resource {file_path.name} not found.")

    html_content_original = ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html_content_original = f.read()
        print(f"---- SERVER LOG: HTML SERVER - '{file_path.name}' content (length: {len(html_content_original)}) read successfully.")
    except Exception as e:
        print(f"---- SERVER LOG: HTML SERVER - ERROR reading '{file_path.name}': {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error loading content for {file_path.name}.")

    _raw_token_csrf, signed_token_for_cookie = csrf_protect.generate_csrf_tokens()
    if not _raw_token_csrf or not signed_token_for_cookie:
        print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - CRITICAL ERROR: Failed to generate CSRF tokens.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="CSRF token generation failed on server.")

    print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - Raw CSRF token generated for JS: '{_raw_token_csrf[:10]}...'")

    html_content_processed = html_content_original
    
    # Standard CSRF token replacement for JavaScript variable `window.csrfTokenRaw`
    csrf_placeholder = "%%CSRF_TOKEN_RAW%%"
    if csrf_placeholder in html_content_processed:
        html_content_processed = html_content_processed.replace(csrf_placeholder, _raw_token_csrf)
        if csrf_placeholder in html_content_processed: # Check if replacement failed
             print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - CRITICAL FAILURE: Placeholder '{csrf_placeholder}' replacement had no effect. Token: '{_raw_token_csrf}'.")
        else:
            print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - SUCCESS: Placeholder '{csrf_placeholder}' replaced for JS.")
    else:
        print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - INFO: Placeholder '{csrf_placeholder}' NOT FOUND in HTML. Token not directly injected for 'window.csrfTokenRaw'.")

    # Apply additional dynamic replacements if any
    if replacements:
        for key, value in replacements.items():
            if key in html_content_processed:
                html_content_processed = html_content_processed.replace(key, str(value)) # Ensure value is string
                print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - Replaced placeholder '{key}'.")
            else:
                print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - INFO: Additional placeholder '{key}' not found.")

    response = HTMLResponse(content=html_content_processed)
    try:
        csrf_protect.set_csrf_cookie(response=response, csrf_signed_token=signed_token_for_cookie)
        print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - CSRF cookie set in response.")
    except Exception as e:
        print(f"---- SERVER LOG: HTML SERVER ('{file_path.name}') - ERROR during CSRF cookie setting: {e}")
        traceback.print_exc()
        # Not raising HTTPException here as content is ready, but cookie setting failed. Client might still work if old cookie is valid.

    return response

# --- Authentication & Page Routes ---

@app.get("/login", response_class=HTMLResponse, name="get_login_page_route", tags=["Pages"])
async def get_login_page_route(
    request: Request,
    # CRITICAL: This route MUST use `auth.get_current_user` for OPTIONAL authentication.
    # This allows unauthenticated users (like new users) to access the login page.
    # `auth.get_current_user` returns the user dict if authenticated, or None otherwise,
    # and importantly, it does NOT raise an HTTPException if the user is not authenticated.
    user: Optional[Dict[str, Any]] = Depends(auth.get_current_user), 
    csrf_protect: CsrfProtect = Depends() # Ensure CsrfProtect is correctly imported
) -> Response: # MODIFIED: Changed return type hint to Response
    """
    Serves the login page.
    If the user is already authenticated, it redirects them to the session choice page.
    Otherwise, it displays the login form.
    """
    print("---- SERVER LOG: GET /login - Route entered ----")
    
    # Log the state of the user object obtained from the optional dependency.
    # For a new, unauthenticated user, 'user' should be None here.
    if user is None:
        print("---- SERVER LOG: GET /login - `auth.get_current_user` returned None (User is not authenticated). This is expected for new users.")
    else:
        print(f"---- SERVER LOG: GET /login - `auth.get_current_user` returned a user object. Email: '{user.get('email')}'.")

    if user:
        # User is already authenticated (auth.get_current_user returned user data).
        # Redirect them away from the login page.
        print(f"---- SERVER LOG: GET /login - User '{user.get('email')}' already authenticated. Redirecting to session choice page...")
        try:
            # Ensure you have a route named 'get_session_choice_page'
            # This name should match the 'name' parameter in the @app.get("/") decorator for your session choice page.
            session_choice_url = request.url_for("get_session_choice_page")
        except Exception as e:
            # Fallback if the named route isn't found (should not happen in a well-configured app)
            print(f"---- SERVER LOG: GET /login - CRITICAL ERROR: Could not find route named 'get_session_choice_page'. Defaulting to '/'. Error: {e}")
            session_choice_url = "/" 
        return RedirectResponse(url=str(session_choice_url), status_code=status.HTTP_302_FOUND)

    # If 'user' is None, the user is not authenticated. Proceed to show the login page.
    print("---- SERVER LOG: GET /login - Proceeding to serve login page for unauthenticated user.")
    
    # Ensure config.STATIC_DIR is correctly defined and points to your static files directory
    if not config.STATIC_DIR or not (config.STATIC_DIR / "login.html").is_file():
        print(f"---- SERVER LOG: GET /login - CRITICAL ERROR: login.html not found at {config.STATIC_DIR / 'login.html' if config.STATIC_DIR else 'configured STATIC_DIR'}.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Login page resource is missing on the server.")
        
    login_html_path = config.STATIC_DIR / "login.html"
    
    # Assuming serve_html_with_csrf is your helper function to read HTML, inject CSRF, and set cookies
    # serve_html_with_csrf should return an HTMLResponse
    html_response_content = await serve_html_with_csrf(login_html_path, request, csrf_protect)
    return html_response_content # This is already an HTMLResponse



@app.get("/", response_class=HTMLResponse, name="get_session_choice_page", tags=["Pages"])
async def get_session_choice_page_route( # Renamed function to avoid conflict if you had another
    request: Request,
    # Use the OPTIONAL user dependency here
    user: Optional[Dict[str, Any]] = Depends(auth.get_current_user),
    csrf_protect: CsrfProtect = Depends()
) -> Response: # Can return HTMLResponse or RedirectResponse
    """
    Serves the session choice page if the user is authenticated.
    If the user is not authenticated, redirects them to the login page.
    """
    print("---- SERVER LOG: GET / (get_session_choice_page) - Route entered ----")

    if user is None:
        # User is not authenticated, redirect to the login page.
        print("---- SERVER LOG: GET / (get_session_choice_page) - User not authenticated. Redirecting to /login.")
        try:
            login_url = request.url_for("get_login_page_route")
        except Exception as e:
            print(f"---- SERVER LOG: GET / (get_session_choice_page) - CRITICAL ERROR: Could not find route named 'get_login_page_route'. Defaulting to '/login'. Error: {e}")
            login_url = "/login" # Fallback
        return RedirectResponse(url=str(login_url), status_code=status.HTTP_302_FOUND)

    # User is authenticated, proceed to show the session choice page.
    print(f"---- SERVER LOG: GET / (get_session_choice_page) - User '{user.get('email')}' authenticated. Serving session-choice page.")
    
    if not config.STATIC_DIR or not (config.STATIC_DIR / "session-choice.html").is_file():
        print(f"---- SERVER LOG: GET / (get_session_choice_page) - CRITICAL ERROR: session-choice.html not found.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Session choice page resource is missing.")
        
    session_choice_html_path = config.STATIC_DIR / "session-choice.html"
    replacements = {"[User Name]": user.get("name", "User")} # Ensure this placeholder exists in session-choice.html
    
    # Assuming serve_html_with_csrf is your helper function
    return await serve_html_with_csrf(session_choice_html_path, request, csrf_protect, replacements=replacements)

@app.get("/chat/{session_id}", response_class=HTMLResponse, name="get_chat_page_for_session", tags=["Pages"])
async def get_chat_page_for_session(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the chat session to load."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user), # Ensure this is the correct auth dependency
    csrf_protect: CsrfProtect = Depends()
):
    print(f"---- SERVER LOG: GET /chat/{session_id} - Route entered ----")
    if not user: # Should be handled by Depends
        print(f"---- SERVER LOG: GET /chat/{session_id} - No active user, redirecting to login.")
        # Ensure get_login_page_route is correctly named if you use url_for
        login_url = request.url_for("get_login_page_route") if "get_login_page_route" in request.app.router.routes else "/login"
        return RedirectResponse(url=str(login_url), status_code=status.HTTP_302_FOUND)

    user_id = user['id']
    session_name_for_html = "Chat Session" # Default name
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        # Fetch session details
        cursor.execute("SELECT id, name FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        session_row = cursor.fetchone()
        
        if not session_row:
            print(f"---- SERVER LOG: GET /chat/{session_id} - Session not found or inactive.")
            raise HTTPException(status_code=404, detail="Chat session not found or is inactive.")
        
        session_name_for_html = session_row["name"] # This is your "May 16, 2025..." string

        # Verify user has access to this session
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone():
            print(f"---- SERVER LOG: GET /chat/{session_id} - User {user_id} lacks access.")
            raise HTTPException(status_code=403, detail="You do not have access to this chat session.")

        # Update last_accessed_at for the session
        current_time_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute("UPDATE sessions SET last_accessed_at = ? WHERE id = ?", (current_time_utc_iso, session_id))
        conn.commit()
        print(f"---- SERVER LOG: GET /chat/{session_id} - Access granted for user {user_id}. Session last_accessed_at updated.")

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc() # It's good to have traceback for unexpected errors
        raise HTTPException(status_code=500, detail="Error verifying session access for chat page.")
    finally:
        if conn: conn.close()

    chat_html_path = config.STATIC_DIR / "chat-session.html"
    if not chat_html_path.is_file():
        print(f"---- SERVER LOG: GET /chat/{session_id} - CRITICAL ERROR: chat-session.html not found at {chat_html_path}")
        raise HTTPException(status_code=500, detail="Chat page resource is missing on the server.")

    # *** CORRECTED REPLACEMENTS ***
    # Define a specific placeholder that you will use in your chat-session.html
    # For example, let's use "%%SESSION_NAME_PLACEHOLDER%%"
    replacements = {
        "%%SESSION_NAME_PLACEHOLDER%%": utils.escape_html(session_name_for_html)
    }
    
    return await serve_html_with_csrf(chat_html_path, request, csrf_protect, replacements=replacements)


@app.get("/settings", response_class=HTMLResponse, name="get_settings_page", tags=["Pages"])
async def get_settings_page(
    request: Request,
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    print("---- SERVER LOG: GET /settings - Route entered ----")
    if not user: # Should be caught by Depends(auth.get_current_active_user)
        print("---- SERVER LOG: GET /settings - No active user, redirecting to login (unexpected).")
        login_url = request.url_for("get_login_page_route")
        return RedirectResponse(url=str(login_url), status_code=status.HTTP_302_FOUND)

    settings_html_path = config.STATIC_DIR / "settings.html"
    # Add any specific replacements for settings.html if needed
    # e.g. replacements = {"": utils.escape_html(user.get("email", ""))}
    return await serve_html_with_csrf(settings_html_path, request, csrf_protect) # Add replacements if any

@app.post("/check_email", response_model=models.EmailCheckResponse, tags=["Authentication"])
async def check_email_exists_route(
    request: Request,
    request_data: models.EmailCheckRequest,
    csrf_protect: CsrfProtect = Depends()
):
    print(f"---- SERVER LOG: POST /check_email - Route entered for email: {request_data.email} ----")
    # CSRF token is expected in the X-CSRF-Token header for AJAX by default
    await csrf_protect.validate_csrf(request)
    print(f"---- SERVER LOG: POST /check_email - CSRF validation PASSED for email: {request_data.email} ----")

    email_to_check = request_data.email.lower().strip()
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM users WHERE email = ? AND is_active = 1", (email_to_check,))
        user_row = cursor.fetchone()
        if user_row:
            user_name = user_row["name"]
            return models.EmailCheckResponse(exists=True, user_name=user_name)
        else:
            return models.EmailCheckResponse(exists=False, user_name=None)
    except sqlite3.Error as e:
        print(f"---- SERVER LOG: POST /check_email - Database error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error checking email.")
    except Exception as e:
        print(f"---- SERVER LOG: POST /check_email - Unexpected error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected error checking email.")
    finally:
        if conn: conn.close()

@app.post("/token", response_model=models.Token, tags=["Authentication"])
async def login_for_access_token(
    request: Request,
    response: FastAPIResponse,
    form_data: OAuth2PasswordRequestForm = Depends(),
    csrf_protect: CsrfProtect = Depends()
):
    print("---- SERVER LOG: POST /token - Route entered ----")
    # For form submissions, fastapi-csrf-protect checks the form field "csrf_token" by default.
    await csrf_protect.validate_csrf(request)
    print("---- SERVER LOG: POST /token - CSRF validation PASSED ----")

    email = form_data.username.lower().strip()
    password = form_data.password
    conn = None
    print(f"---- SERVER LOG: POST /token - Attempting login for email: {email}")
    try:
        conn = database.get_db_connection()
        user_dict = auth.authenticate_user_from_db(conn, email, password) # Refactored to auth module potentially
        if not user_dict: # Covers user not found or password mismatch
            print(f"---- SERVER LOG: POST /token - Authentication failed for email: {email}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")
        
        if not user_dict["is_active"]:
            print(f"---- SERVER LOG: POST /token - Login FAILED: Account inactive for email: {email}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")
        
        print(f"---- SERVER LOG: POST /token - Login SUCCESSFUL for user ID: {user_dict['id']}. Creating session...")
        session_token_raw = await auth.create_user_session(response=response, user_id=user_dict["id"]) # Sets session cookie
        
        # After successful login, new CSRF tokens should be set on the response for the new authenticated context.
        _raw_token_new, signed_token_for_cookie_new = csrf_protect.generate_csrf_tokens()
        csrf_protect.set_csrf_cookie(response=response, csrf_signed_token=signed_token_for_cookie_new)
        print(f"---- SERVER LOG: POST /token - New CSRF cookie set in response. Raw part of new token: {_raw_token_new[:10]}...")
        
        return models.Token(
            access_token=session_token_raw, 
            token_type="bearer", # This is for API access, session cookie is for browser
            user_id=user_dict["id"], 
            user_name=user_dict["name"], 
            user_email=user_dict["email"]
            # The client JS after login will typically redirect or refresh,
            # and the new page served via serve_html_with_csrf will get its %%CSRF_TOKEN_RAW%%
        )
    except HTTPException as http_exc:
        raise http_exc # Re-raise known HTTP exceptions
    except sqlite3.Error as db_err:
        print(f"---- SERVER LOG: POST /token - Database error for {email}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during login process.")
    except Exception as e:
        print(f"---- SERVER LOG: POST /token - Unexpected server error during login for {email}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected server error during login.")
    finally:
        if conn: conn.close()

@app.post("/sessions/create", status_code=status.HTTP_303_SEE_OTHER, tags=["Sessions"])
async def create_new_session_route(
    request: Request, 
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    print("---- SERVER LOG: POST /sessions/create - Route entered ----")

    # Let CsrfProtect handle reading the form data internally.
    # With csrf_form_field_name explicitly set in config, it should prioritize this.
    try:
        await csrf_protect.validate_csrf(request)
        print("---- SERVER LOG: POST /sessions/create - CSRF validation PASSED ----")
    except CsrfProtectError as e_csrf: 
        print(f"---- SERVER LOG: POST /sessions/create - CSRF validation FAILED (CsrfProtectError). Message: {e_csrf.message}")
        raise 
    except Exception as e_csrf_val: 
        print(f"---- SERVER LOG: POST /sessions/create - CSRF validation FAILED (Unexpected Exception). Error type: {type(e_csrf_val).__name__}, Message: {e_csrf_val}")
        traceback.print_exc() 
        raise 

    if not user: 
        print("---- SERVER LOG: POST /sessions/create - User not authenticated (should have been caught by Depends).")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated for session creation.")

    new_session_id = str(uuid.uuid4())
    host_user_id = user["id"]
    conn = None
    default_session_name = ""
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            """INSERT INTO sessions (id, host_user_id, name, is_active, created_at, last_accessed_at) 
               VALUES (?, ?, ?, ?, datetime('now', 'utc'), datetime('now', 'utc'))""",
            (new_session_id, host_user_id, None, True) 
        )
        cursor.execute("SELECT created_at FROM sessions WHERE id = ?", (new_session_id,))
        session_row = cursor.fetchone()
        
        if not session_row or not session_row["created_at"]:
            default_session_name = f"Session ({new_session_id[:4]})"
            print(f"---- SERVER LOG: POST /sessions/create - Warning: Could not retrieve created_at for session {new_session_id}. Using fallback name: {default_session_name}")
        else:
            try:
                created_at_str = session_row["created_at"]
                if isinstance(created_at_str, str):
                     created_at_str = created_at_str.replace('Z', '+00:00') 
                created_dt = datetime.datetime.fromisoformat(created_at_str)
                if created_dt.tzinfo is None: 
                    created_dt = created_dt.replace(tzinfo=datetime.timezone.utc)
                default_session_name = created_dt.strftime("%b %d, %Y %I:%M %p UTC")
            except (ValueError, TypeError) as parse_err:
                print(f"---- SERVER LOG: POST /sessions/create - Error parsing created_at '{session_row['created_at']}': {parse_err}. Using fallback name.")
                default_session_name = f"Session ({new_session_id[:4]})"
        
        cursor.execute("UPDATE sessions SET name = ? WHERE id = ?", (default_session_name, new_session_id))
        
        cursor.execute(
            "INSERT INTO session_participants (session_id, user_id) VALUES (?, ?)",
            (new_session_id, host_user_id)
        )
        conn.commit()
        print(f"---- SERVER LOG: POST /sessions/create - New session '{new_session_id}' ('{default_session_name}') created by user '{host_user_id}'.")
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error: Could not create new session.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error: Could not create new session.")
    finally:
        if conn: conn.close()
    
    try:
        chat_url = request.url_for("get_chat_page_for_session", session_id=new_session_id)
    except Exception as url_for_err:
        print(f"---- SERVER LOG: POST /sessions/create - Error generating URL for 'get_chat_page_for_session': {url_for_err}. Defaulting redirect.")
        chat_url = f"/chat/{new_session_id}" 

    response = RedirectResponse(url=str(chat_url), status_code=status.HTTP_303_SEE_OTHER)
    return response




@app.post("/register", response_model=models.RegistrationResponse, tags=["Authentication"])
async def register_new_user(
    request_data: models.RegistrationRequest,
    request: Request,
    csrf_protect: CsrfProtect = Depends()
):
    print(f"---- SERVER LOG: POST /register - Attempting registration for email: {request_data.email} ----")
    await csrf_protect.validate_csrf(request) # Expects X-CSRF-Token for JSON request
    print(f"---- SERVER LOG: POST /register - CSRF validation PASSED for email: {request_data.email} ----")

    email = request_data.email.lower().strip()
    name = request_data.name.strip()

    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty.")
    if not utils.is_valid_email(email): # Assuming utils.is_valid_email exists
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email format.")

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This email address is already registered.")
        
        plain_password = database.generate_secure_token(12) # Or from config.PASSWORD_LENGTH
        hashed_password = auth.get_password_hash(plain_password)
        
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (name, email, hashed_password, True) # is_active usually True on registration
        )
        user_id = cursor.lastrowid
        if not user_id: # Should not happen if insert is successful and table has autoincrement PK
            conn.rollback()
            print(f"---- SERVER LOG: POST /register - CRITICAL: Failed to get lastrowid after user insertion for {email}.")
            raise sqlite3.Error("User insertion failed to return an ID.")

        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        # Construct a robust login_page_url
        parsed_base_url = urlparse(config.BASE_URL)
        parsed_login_route_url = urlparse(login_url_from_fastapi)
        login_page_url = parsed_base_url._replace(path=parsed_login_route_url.path, query=parsed_login_route_url.query, fragment=parsed_login_route_url.fragment).geturl()
        
        email_sent = await email_utils.send_registration_password_email(
            recipient_email=email, recipient_name=name, generated_password=plain_password, login_url=login_page_url
        )
        
        if not email_sent:
            # Account is created, but email failed. This is a critical issue for user experience.
            # Log this error clearly.
            # Commit user creation but inform about email failure.
            conn.commit() # Commit the user anway
            print(f"---- SERVER LOG: POST /register - User '{email}' registered (ID: {user_id}), BUT FAILED to send password email.")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account created, but there was an issue sending your password email. Please try the 'Forgot Password' option or contact support.")
        
        conn.commit()
        print(f"---- SERVER LOG: POST /register - User '{email}' (ID: {user_id}) registered successfully. Password email sent.")
        return models.RegistrationResponse(message="Account created successfully! Your password has been sent to your email address.")

    except HTTPException as http_exc: # Re-raise known HTTP exceptions
        if conn: conn.rollback() # Ensure rollback on handled errors too
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="A database error occurred during registration.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred during registration.")
    finally:
        if conn: conn.close()

@app.post("/forgot_password", response_model=models.ForgotPasswordResponse, tags=["Authentication"])
async def handle_forgot_password(
    request_data: models.ForgotPasswordRequest,
    request: Request,
    csrf_protect: CsrfProtect = Depends()
):
    print(f"---- SERVER LOG: POST /forgot_password - Request for email: {request_data.email} ----")
    await csrf_protect.validate_csrf(request) # Expects X-CSRF-Token for JSON
    print(f"---- SERVER LOG: POST /forgot_password - CSRF validation PASSED for email: {request_data.email} ----")

    email = request_data.email.lower().strip()
    if not utils.is_valid_email(email): # Assuming utils.is_valid_email
        # Still return generic message to prevent email enumeration
        return models.ForgotPasswordResponse(message="If an account with this email exists and is active, a password reset email has been sent.")

    client_ip = request.client.host if request.client else "unknown_ip"
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # Always log the attempt for auditing and rate limiting analysis
        cursor.execute(
            "INSERT INTO password_reset_attempts (email, ip_address, attempted_at) VALUES (?, ?, datetime('now'))",
            (email, client_ip)
        )
        # Don't commit yet, commit at the end of successful operations or before returning generic message

        # Rate limiting check (after logging the current attempt)
        time_window_start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=config.FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS)
        cursor.execute(
            "SELECT COUNT(*) FROM password_reset_attempts WHERE email = ? AND attempted_at >= ?",
            (email, time_window_start.isoformat())
        )
        attempt_count_row = cursor.fetchone()
        recent_attempts = attempt_count_row[0] if attempt_count_row else 0

        if recent_attempts > config.FORGOT_PASSWORD_ATTEMPT_LIMIT: # Use > because current attempt is already logged
            conn.commit() # Commit the logged attempt
            print(f"---- SERVER LOG: POST /forgot_password - Rate limit exceeded for {email}. Attempts: {recent_attempts}.")
            return models.ForgotPasswordResponse(message="If an account with this email exists and is active, a password reset email has been sent.")

        cursor.execute("SELECT id, name FROM users WHERE email = ? AND is_active = 1", (email,))
        user_row = cursor.fetchone()

        if not user_row:
            conn.commit() # Commit the logged attempt
            print(f"---- SERVER LOG: POST /forgot_password - No active user found for {email}.")
            return models.ForgotPasswordResponse(message="If an account with this email exists and is active, a password reset email has been sent.")

        user_dict = dict(user_row)
        user_id = user_dict["id"]
        user_name = user_dict["name"]

        new_plain_password = database.generate_secure_token(12)
        new_hashed_password = auth.get_password_hash(new_plain_password)

        cursor.execute("UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE id = ?", (new_hashed_password, user_id))
        if cursor.rowcount == 0: # Should not happen if user_row was found
            conn.rollback() # Rollback the attempt log as well if this fails
            print(f"---- SERVER LOG: POST /forgot_password - ERROR: Failed to update password hash for user {email} (ID: {user_id}) though user was found.")
            # Return generic message, but this is a server issue.
            return models.ForgotPasswordResponse(message="If an account with this email exists and is active, a password reset email has been sent.")

        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        parsed_base_url = urlparse(config.BASE_URL)
        parsed_login_route_url = urlparse(login_url_from_fastapi)
        login_page_url = parsed_base_url._replace(path=parsed_login_route_url.path, query=parsed_login_route_url.query, fragment=parsed_login_route_url.fragment).geturl()
        
        email_sent = await email_utils.send_password_reset_email(
            recipient_email=email, recipient_name=user_name, new_password=new_plain_password, login_url=login_page_url
        )

        if not email_sent:
            conn.rollback() # Rollback password change AND the attempt log if email fails critically
            print(f"---- SERVER LOG: POST /forgot_password - Password for {email} was reset in DB, but email sending FAILED. Transaction rolled back.")
            # Still return generic message to the user.
            return models.ForgotPasswordResponse(message="If an account with this email exists and is active, a password reset email has been sent.")
            # Server-side, this might be a 502 error if you want to be more explicit about failure.

        conn.commit() # Commit password change and the successful attempt log
        print(f"---- SERVER LOG: POST /forgot_password - Password reset email successfully sent for user {email} (ID: {user_id}).")
        return models.ForgotPasswordResponse(message="If an account with this email exists and is active, a password reset email has been sent.")

    except sqlite3.Error as db_err:
        if conn: conn.rollback() # Rollback any partial changes
        traceback.print_exc()
        print(f"---- SERVER LOG: POST /forgot_password - Database error for {email}: {db_err}")
        # Return generic message to prevent leaking information
        return models.ForgotPasswordResponse(message="An error occurred while processing your request. Please try again.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        print(f"---- SERVER LOG: POST /forgot_password - Unexpected error for {email}: {e}")
        return models.ForgotPasswordResponse(message="An unexpected error occurred. Please try again.")
    finally:
        if conn: conn.close()


# --- User Account Management API Routes (/api/me/*) ---
# These routes can be called by client-side JavaScript using fetch (likely with Bearer token)
# OR from a settings page form (potentially with session cookie + CSRF token).
# The `auth.get_current_active_user` dependency handles Bearer token auth primarily.
# We add `csrf_protect` and a check to enforce CSRF if not a Bearer call.

async def _ensure_csrf_for_cookie_auth(request: Request, csrf_protect: CsrfProtect):
    """Helper to validate CSRF if the request is not using Bearer token auth."""
    auth_header = request.headers.get("authorization")
    is_bearer_auth = auth_header and auth_header.lower().startswith("bearer ")
    if not is_bearer_auth:
        print(f"---- CSRF CHECK: Path '{request.url.path}' is not Bearer auth, validating CSRF token.")
        await csrf_protect.validate_csrf(request)
        print(f"---- CSRF CHECK: Path '{request.url.path}' CSRF token validated for cookie-based request.")
    else:
        print(f"---- CSRF CHECK: Path '{request.url.path}' is Bearer auth, skipping CSRF token validation.")


@app.put("/api/me/llm-settings", response_model=models.UserLLMSettingsResponse, tags=["User Account Management", "LLM Configuration"])
async def update_user_llm_settings(
    request: Request,
    settings_update: models.UserLLMSettingsUpdateRequest,
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user), # Handles Bearer token
    csrf_protect: CsrfProtect = Depends() # For CSRF validation if not Bearer
):
    await _ensure_csrf_for_cookie_auth(request, csrf_protect)
    user_id = current_user["id"]
    conn = None

    # Validate provider and model IDs against config
    if settings_update.selected_llm_provider_id:
        provider_config_info = config.LLM_PROVIDERS.get(settings_update.selected_llm_provider_id)
        if not provider_config_info:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid provider ID: {settings_update.selected_llm_provider_id}")
        
        available_models_for_provider = provider_config_info.get("available_models", [])
        if settings_update.selected_llm_model_id:
            model_found = any(
                model.get("model_id") == settings_update.selected_llm_model_id
                for model in available_models_for_provider
            )
            if not model_found:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model ID '{settings_update.selected_llm_model_id}' not found or not valid for provider '{settings_update.selected_llm_provider_id}'."
                )
        elif available_models_for_provider: # Provider has models, but none selected in update
             # If a provider is selected that has defined models, a model must also be selected.
             # If the user intends to clear the model, they should also clear the provider or select a provider with no predefined models.
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"A model ID must be selected for provider '{settings_update.selected_llm_provider_id}'.")

    encrypted_api_key_to_store: Optional[str] = None
    if settings_update.user_llm_api_key is not None: # Field is present in the request
        if settings_update.user_llm_api_key == "": # User explicitly wants to clear the key
            encrypted_api_key_to_store = None
        else:
            if not config.APP_SECRET_KEY:
                print("CRITICAL ERROR: APP_SECRET_KEY is not set. Cannot encrypt user API key.")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server configuration error: API key encryption service is unavailable.")
            encrypted_api_key_to_store = encryption_utils.encrypt_data(settings_update.user_llm_api_key)
            if not encrypted_api_key_to_store: # Should only happen if encryption fails unexpectedly
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to secure API key.")
    
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?",
            (user_id,)
        )
        current_db_settings = cursor.fetchone()
        if not current_db_settings: # Should not happen for an active authenticated user
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User settings record not found.")

        # Determine final values: if a field is in settings_update, use it; otherwise, keep DB value.
        final_provider_id = settings_update.selected_llm_provider_id if settings_update.selected_llm_provider_id is not None else current_db_settings["selected_llm_provider_id"]
        final_model_id = settings_update.selected_llm_model_id if settings_update.selected_llm_model_id is not None else current_db_settings["selected_llm_model_id"]
        
        if settings_update.user_llm_api_key is not None: # API key was part of the update request
            final_api_key_encrypted = encrypted_api_key_to_store
        else: # API key was not in the update request, keep existing
            final_api_key_encrypted = current_db_settings["user_llm_api_key_encrypted"]

        final_base_url_str: Optional[str] = None
        if settings_update.selected_llm_base_url is not None: # Base URL was part of the update
            if not settings_update.selected_llm_base_url: # Explicitly empty (e.g., empty string from form)
                final_base_url_str = None
            else:
                final_base_url_str = str(settings_update.selected_llm_base_url) # Convert HttpUrl to string
        else: # Base URL not in update, keep existing
            final_base_url_str = current_db_settings["selected_llm_base_url"]

        # Logic to clear dependent fields if provider is cleared or fundamentally changed
        if final_provider_id is None:
            final_model_id = None
            final_api_key_encrypted = None # Clearing provider implies clearing user-specific key for it
            final_base_url_str = None    # Clearing provider implies clearing user-specific base URL
        elif final_provider_id != current_db_settings["selected_llm_provider_id"]:
            # If provider changed, re-evaluate if existing API key/base URL are still relevant
            # For simplicity here, if provider changes, and user didn't explicitly send new key/URL,
            # we might clear them if the new provider type suggests it (e.g. ollama often doesn't need user key).
            # This logic can be more nuanced based on provider types.
            new_provider_config_details = config.get_provider_config(final_provider_id)
            if new_provider_config_details and new_provider_config_details.get("type") == "ollama":
                if settings_update.user_llm_api_key is None: # If user didn't send a new key for ollama
                    final_api_key_encrypted = None
                if settings_update.selected_llm_base_url is None: # If user didn't send a new base_url for ollama
                    final_base_url_str = None # Default to system config for ollama base URL unless user specifies
        
        # Ensure model is compatible with provider
        if final_provider_id and final_model_id:
            prov_info = config.LLM_PROVIDERS.get(final_provider_id)
            if not prov_info or not any(m.get("model_id") == final_model_id for m in prov_info.get("available_models", [])):
                # This case implies an inconsistency, possibly model was valid for old provider but not new one
                # Or user cleared model without clearing provider that requires one.
                # Safest might be to clear model if it's not valid for the final_provider_id
                final_model_id = None 
                # If the provider requires a model, this state (provider set, model None) might be an issue later.
                # The initial validation should catch "provider selected, model required but not provided".

        cursor.execute(
            """UPDATE users SET
                selected_llm_provider_id = ?,
                selected_llm_model_id = ?,
                user_llm_api_key_encrypted = ?,
                selected_llm_base_url = ?,
                updated_at = datetime('now')
                WHERE id = ?""",
            (final_provider_id, final_model_id, final_api_key_encrypted, final_base_url_str, user_id)
        )
        conn.commit()

        has_user_api_key_after_update = False
        if final_api_key_encrypted and config.APP_SECRET_KEY:
            try:
                decrypted_check = encryption_utils.decrypt_data(final_api_key_encrypted)
                has_user_api_key_after_update = bool(decrypted_check and decrypted_check.strip())
            except Exception:
                has_user_api_key_after_update = False 
                print(f"WARNING: User {user_id} LLM settings update - could not decrypt stored API key for verification.")
        elif final_api_key_encrypted and not config.APP_SECRET_KEY:
            has_user_api_key_after_update = True # Assume it's present but unverified
            print(f"WARNING: User {user_id} LLM settings update - APP_SECRET_KEY not set, cannot verify has_user_api_key status accurately.")
        
        updated_base_url_obj: Optional[HttpUrl] = None
        if final_base_url_str:
            try:
                updated_base_url_obj = HttpUrl(final_base_url_str)
            except ValueError:
                print(f"WARNING: User {user_id} LLM settings - stored base_url '{final_base_url_str}' is invalid.")
                pass # Keep it None
        
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
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error occurred while updating LLM settings.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred while updating LLM settings.")
    finally:
        if conn: conn.close()

@app.post("/api/me/regenerate-password", response_model=models.RegeneratePasswordResponse, tags=["User Account Management"])
async def regenerate_user_password(
    request: Request,
    payload: models.RegeneratePasswordRequest = Body(...),
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await _ensure_csrf_for_cookie_auth(request, csrf_protect)
    user_id = current_user.get("id")
    user_email = current_user.get("email")
    user_name = current_user.get("name", "User") # Ensure name is available

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = ? AND email = ?", (user_id, user_email))
        user_record = cursor.fetchone()
        if not user_record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User record not found. Please try logging out and back in.")
        
        stored_password_hash = user_record["password_hash"]
        if not stored_password_hash:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot verify password due to an account data issue. Please contact support.")

        if not auth.verify_password(payload.current_password, stored_password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect current password.")

        new_plain_password = database.generate_secure_token(12) # Or from config
        new_hashed_password = auth.get_password_hash(new_plain_password)
        
        cursor.execute("UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE id = ?", (new_hashed_password, user_id))
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update password due to a server error.")

        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        parsed_base_url = urlparse(config.BASE_URL)
        parsed_login_route_url = urlparse(login_url_from_fastapi)
        login_page_url = parsed_base_url._replace(path=parsed_login_route_url.path, query=parsed_login_route_url.query, fragment=parsed_login_route_url.fragment).geturl()
        
        email_sent = await email_utils.send_password_reset_email( # Re-using this, or create a dedicated one
            recipient_email=user_email, recipient_name=user_name, new_password=new_plain_password, login_url=login_page_url
        )
        if not email_sent:
            conn.rollback() # Critical: if email fails, rollback password change
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Password was reset in the database, but the notification email failed to send. The password change has been rolled back. Please try again.")

        # Invalidate all existing session tokens for this user
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            "UPDATE auth_tokens SET used_at = ?, expires_at = ? WHERE user_id = ? AND token_type = 'session' AND used_at IS NULL",
            (now_utc_iso, now_utc_iso, user_id)
        )
        conn.commit()
        return models.RegeneratePasswordResponse(
            message="Password regenerated successfully. An email has been sent with your new password. You should now log out all other sessions and log back in with the new password."
        )
    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="A database error occurred while regenerating your password.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred.")
    finally:
        if conn: conn.close()

@app.patch("/api/me/update-email", response_model=models.UpdateEmailResponse, tags=["User Account Management"])
async def update_user_email(
    request: Request,
    payload: models.UpdateEmailRequest = Body(...),
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await _ensure_csrf_for_cookie_auth(request, csrf_protect)
    user_id = current_user.get("id")
    current_email_for_logging = current_user.get("email") # For logging comparison
    new_email_normalized = payload.new_email.lower().strip()

    if not utils.is_valid_email(new_email_normalized):
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid new email address format.")
    if new_email_normalized == current_email_for_logging:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New email address is the same as the current one.")

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user_record = cursor.fetchone()
        if not user_record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User record not found. Please try logging out and back in.")
        
        stored_password_hash = user_record["password_hash"]
        if not stored_password_hash:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot verify password due to an account data issue. Please contact support.")

        if not auth.verify_password(payload.current_password, stored_password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect current password.")

        cursor.execute("SELECT id FROM users WHERE email = ? AND id != ?", (new_email_normalized, user_id))
        if cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This email address is already in use by another account.")

        cursor.execute("UPDATE users SET email = ?, updated_at = datetime('now') WHERE id = ?", (new_email_normalized, user_id))
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update email due to a server error during the update operation.")

        # Invalidate all existing session tokens for this user as email (username) changed
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            "UPDATE auth_tokens SET used_at = ?, expires_at = ? WHERE user_id = ? AND token_type = 'session' AND used_at IS NULL",
            (now_utc_iso, now_utc_iso, user_id)
        )
        conn.commit()
        return models.UpdateEmailResponse(
            message="Email updated successfully. You will now be logged out to apply changes and must log in with your new email address.",
            new_email=new_email_normalized
        )
    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="A database error occurred while updating your email.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred while updating your email.")
    finally:
        if conn: conn.close()

@app.patch("/api/me/update-name", response_model=models.UpdateNameResponse, tags=["User Account Management"])
async def update_user_name(
    request: Request,
    update_data: models.UpdateNameRequest = Body(...),
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await _ensure_csrf_for_cookie_auth(request, csrf_protect)
    user_id = current_user.get("id")
    new_name_stripped = update_data.new_name.strip()

    if not new_name_stripped:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name cannot be empty.")
    if len(new_name_stripped) > 100: # Example length validation
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name is too long (maximum 100 characters).")

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user_record = cursor.fetchone()
        if not user_record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User record not found. Please try logging out and back in.")
        
        stored_password_hash = user_record["password_hash"]
        if not stored_password_hash:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot verify password due to an account data issue. Please contact support.")

        if not auth.verify_password(update_data.current_password, stored_password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect current password.")

        cursor.execute("UPDATE users SET name = ?, updated_at = datetime('now') WHERE id = ?", (new_name_stripped, user_id))
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update name due to a server error during the update operation.")
        
        conn.commit()
        return models.UpdateNameResponse(message="Name updated successfully.", new_name=new_name_stripped)
    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="A database error occurred while updating your name.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred while updating your name.")
    finally:
        if conn: conn.close()


# --- LLM, User, Session Data (mostly GET requests or auth via Bearer, CSRF not primary concern for GET) ---

@app.get("/api/llm/providers", response_model=List[models.LLMProviderDetail], tags=["LLM Configuration"])
async def list_llm_providers(
    current_user: Optional[Dict[str, Any]] = Depends(auth.get_current_user) # Can be optional if public info
):
    # This is a GET request, so CSRF is not typically applied for read operations.
    response_providers = []
    for provider_id, provider_data_from_config_root in config.LLM_PROVIDERS.items():
        # Get runtime configuration which might include resolved env vars
        provider_runtime_config = config.get_provider_config(provider_id)
        if not provider_runtime_config:
            print(f"Warning: Runtime configuration for LLM provider '{provider_id}' not found. Skipping.")
            continue

        # Determine if the system has a key configured for this provider
        is_system_key_configured = False
        api_key_env_var_name = provider_runtime_config.get("api_key_env_var_name")
        if api_key_env_var_name and os.getenv(api_key_env_var_name):
            is_system_key_configured = True
        
        # A provider "requires_api_key_from_user" if:
        # 1. The provider definition inherently can use an API key (e.g., OpenAI, Anthropic).
        # 2. AND the system does NOT have a global key configured for it.
        # Some providers (like 'ollama' or local models) might not require a key at all.
        # Some (like 'openai_compatible_server') might always allow a user key even if a system one exists.
        
        # Base assumption: provider needs a key if it's one of the known types that use keys
        # OR if its config explicitly mentions an API key environment variable.
        provider_type_can_use_key = provider_id in config.PROVIDERS_TYPICALLY_USING_API_KEYS or \
                                    bool(api_key_env_var_name)

        # Does the user *need* to provide a key? True if the type can use a key AND system doesn't have one.
        needs_api_key_from_user = provider_type_can_use_key and not is_system_key_configured
        
        # Can the user *optionally* provide a key? (e.g. for OpenAI compatible servers, even if system has one)
        can_accept_user_api_key = provider_id in config.PROVIDERS_ALLOWING_USER_KEYS_EVEN_IF_SYSTEM_CONFIGURED or needs_api_key_from_user


        available_models_details = []
        for model_info in provider_data_from_config_root.get("available_models", []):
            available_models_details.append(
                models.LLMAvailableModel(
                    model_id=model_info.get("model_id"),
                    display_name=model_info.get("display_name"),
                    context_window=model_info.get("context_window") # Ensure this is handled if missing
                )
            )
        
        response_providers.append(
            models.LLMProviderDetail(
                id=provider_id,
                display_name=provider_data_from_config_root.get("display_name", provider_id.replace("_", " ").title()),
                type=provider_runtime_config.get("type", "unknown"),
                is_system_configured=is_system_key_configured or not provider_type_can_use_key, # System is "configured" if key is present OR if provider type doesn't need one
                can_accept_user_api_key=can_accept_user_api_key, # If user can enter their own key
                needs_api_key_from_user=needs_api_key_from_user, # If user *must* enter a key because system lacks one
                available_models=available_models_details,
                # Does provider support a user-defined base_url?
                can_accept_user_base_url=provider_runtime_config.get("type") == "openai_compatible_server" # Example
            )
        )
    return response_providers


@app.get("/api/me/llm-settings", response_model=models.UserLLMSettingsResponse, tags=["User Account Management", "LLM Configuration"])
async def get_user_llm_settings(
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user) # Bearer token auth
):
    # GET request, CSRF not primary concern
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

        if not user_settings_row: # Should not happen for an active user
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User LLM settings not found.")

        selected_provider_id = user_settings_row["selected_llm_provider_id"]
        selected_model_id = user_settings_row["selected_llm_model_id"]
        encrypted_api_key = user_settings_row["user_llm_api_key_encrypted"]
        selected_base_url_str = user_settings_row["selected_llm_base_url"]

        # If user has no provider/model selected in DB, use system defaults from config
        if selected_provider_id is None and config.DEFAULT_LLM_PROVIDER_ID:
            selected_provider_id = config.DEFAULT_LLM_PROVIDER_ID
            # If provider is defaulted, model should also be the system default model for that provider
            # or the overall system default model if the default provider doesn't specify one.
            default_provider_config = config.LLM_PROVIDERS.get(config.DEFAULT_LLM_PROVIDER_ID, {})
            if default_provider_config.get("available_models"):
                selected_model_id = default_provider_config["available_models"][0]["model_id"] # First model of default provider
            else: # Fallback to overall default model ID if any
                selected_model_id = config.DEFAULT_LLM_MODEL_ID

            # If defaulting the provider, logically clear any user-specific API key or base URL
            # as these were not explicitly chosen by the user for this default.
            encrypted_api_key = None
            selected_base_url_str = None
        elif selected_provider_id and not selected_model_id: # Provider chosen, but model missing (e.g. after config change)
            provider_config = config.LLM_PROVIDERS.get(selected_provider_id, {})
            if provider_config.get("available_models"):
                 selected_model_id = provider_config["available_models"][0]["model_id"] # Default to first model of chosen provider

        has_user_api_key = False
        if encrypted_api_key:
            if config.APP_SECRET_KEY:
                try:
                    decrypted_key_check = encryption_utils.decrypt_data(encrypted_api_key)
                    if decrypted_key_check and decrypted_key_check.strip(): # Ensure non-empty key after decryption
                        has_user_api_key = True
                except Exception: # Decryption can fail
                    print(f"WARNING: User {user_id} - Could not decrypt stored API key for has_user_api_key flag.")
                    pass # has_user_api_key remains False
            else:
                # APP_SECRET_KEY not set, cannot confirm key validity but it exists encrypted
                has_user_api_key = True # Best guess: it's there.
                print(f"WARNING: User {user_id} has an encrypted API key, but APP_SECRET_KEY is not set. Cannot fully verify for has_user_api_key flag.")

        valid_base_url_obj: Optional[HttpUrl] = None
        if selected_base_url_str:
            try:
                valid_base_url_obj = HttpUrl(selected_base_url_str)
            except ValueError:
                print(f"Warning: User {user_id} has an invalid base URL stored: {selected_base_url_str}")
                pass # Keep it None

        return models.UserLLMSettingsResponse(
            selected_llm_provider_id=selected_provider_id,
            selected_llm_model_id=selected_model_id,
            has_user_api_key=has_user_api_key,
            selected_llm_base_url=valid_base_url_obj
        )
    except sqlite3.Error as db_err:
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error retrieving LLM settings.")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected server error retrieving LLM settings.")
    finally:
        if conn: conn.close()


@app.get("/logout", tags=["Authentication"])
async def logout_route(
    request: Request, # To access CsrfProtect methods if needed, though not strictly for validation here
    response: FastAPIResponse, # To set/unset cookies
    session_token_value: Optional[str] = Depends(auth.cookie_scheme) # Gets session token from cookie
):
    # No CSRF validation needed for logout itself usually, as it's a benign action.
    # The main goal is to invalidate the session.
    print(f"---- SERVER LOG: GET /logout - User initiated logout. Session token from cookie: {'present' if session_token_value else 'missing'}")
    await auth.logout_user(response, session_token_value) # Invalidates session token and unsets session cookie
    
    # Unset the CSRF token cookie as well, as the session it was tied to is now invalid.
    # This requires a CsrfProtect instance. We can create one on the fly or inject it.
    # Since CSRF is app-wide, it might be cleaner if CsrfProtect had a static-like method or if we inject.
    # For simplicity here, instantiate, but in a larger app, manage dependencies carefully.
    # However, `fastapi-csrf-protect` sets cookies on responses where tokens are generated or validated.
    # The `logout_user` handles the session cookie. For the CSRF cookie, we explicitly unset.
    
    # Get a CsrfProtect instance - if we are not in a Depends context, we need to create it.
    # This is a bit of a workaround. Ideally, a CsrfProtect instance would be available.
    # We can try to get it from the app state if it was stored, or construct as needed.
    # For now, assuming we can construct one to call unset_csrf_cookie
    # csrf_protect_instance = CsrfProtect() # This would use default config if not loaded.
    # Instead of new instance, if we need to ensure it uses loaded config, we might need to pass it.
    # However, unsetting doesn't strictly need the secret key.

    # The CSRF cookie should be unset. `fastapi-csrf-protect` might do this if `validate_csrf` is called
    # and it decides to rotate/clear. For an explicit logout, better to be sure.
    # Let's assume csrf_protect.unset_csrf_cookie can be called on a response.
    # We might need to pass the `CsrfProtect` dependency.
    # Let's add it as a dependency to be safe.
    
    # Re-thinking: We don't generate new tokens or validate on logout usually.
    # We just want to clear the cookie.
    try:
        _csrf_protect_for_logout = CsrfProtect() # Standard instance
        _csrf_protect_for_logout.load_config(get_csrf_config) # Ensure it has the config
        _csrf_protect_for_logout.unset_csrf_cookie(response=response)
        print("---- SERVER LOG: GET /logout - CSRF token cookie explicitly unset.")
    except Exception as e_csrf_unset:
        print(f"---- SERVER LOG: GET /logout - Minor error unsetting CSRF cookie: {e_csrf_unset}")
        # Continue with redirect even if CSRF cookie unsetting had an issue.

    redirect_url = request.url_for('get_login_page_route')
    return RedirectResponse(url=str(redirect_url), status_code=status.HTTP_302_FOUND)


@app.get("/api/me", response_model=models.UserResponseModel, tags=["Users"])
async def get_current_user_details(
    user: Dict[str, Any] = Depends(auth.get_current_active_user) # Bearer token auth
):
    # GET request, CSRF not primary concern. Auth handled by Bearer token.
    if not user: # Should be caught by Depends
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    
    # Ensure all required fields are present in the user dict from the token/DB
    required_keys = ["id", "name", "email"]
    if not all(key in user for key in required_keys):
        print(f"ERROR: User object for /api/me is missing required keys. User: {user}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User data is incomplete on server.")
        
    return models.UserResponseModel(id=user["id"], name=user["name"], email=user["email"])


@app.get("/api/sessions/{session_id}/code-results", response_model=List[Dict[str, Any]], tags=["Code Execution"])
async def get_session_code_execution_results(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch code results for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
) -> List[Dict[str, Any]]:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    user_id = user.get('id')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User ID missing in token/session.")

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id)
        )
        if not cursor.fetchone():
            cursor.execute("SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found or is inactive.")
            else:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this session's code results.")
        
        results = database.get_code_execution_results(session_id)
        return results
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred while fetching code results.")
    finally:
        if conn:
            conn.close()


@app.get("/api/sessions", response_model=List[models.SessionResponseModel], tags=["Sessions"])
async def get_user_sessions(
    user: Dict[str, Any] = Depends(auth.get_current_active_user) # Bearer token auth
) -> List[models.SessionResponseModel]: # Explicit return type annotation
    # GET request, CSRF not primary concern as it's a read operation
    if not user: # Should be caught by Depends, but as a safeguard
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    user_id = user.get('id')
    if not user_id:
        # This case should ideally not happen if auth.get_current_active_user guarantees 'id'
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User ID missing in token/session.")

    sessions_list: List[models.SessionResponseModel] = []
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        # Fetches sessions where the user is a participant and session is active
        # The SQL query already aliases s.last_accessed_at AS last_active
        cursor.execute(
            """SELECT s.id, s.name, s.created_at, s.last_accessed_at AS last_active, s.host_user_id
               FROM sessions s
               JOIN session_participants sp ON s.id = sp.session_id
               WHERE sp.user_id = ? AND s.is_active = 1
               ORDER BY s.last_accessed_at DESC, s.created_at DESC""",
            (user_id,)
        )
        rows = cursor.fetchall()
        
        for row_data in rows:
            session_data_from_db = dict(row_data)
            
            # Ensure datetime fields from DB (if they are datetime objects) are converted to ISO strings
            # The model SessionResponseModel expects Optional[str] for these.
            if session_data_from_db.get("created_at") and isinstance(session_data_from_db["created_at"], (datetime.datetime, datetime.date)):
                session_data_from_db["created_at"] = session_data_from_db["created_at"].isoformat()
            
            if session_data_from_db.get("last_active") and isinstance(session_data_from_db["last_active"], (datetime.datetime, datetime.date)):
                session_data_from_db["last_active"] = session_data_from_db["last_active"].isoformat()

            # Pydantic will validate against SessionResponseModel fields:
            # id, name, created_at, last_active, host_user_id
            sessions_list.append(models.SessionResponseModel(**session_data_from_db))
            
        return sessions_list
    except sqlite3.Error as db_err:
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error while fetching sessions.")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred while fetching sessions.")
    finally:
        if conn:
            conn.close()

@app.get("/api/sessions/{session_id}/edited-blocks", response_model=Dict[str, str], tags=["Sessions"])
async def get_session_edited_blocks(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch edited code blocks for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    # User access verification
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user['id']))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this session's data.")
    conn.close()

    return database.get_edited_code_blocks(session_id)

@app.get("/api/sessions/{session_id}/messages", response_model=List[models.MessageItem], tags=["Messages"])
async def get_chat_messages_for_session(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch messages for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user) # Bearer token auth
) -> List[models.MessageItem]: # Explicit return type annotation
    # GET request, CSRF not primary concern
    if not user: # Should be caught by Depends
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    user_id = user.get('id')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User ID missing in token/session.")

    messages_list: List[models.MessageItem] = []
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        # Verify user has access to this session
        cursor.execute(
            "SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id)
        )
        if not cursor.fetchone():
            # If user is not a participant, check if the session itself is valid before denying access
            cursor.execute("SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found or is inactive.")
            else: # Session exists and is active, but user is not a participant
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this chat session's messages.")
        
        # Session is valid and user is a participant, fetch messages
        # The query already includes model_provider_id and model_id
        cursor.execute(
            """SELECT id, session_id, user_id, sender_name, sender_type, content, 
                      client_id_temp, thinking_content, timestamp, turn_id, model_provider_id, model_id
               FROM chat_messages 
               WHERE session_id = ? 
               ORDER BY timestamp ASC, id ASC""", # Added id for tie-breaking if timestamps are identical
            (session_id,)
        )
        rows = cursor.fetchall()
        
        for row_data in rows:
            message_data_from_db = dict(row_data)
            
            # Ensure timestamp is ISO format string as expected by MessageItem model
            if message_data_from_db.get("timestamp") and isinstance(message_data_from_db["timestamp"], (datetime.datetime, datetime.date)):
                message_data_from_db["timestamp"] = message_data_from_db["timestamp"].isoformat()
            
            # Pydantic will validate against MessageItem fields
            messages_list.append(models.MessageItem(**message_data_from_db))
            
        return messages_list
    except sqlite3.Error as db_err:
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error while fetching messages.")
    except HTTPException as http_exc: # Re-raise if it's already an HTTPException (like 403/404)
        raise http_exc
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected server error occurred while fetching messages.")
    finally:
        if conn:
            conn.close()


async def handle_chat_message(
    chain: Any, 
    memory: Any, 
    websocket: WebSocket, 
    client_js_id: str,
    current_user: Dict[str, Any], 
    session_id: str, 
    user_input: str, 
    turn_id: int,
    llm_provider_id_used: Optional[str], # For logging
    llm_model_id_used: Optional[str]     # For logging
):
    user_name = current_user.get('name', 'Anonymous User') # Fallback name
    user_db_id = current_user['id']
    full_response = ""
    thinking_content: Optional[str] = None # Placeholder for future use if 'think' mode provides preliminary thoughts
    stream_id = f"{client_js_id}_{turn_id}" 
    stop_event: Optional[asyncio.Event] = None
    
    # Store user message in DB
    db_conn_user_msg = None
    try:
        db_conn_user_msg = database.get_db_connection()
        db_cursor_user_msg = db_conn_user_msg.cursor()
        db_cursor_user_msg.execute(
            """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, 
                                           client_id_temp, turn_id, timestamp, 
                                           model_provider_id, model_id) 
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'), ?, ?)""",
            (session_id, user_db_id, user_name, 'user', user_input, client_js_id, turn_id, None, None) # LLM info not applicable to user msg
        )
        db_conn_user_msg.commit()
    except Exception as db_err_user:
        traceback.print_exc()
        print(f"ERROR saving user message to DB for session {session_id}: {db_err_user}")
        if db_conn_user_msg: db_conn_user_msg.rollback()
        # Do not stop processing if DB save fails, but log it.
    finally:
        if db_conn_user_msg: db_conn_user_msg.close()

    # Process with LLM
    try:
        stop_event = await state.register_ai_stream(stream_id) # For handling 'stop generation'
        if chain is None:
            error_msg_no_chain = "<ERROR>LLM Error: Chat model is not available. Please check server configuration or your LLM settings."
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(error_msg_no_chain)
                await websocket.send_text("<EOS>") # End of Stream signal
            return 

        async for chunk_data in chain.astream({"input": user_input}):
            
            # --- START: Added logging for debugging ---
            print(f"--- LLM RAW CHUNK (Turn ID: {turn_id}) ---\n{chunk_data}\n---------------------------------")
            # --- END: Added logging for debugging ---

            if stop_event and stop_event.is_set(): # Check if stop signal received
                print(f"AI stream {stream_id} stopped by client signal.")
                break 
            
            chunk_str = ""
            if isinstance(chunk_data, dict): 
                # Adapt based on actual chain output structure (e.g., LangChain LCEL)
                # Common for LCEL chains that output AIMessageChunk or similar
                if hasattr(chunk_data, 'content'): # Langchain AIMessageChunk
                    chunk_str = chunk_data.content
                elif "answer" in chunk_data: # Older LangChain style
                    chunk_str = chunk_data.get("answer", "")
                else: # Try to find content in a common key
                    content_keys = ['content', 'text', 'chunk']
                    for key in content_keys:
                        if key in chunk_data and isinstance(chunk_data[key], str):
                            chunk_str = chunk_data[key]
                            break
                    if not chunk_str:
                        print(f"DEBUG: LLM chunk_data (dict) received with unexpected structure: {chunk_data}")

            elif hasattr(chunk_data, 'content') and isinstance(chunk_data.content, str): # For AIMessageChunk like objects
                chunk_str = chunk_data.content
            else: 
                chunk_str = str(chunk_data) # Fallback
            
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WebSocket for stream {stream_id} disconnected during AI response.")
                return 
            
            if chunk_str: # Only send if there's content
                await websocket.send_text(chunk_str)
                full_response += chunk_str
        
        # After loop finishes (either by completing or by stop_event)
        if websocket.client_state == WebSocketState.CONNECTED:
            if stop_event and stop_event.is_set():
                await websocket.send_text("<EOS_STOPPED>") # Signal client generation was stopped
            else:
                await websocket.send_text("<EOS>") # Normal End of Stream signal

        if memory: 
            try:
                memory.save_context({"input": user_input}, {"output": full_response})
                if hasattr(state, 'save_memory_state_to_db'): 
                    state.save_memory_state_to_db(session_id, memory) 
            except Exception as save_mem_err: 
                traceback.print_exc()
                print(f"Error saving memory state for session {session_id}: {save_mem_err}")
        
        # Store AI response in DB
        db_conn_ai_msg = None
        try:
            db_conn_ai_msg = database.get_db_connection()
            db_cursor_ai_msg = db_conn_ai_msg.cursor()
            db_cursor_ai_msg.execute(
                """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, 
                                              thinking_content, client_id_temp, turn_id, timestamp,
                                              model_provider_id, model_id) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'), ?, ?)""", 
                (session_id, None, "AI", 'ai', full_response, thinking_content, client_js_id, turn_id, llm_provider_id_used, llm_model_id_used)
            )
            db_conn_ai_msg.commit()
        except Exception as db_err_ai:
            traceback.print_exc()
            print(f"ERROR saving AI message to DB for session {session_id}: {db_err_ai}")
            if db_conn_ai_msg: db_conn_ai_msg.rollback()
        finally:
            if db_conn_ai_msg: db_conn_ai_msg.close()

    except Exception as chain_exc:
        error_msg = f"<ERROR>LLM Error: Processing your message failed. Details: {str(chain_exc)}"
        traceback.print_exc()
        print(f"Error during LLM chain processing for session {session_id}: {chain_exc}")
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(error_msg)
                await websocket.send_text("<EOS>") 
            except Exception as send_err:
                print(f"Error sending LLM processing error to client {client_js_id}: {send_err}")
    finally:
        if stream_id and stop_event: 
            await state.unregister_ai_stream(stream_id)

@app.websocket("/ws/{session_id_ws}/{client_js_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id_ws: str = FastApiPath(..., title="Session ID", description="The ID of the chat session."),
    client_js_id: str = FastApiPath(..., title="Client JS ID", description="A unique ID generated by the client-side JavaScript.")
):
    print(f"---- WS LOG: Attempting WebSocket connection for session_id_ws: {session_id_ws}, client_js_id: {client_js_id} ----")
    print(f"---- WS LOG: WebSocket request headers: {websocket.headers}")
    print(f"---- WS LOG: WebSocket request cookies: {websocket.cookies}")

    session_token_from_cookie = websocket.cookies.get(auth.SESSION_COOKIE_NAME)
    current_ws_user: Optional[Dict[str, Any]] = None

    if not session_token_from_cookie:
        print(f"---- WS ERROR: No session token found in cookies. Cookie name expected: '{auth.SESSION_COOKIE_NAME}'. Cannot authenticate WebSocket.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required - no session token.")
        return
    
    print(f"---- WS LOG: Session token found in cookie: '{session_token_from_cookie[:10]}...' (first 10 chars)")

    try:
        # *** CORRECTED FUNCTION NAME HERE ***
        current_ws_user = await auth.get_user_by_session_token_internal(session_token_from_cookie)
    except Exception as e_auth_token:
        print(f"---- WS ERROR: Exception during auth.get_user_by_session_token_internal for token '{session_token_from_cookie[:10]}...': {e_auth_token}")
        traceback.print_exc()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Server error during token validation.")
        return

    if not current_ws_user:
        print(f"---- WS ERROR: WebSocket authentication failed for session {session_id_ws}, client {client_js_id}. Token validation returned no user.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication failed - invalid session token.")
        return

    user_id = current_ws_user.get('id')
    user_email = current_ws_user.get('email', 'N/A') # Get email for logging
    if not user_id:
        print(f"---- WS ERROR: Authenticated user object for email '{user_email}' is missing 'id'. User object: {current_ws_user}")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Server error - user data incomplete.")
        return
        
    print(f"---- WS LOG: User successfully authenticated via session token. User ID: {user_id}, Email: {user_email}")
    print(f"---- WS LOG: Verifying session access for user {user_id} on session {session_id_ws}...")

    conn_verify = None
    try:
        conn_verify = database.get_db_connection()
        cursor_verify = conn_verify.cursor()
        
        cursor_verify.execute("SELECT id, name, is_active FROM sessions WHERE id = ?", (session_id_ws,))
        session_details = cursor_verify.fetchone()

        if not session_details:
            print(f"---- WS ERROR: Session {session_id_ws} not found in database for user {user_id}.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found.")
            return
        
        if not session_details["is_active"]:
            print(f"---- WS ERROR: Session {session_id_ws} is inactive for user {user_id}. Session name: {session_details['name']}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session is inactive.")
            return
        
        print(f"---- WS LOG: Session {session_id_ws} (Name: {session_details['name']}) found and is active.")

        cursor_verify.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id_ws, user_id))
        if not cursor_verify.fetchone():
            print(f"---- WS ERROR: User {user_id} ({user_email}) is not a participant of session {session_id_ws}.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Access denied to session.")
            return
        print(f"---- WS LOG: User {user_id} ({user_email}) is a participant of session {session_id_ws}. Access granted.")

    except sqlite3.Error as e_db_verify: # Catch specific sqlite3 errors
        print(f"---- WS ERROR: Database error during session verification for user {user_id}, session {session_id_ws}: {e_db_verify}")
        traceback.print_exc()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Server error during session verification (DB).")
        return
    except Exception as e_verify:
        print(f"---- WS ERROR: Unexpected error during session verification for user {user_id}, session {session_id_ws}: {e_verify}")
        traceback.print_exc()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Server error during session verification (General).")
        return
    finally:
        if conn_verify: conn_verify.close()

    try:
        await websocket.accept()
        print(f"---- WS SUCCESS: WebSocket connection accepted for user {user_id} ({user_email}), session {session_id_ws}, client {client_js_id}.")
    except Exception as accept_err:
        print(f"---- WS CRITICAL ERROR: Failed to accept WebSocket connection for user {user_id}, session {session_id_ws}: {accept_err}")
        traceback.print_exc()
        return 

    llm_provider_id_for_session: Optional[str] = None
    llm_model_id_for_session: Optional[str] = None
    user_api_key_for_session: Optional[str] = None 
    user_base_url_for_session: Optional[str] = None
    
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
            llm_provider_id_for_session = user_llm_prefs["selected_llm_provider_id"]
            llm_model_id_for_session = user_llm_prefs["selected_llm_model_id"]
            user_base_url_for_session = user_llm_prefs["selected_llm_base_url"] 
            encrypted_key = user_llm_prefs["user_llm_api_key_encrypted"]
            if encrypted_key and config.APP_SECRET_KEY:
                try:
                    user_api_key_for_session = encryption_utils.decrypt_data(encrypted_key)
                    if not user_api_key_for_session or not user_api_key_for_session.strip():
                        user_api_key_for_session = None 
                except Exception:
                    user_api_key_for_session = None
                    print(f"---- WS WARNING: User {user_id} - Failed to decrypt API key for session {session_id_ws}.")
            elif encrypted_key and not config.APP_SECRET_KEY:
                print(f"---- WS WARNING: User {user_id} - API key present but APP_SECRET_KEY not set, cannot decrypt for session {session_id_ws}.")

        if not llm_provider_id_for_session or not llm_model_id_for_session:
            print(f"---- WS INFO: User {user_id} has no LLM provider/model selected or incomplete. Using system defaults for session {session_id_ws}.")
            llm_provider_id_for_session = config.DEFAULT_LLM_PROVIDER_ID
            llm_model_id_for_session = config.DEFAULT_LLM_MODEL_ID
            if user_llm_prefs and llm_provider_id_for_session != user_llm_prefs["selected_llm_provider_id"]:
                user_api_key_for_session = None
                user_base_url_for_session = None
        print(f"---- WS LOG: LLM settings for session {session_id_ws} - Provider: '{llm_provider_id_for_session}', Model: '{llm_model_id_for_session}', HasUserAPIKey: {'Yes' if user_api_key_for_session else 'No'}, BaseURL: '{user_base_url_for_session if user_base_url_for_session else 'Default'}'")

    except Exception as e_settings:
        traceback.print_exc()
        print(f"---- WS ERROR: Error fetching LLM settings for user {user_id}, session {session_id_ws}: {e_settings}. Using system defaults.")
        llm_provider_id_for_session = config.DEFAULT_LLM_PROVIDER_ID
        llm_model_id_for_session = config.DEFAULT_LLM_MODEL_ID
        user_api_key_for_session = None 
        user_base_url_for_session = None 
    finally:
        if db_conn_settings: db_conn_settings.close()

    memory_for_session = state.get_memory_for_client(session_id_ws) 
    
    def load_memory_for_current_session_chain(_ignored_input_map=None):
        loaded_vars = memory_for_session.load_memory_variables({})
        return loaded_vars.get("history", [])

    chain_for_session: Optional[Any] = None 
    try:
        chain_for_session = llm.create_chain(
            provider_id=llm_provider_id_for_session,
            model_id=llm_model_id_for_session,
            memory_loader_func=load_memory_for_current_session_chain,
            api_key=user_api_key_for_session,
            base_url_override=user_base_url_for_session
        )
        if not chain_for_session:
            raise ValueError(f"LLM chain creation returned None for provider '{llm_provider_id_for_session}', model '{llm_model_id_for_session}'.")
        print(f"---- WS LOG: LLM chain successfully created for session {session_id_ws}.")
    except Exception as chain_init_error:
        traceback.print_exc()
        print(f"---- WS CRITICAL ERROR: Could not initialize LLM chain for session {session_id_ws}: {chain_init_error}")
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_json({"type": "error", "payload": {"message": f"Server error: Could not initialize chat with the selected model configuration ({str(chain_init_error)})."}})
            except Exception: pass 
        if websocket.client_state != WebSocketState.CLOSED :
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="LLM initialization failed.")
        return 

    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"---- WS INFO: Client {client_js_id} for session {session_id_ws} disconnected pre-receive.")
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
                        print(f"---- WS LOG: Received 'chat_message' from {user_id} for session {session_id_ws}, turn {turn_id}")
                        asyncio.create_task(
                            handle_chat_message( 
                                chain_for_session, memory_for_session, websocket, client_js_id, 
                                current_ws_user, session_id_ws, user_input, turn_id,
                                llm_provider_id_for_session, llm_model_id_for_session
                            )
                        )
                    else:
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_text("<ERROR>Invalid chat_message payload: 'user_input' or 'turn_id' missing.<EOS>")
                
                elif message_type == "run_code" and payload and payload.get("code_block_id"):
                    language = payload.get("language")
                    code = payload.get("code")
                    code_block_id = payload.get("code_block_id")
                    if language and code is not None:
                        print(f"---- WS LOG: Received 'run_code' for block {code_block_id} ({language}) from {user_id}")
                        asyncio.create_task(docker_utils.run_code_in_docker_stream(websocket, client_js_id, code_block_id, language, code))
                    else:
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_json({"type": "code_finished", "payload": {"code_block_id": code_block_id, "exit_code": -1, "error": "Invalid run_code payload: 'language' or 'code' missing."}})
                
                elif message_type == "save_code_result" and payload and payload.get("code_block_id"):
                    code_block_id = payload.get("code_block_id")
                    language = payload.get("language")
                    code_content = payload.get("code_content")
                    output_content = payload.get("output_content")
                    html_content = payload.get("html_content")
                    exit_code = payload.get("exit_code")
                    error_message = payload.get("error_message")
                    execution_status = payload.get("execution_status", "completed")
                    turn_id = payload.get("turn_id")
                    
                    database.save_code_execution_result(
                        session_id=session_id_ws,
                        code_block_id=code_block_id,
                        language=language,
                        code_content=code_content,
                        output_content=output_content,
                        html_content=html_content,
                        exit_code=exit_code,
                        error_message=error_message,
                        execution_status=execution_status,
                        turn_id=turn_id
                    )
                elif message_type == "stop_code" and payload and payload.get("code_block_id"):
                    code_block_id = payload.get("code_block_id")
                    print(f"---- WS LOG: Received 'stop_code' for block {code_block_id} from {user_id}")
                    asyncio.create_task(docker_utils.stop_docker_container(code_block_id))
                elif message_type == "save_code_content" and payload and payload.get("code_block_id"):
                    session_id_from_payload = payload.get("session_id")
                    code_block_id = payload.get("code_block_id")
                    language = payload.get("language")
                    code_content = payload.get("code_content")
                    
                    if session_id_from_payload == session_id_ws and code_content is not None:
                        print(f"---- WS LOG: Received 'save_code_content' for block {code_block_id} from {user_id}")
                        database.save_edited_code_content(session_id_ws, code_block_id, language, code_content)
                    else:
                        print(f"---- WS WARNING: Ignoring 'save_code_content' with mismatched session or missing content.")

                elif message_type == "stop_ai_stream" and payload:
                    stop_client_id = payload.get("client_id") 
                    stop_session_id = payload.get("session_id") 
                    stop_turn_id = payload.get("turn_id")
                    if stop_client_id == client_js_id and stop_session_id == session_id_ws and stop_turn_id is not None:
                        stream_id_to_stop = f"{stop_client_id}_{stop_turn_id}"
                        print(f"---- WS LOG: Received 'stop_ai_stream' for stream_id: {stream_id_to_stop} from {user_id}")
                        await state.signal_stop_ai_stream(stream_id_to_stop)
                    else:
                        print(f"---- WS WARNING: Ignoring 'stop_ai_stream' with mismatched identifiers. Client: {stop_client_id}/{client_js_id}, Session: {stop_session_id}/{session_id_ws}")

                elif message_type == "code_input" and payload and payload.get("code_block_id"):
                    code_block_id = payload.get("code_block_id")
                    user_input_text = payload.get("input", "")
                    print(f"---- WS LOG: Received 'code_input' for block {code_block_id} from {user_id}: '{user_input_text.strip()}'")
                    asyncio.create_task(docker_utils.send_input_to_container(code_block_id, user_input_text))

                else: 
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_text(f"<ERROR>Unknown command type received: {message_type}<EOS>")
            
            except json.JSONDecodeError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text("<ERROR>Invalid message format. Expected JSON.<EOS>")
            except Exception as handler_exc: 
                traceback.print_exc()
                print(f"---- WS ERROR: Error handling received data for session {session_id_ws}, client {client_js_id}: {handler_exc}")
                if websocket.client_state == WebSocketState.CONNECTED:
                    try:
                        await websocket.send_text(f"<ERROR>Server error processing your request: {str(handler_exc)}<EOS>")
                    except Exception: pass

    except WebSocketDisconnect:
        print(f"---- WS INFO: Client {client_js_id} (User {user_id}, {user_email}) disconnected from session {session_id_ws}.")
    except Exception as e_ws_loop: 
        traceback.print_exc()
        print(f"---- WS CRITICAL ERROR: Unexpected error in main WebSocket loop for session {session_id_ws}, client {client_js_id}: {e_ws_loop}")
        if websocket.client_state == WebSocketState.CONNECTED:
            try: await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Unexpected server error in WebSocket.")
            except Exception: pass 
    finally:
        print(f"---- WS INFO: Cleaning up resources for client {client_js_id}, session {session_id_ws}.")
        # Ensure docker_utils.cleanup_client_containers is an async function if awaited
        await docker_utils.cleanup_client_containers(client_js_id) 
        
        if websocket.client_state == WebSocketState.CONNECTED:
            try: 
                print(f"---- WS INFO: Explicitly closing WebSocket connection for client {client_js_id}, session {session_id_ws}.")
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            except Exception: pass

@app.delete("/api/sessions/{session_id}/edited-blocks/{code_block_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Code Execution"])
async def delete_edited_code_block_route(
    request: Request,
    session_id: str = FastApiPath(..., description="The session ID."),
    code_block_id: str = FastApiPath(..., description="The code block ID to restore."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    
    # User access verification
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user['id']))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this session's data.")
    conn.close()

    success = database.delete_edited_code_block(session_id, code_block_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to restore code block.")
    
    # Invalidate the in-memory cache for this session
    state.remove_memory_for_client(session_id)
    
    return

@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Sessions"])
async def delete_session_route(
    request: Request, # For CSRF validation
    session_id: str = FastApiPath(..., description="The ID of the session to delete."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user), # Ensures user is authenticated
    csrf_protect: CsrfProtect = Depends() # CSRF protection dependency
):
    """
    Deletes a specific chat session (marks it as inactive).
    Requires the user to be authenticated and provide a valid CSRF token.
    """
    print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Attempt by user ID: {user.get('id')} ----")
    
    # Perform CSRF validation. This is crucial for state-changing operations.
    await csrf_protect.validate_csrf(request)
    print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - CSRF validation PASSED ----")

    user_id = user.get('id') # Get the ID of the authenticated user
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Fetching session data from DB.")
        # Fetch necessary session details, ensuring it's active
        cursor.execute("SELECT host_user_id, name FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        session_data = cursor.fetchone()

        if not session_data:
            # If not found active, check if it exists at all or was already inactive
            cursor.execute("SELECT id, name, is_active FROM sessions WHERE id = ?", (session_id,))
            already_exists_data = cursor.fetchone()
            if not already_exists_data:
                print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Session ID truly not found in database.")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
            else:
                # Session exists but was already inactive. Consider this a "successful" delete.
                # Use dictionary-style access for sqlite3.Row
                name_val = already_exists_data['name'] if 'name' in already_exists_data.keys() else 'N/A'
                is_active_val = already_exists_data['is_active'] if 'is_active' in already_exists_data.keys() else 'N/A'
                print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Session found but is already inactive. Name: {name_val}, Active: {is_active_val}")
                conn.commit() # Commit if any prior transaction was started, though unlikely here
                return # Return 204 as it's effectively "deleted" or already in that state
        
        print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Session data fetched: Type={type(session_data)}, Keys={list(session_data.keys()) if hasattr(session_data, 'keys') else 'N/A (not dict-like)'}")

        # Use dictionary-style access for sqlite3.Row
        # Ensure 'name' is in keys before accessing, or handle potential None if name can be NULL
        session_name_from_db = session_data["name"] if "name" in session_data.keys() and session_data["name"] is not None else None
        # session_host_id = session_data["host_user_id"] # Access directly if needed for permission check

        # Example permission check (if you want to re-enable it):
        # if session_host_id != user_id:
        #     print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - User {user_id} is not host ({session_host_id}). Forbidden.")
        #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this session.")

        current_time_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        deleted_session_name = f"Deleted: {session_name_from_db if session_name_from_db else session_id[:8]} ({current_time_iso})"
        
        print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Updating session to inactive. New name: '{deleted_session_name}'")
        cursor.execute(
            "UPDATE sessions SET is_active = 0, name = ?, last_accessed_at = ? WHERE id = ?",
            (deleted_session_name, current_time_iso, session_id)
        )
        
        if cursor.rowcount == 0:
            # This case implies the session was active when fetched but couldn't be updated (e.g., race condition or DB issue).
            print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Session was found active but UPDATE operation affected 0 rows. This is unexpected.")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update session state during deletion.")

        conn.commit()
        print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Session marked as inactive by user ID: {user_id}.")

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as e_db:
        if conn: conn.rollback()
        print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - SQLite Error ----")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error processing delete request: {e_db}")
    except Exception as e_general: # This will catch other errors like KeyError or IndexError
        if conn: conn.rollback()
        print(f"---- SERVER LOG: DELETE /api/sessions/{session_id} - Unexpected General Error ----")
        traceback.print_exc() 
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An unexpected server error occurred: {e_general}")
    finally:
        if conn:
            conn.close()

# --- Application Startup and Static Files ---

@app.on_event("startup")
async def startup_event():
    print("Application startup: Initializing database...")
    # Ensure the database directory exists
    db_parent_dir = config.DATABASE_PATH.parent
    if not db_parent_dir.exists():
        db_parent_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created database directory: {db_parent_dir}")
    database.init_db() # Initializes tables if they don't exist
    print("Database initialization check complete.")

    # Docker client check (optional, for code execution feature)
    docker_client = None
    try:
        docker_client = docker_utils.get_docker_client() 
        if docker_client is None:
            print("WARNING: Could not get Docker client via get_docker_client(). Code execution features might be disabled or limited.")
        else:
            # Perform a quick check like docker_client.ping()
            if docker_client.ping():
                print("Docker client confirmed available and responsive at startup.")
            else:
                print("WARNING: Docker client obtained, but ping failed. Docker daemon might not be running correctly. Code execution may fail.")
    except Exception as e_docker:
        print(f"WARNING: Could not connect to Docker daemon at startup: {e_docker}")
        print("Code execution via Docker will be unavailable if Docker is not running or misconfigured.")

    # Default LLM check (optional, for system health check)
    try:
        if config.DEFAULT_LLM_PROVIDER_ID and config.DEFAULT_LLM_MODEL_ID:
            print(f"Attempting to initialize default LLM: Provider='{config.DEFAULT_LLM_PROVIDER_ID}', Model='{config.DEFAULT_LLM_MODEL_ID}'")
            # This just gets the model class/config, doesn't make a call unless get_model itself does.
            # For a true health check, you might try a very short, non-streaming test call if feasible.
            default_model_instance = llm.get_model( 
                provider_id=config.DEFAULT_LLM_PROVIDER_ID,
                model_id=config.DEFAULT_LLM_MODEL_ID
                # No API key or base URL here, assumes system config if needed by default model
            )
            if default_model_instance:
                print("Default LLM model configuration seems accessible at startup.")
                # To test further, you might try creating a chain:
                # llm.create_chain(config.DEFAULT_LLM_PROVIDER_ID, config.DEFAULT_LLM_MODEL_ID, memory_loader_func=lambda: [])
                # print("Default LLM chain creation test successful.")
            else:
                print(f"CRITICAL WARNING: Could not get default LLM model instance (Provider: {config.DEFAULT_LLM_PROVIDER_ID}, Model: {config.DEFAULT_LLM_MODEL_ID}). Check LLM config and server environment.")
        else:
            print("INFO: No default LLM provider/model ID configured for startup check.")
    except Exception as e_llm_startup:
        print(f"CRITICAL ERROR during startup default LLM check: {e_llm_startup}")
        traceback.print_exc()


# Static file mounting
# Ensure config.STATIC_DIR is an absolute path or correctly relative to the app's root.
if not isinstance(config.STATIC_DIR, Path) or not config.STATIC_DIR.is_dir():
    # Try to resolve if it's a string relative to the current file's directory
    current_file_dir = Path(__file__).parent
    potential_static_dir = (current_file_dir / str(config.STATIC_DIR)).resolve()
    if potential_static_dir.is_dir():
        # Correct config.STATIC_DIR to be the resolved Path object
        # This assumes config.py might define STATIC_DIR as a relative string.
        # It's better if config.py defines it as an absolute Path.
        print(f"Warning: config.STATIC_DIR was not a valid Path object. Resolved '{config.STATIC_DIR}' to '{potential_static_dir}'. Consider defining STATIC_DIR as a Path in config.py.")
        # config.STATIC_DIR = potential_static_dir # This would modify config module, be careful.
        # Better to use the resolved path directly for mounting.
        _resolved_static_dir = potential_static_dir
    else:
        print(f"CRITICAL ERROR: Base static directory invalid or not found: '{config.STATIC_DIR}' (also checked '{potential_static_dir}'). Application may not serve frontend assets correctly.")
        # sys.exit(1) # Exiting might be too drastic if other parts can run.
        _resolved_static_dir = None # Signal that static serving might fail
else:
    _resolved_static_dir = config.STATIC_DIR


if _resolved_static_dir:
    dist_dir = _resolved_static_dir / "dist"
    if not dist_dir.is_dir():
        print(f"WARNING: Bundled assets directory not found: '{dist_dir}'. Ensure frontend assets are built (e.g., 'npm run build' or similar) and placed in the 'dist' subdirectory of your static path.")
        # Frontend might not work correctly.
    else:
        try:
            app.mount("/dist", StaticFiles(directory=dist_dir, html=False), name="dist_assets") # html=False for /dist unless it serves an index.html
            print(f"Mounted bundled assets from '{dist_dir}' at '/dist'")
        except Exception as e_mount_dist:
            print(f"ERROR mounting /dist static files from '{dist_dir}': {e_mount_dist}")

    # Mount the root static directory itself for files like login.html, _sidebar.html, etc.
    # This should come AFTER more specific mounts if there are overlaps (though /static and /dist are usually distinct).
    try:
        # If login.html, etc., are directly in STATIC_DIR and not served by specific routes.
        # However, we are serving login.html, etc., via specific routes now to inject CSRF.
        # So, this mount is for other assets like CSS (if not in /dist), JS helpers (app-ui.js), images.
        app.mount("/static", StaticFiles(directory=_resolved_static_dir, html=True), name="static_general") # html=True if it can serve index.html from subdirs
        print(f"Mounted general static files from '{_resolved_static_dir}' at '/static'")
    except Exception as e_mount_static:
        print(f"ERROR mounting /static general files from '{_resolved_static_dir}': {e_mount_static}")
else:
    print("CRITICAL: Static directory not resolved. Static file serving will likely fail.")


# Print registered routes (useful for debugging, should be near the end)
if config.DEBUG_MODE: # Only print in debug mode
    print("\n---- FastAPI Registered Routes (End of Module Definition) ----")
    for route_item in app.routes:
        if hasattr(route_item, "path"):
            methods_str = ", ".join(sorted(list(getattr(route_item, 'methods', {})))) or ( "WS" if isinstance(route_item, WebSocket) else "N/A")
            route_name = getattr(route_item, 'name', 'N/A')
            print(f"  Path: {route_item.path}, Name: {route_name}, Methods: {{{methods_str}}}")
    print("-------------------------------\n")