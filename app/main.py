import os
import sys
import traceback
from pathlib import Path
import asyncio
import uuid
import tempfile
import json
import sqlite3
import uuid
from urllib.parse import urlparse
import datetime
from typing import Optional, Dict, Any, List


from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Form,
    Depends,
    Response as FastAPIResponse,
    Path as FastApiPath,
    Body,
    status
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    JSONResponse,
    FileResponse,
    Response
)
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState

from pydantic import HttpUrl

from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

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
from . import project_utils

app = FastAPI(title="Tesseracs Chat CSRF Example")

@CsrfProtect.load_config
def get_csrf_config():
    loaded_secret_from_config = getattr(config, 'CSRF_PROTECT_SECRET_KEY', None)
    final_csrf_secret = None
    default_fallback_secret = "a_very_secure_fallback_secret_key_must_be_at_least_32_bytes_long_0123456789"

    if isinstance(loaded_secret_from_config, str) and len(loaded_secret_from_config) >= 32:
        final_csrf_secret = loaded_secret_from_config
    else:
        final_csrf_secret = default_fallback_secret
    
    return [
        ("secret_key", final_csrf_secret),
        ("cookie_key", "fastapi-csrf-token"),
        ("header_name", "X-CSRF-Token"),
        ("httponly", True),
    ]

@app.exception_handler(CsrfProtectError)
async def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
    return JSONResponse(
        status_code=exc.status_code, 
        content={"detail": exc.message if exc.message else "CSRF Validation Failed"}
    )

async def serve_html_with_csrf(
    file_path: Path,
    request: Request,
    csrf_protect: CsrfProtect,
    replacements: Optional[Dict[str, str]] = None
) -> HTMLResponse:
    if not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Resource {file_path.name} not found.")

    html_content_original = ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html_content_original = f.read()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error loading content for {file_path.name}.")

    _raw_token_csrf, signed_token_for_cookie = csrf_protect.generate_csrf_tokens()
    if not _raw_token_csrf or not signed_token_for_cookie:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="CSRF token generation failed on server.")

    html_content_processed = html_content_original
    
    csrf_placeholder = "%%CSRF_TOKEN_RAW%%"
    if csrf_placeholder in html_content_processed:
        html_content_processed = html_content_processed.replace(csrf_placeholder, _raw_token_csrf)
    
    if replacements:
        for key, value in replacements.items():
            if key in html_content_processed:
                html_content_processed = html_content_processed.replace(key, str(value))

    response = HTMLResponse(content=html_content_processed)
    try:
        csrf_protect.set_csrf_cookie(response=response, csrf_signed_token=signed_token_for_cookie)
    except Exception as e:
        traceback.print_exc()

    return response

@app.patch("/api/sessions/{session_id}", response_model=models.SessionResponseModel, tags=["Sessions"])
async def update_session_name(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the session to update."),
    update_data: models.SessionUpdateRequest = Body(...),
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    user_id = user['id']
    new_name = update_data.name

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to modify this session.")

        cursor.execute(
            "UPDATE sessions SET name = ?, last_accessed_at = datetime('now', 'utc') WHERE id = ?",
            (new_name, session_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
        
        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        updated_session_row = cursor.fetchone()
        conn.commit()

        if not updated_session_row:
             raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found after update.")

        state.remove_memory_for_client(session_id)

        session_data = dict(updated_session_row)
        return models.SessionResponseModel(**session_data)

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error while updating session.")
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred.")
    finally:
        if conn: conn.close()

@app.get("/login", response_class=HTMLResponse, name="get_login_page_route", tags=["Pages"])
async def get_login_page_route(
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(auth.get_current_user), 
    csrf_protect: CsrfProtect = Depends()
) -> Response:
    if user:
        session_choice_url = request.url_for("get_session_choice_page")
        return RedirectResponse(url=str(session_choice_url), status_code=status.HTTP_302_FOUND)

    login_html_path = config.STATIC_DIR / "login.html"
    return await serve_html_with_csrf(login_html_path, request, csrf_protect)

@app.get("/", response_class=HTMLResponse, name="get_session_choice_page", tags=["Pages"])
async def get_session_choice_page_route(
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(auth.get_current_user),
    csrf_protect: CsrfProtect = Depends()
) -> Response:
    if user is None:
        login_url = request.url_for("get_login_page_route")
        return RedirectResponse(url=str(login_url), status_code=status.HTTP_302_FOUND)

    session_choice_html_path = config.STATIC_DIR / "session-choice.html"
    replacements = {"[User Name]": user.get("name", "User")}
    return await serve_html_with_csrf(session_choice_html_path, request, csrf_protect, replacements=replacements)

@app.get("/chat/{session_id}", response_class=HTMLResponse, name="get_chat_page_for_session", tags=["Pages"])
async def get_chat_page_for_session(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the chat session to load."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    user_id = user['id']
    session_name_for_html = "Chat Session"
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, name FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        session_row = cursor.fetchone()
        
        if not session_row:
            raise HTTPException(status_code=404, detail="Chat session not found or is inactive.")
        
        session_name_for_html = session_row["name"]

        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="You do not have access to this chat session.")

        current_time_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute("UPDATE sessions SET last_accessed_at = ? WHERE id = ?", (current_time_utc_iso, session_id))
        conn.commit()

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except Exception as e:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error verifying session access for chat page.")
    finally:
        if conn: conn.close()

    chat_html_path = config.STATIC_DIR / "chat-session.html"
    replacements = {"%%SESSION_NAME_PLACEHOLDER%%": utils.escape_html(session_name_for_html)}
    return await serve_html_with_csrf(chat_html_path, request, csrf_protect, replacements=replacements)

@app.get("/settings", response_class=HTMLResponse, name="get_settings_page", tags=["Pages"])
async def get_settings_page(
    request: Request,
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    settings_html_path = config.STATIC_DIR / "settings.html"
    return await serve_html_with_csrf(settings_html_path, request, csrf_protect)

@app.post("/check_email", response_model=models.EmailCheckResponse, tags=["Authentication"])
async def check_email_exists_route(
    request: Request,
    request_data: models.EmailCheckRequest,
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    email_to_check = request_data.email.lower().strip()
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM users WHERE email = ? AND is_active = 1", (email_to_check,))
        user_row = cursor.fetchone()
        if user_row:
            return models.EmailCheckResponse(exists=True, user_name=user_row["name"])
        else:
            return models.EmailCheckResponse(exists=False, user_name=None)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error checking email.")
    finally:
        if conn: conn.close()

@app.post("/token", response_model=models.Token, tags=["Authentication"])
async def login_for_access_token(
    request: Request,
    response: FastAPIResponse,
    form_data: OAuth2PasswordRequestForm = Depends(),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    email = form_data.username.lower().strip()
    password = form_data.password
    conn = None
    try:
        conn = database.get_db_connection()
        user_dict = auth.authenticate_user_from_db(conn, email, password)
        if not user_dict:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")
        
        if not user_dict["is_active"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")
        
        session_token_raw = await auth.create_user_session(response=response, user_id=user_dict["id"])
        
        _raw_token_new, signed_token_for_cookie_new = csrf_protect.generate_csrf_tokens()
        csrf_protect.set_csrf_cookie(response=response, csrf_signed_token=signed_token_for_cookie_new)
        
        return models.Token(
            access_token=session_token_raw, 
            token_type="bearer",
            user_id=user_dict["id"], 
            user_name=user_dict["name"], 
            user_email=user_dict["email"]
        )
    except HTTPException as http_exc:
        raise http_exc
    finally:
        if conn: conn.close()

@app.post("/sessions/create", response_model=models.SessionResponseModel, tags=["Sessions"])
async def create_new_session_route(
    request: Request,
    session_data: models.HostSessionRequest,
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    print("\n--- LOG: 1. `create_new_session_route` endpoint initiated. ---")
    await csrf_protect.validate_csrf(request)
    host_user_id = user["id"]
    new_session_id = str(uuid.uuid4())
    passcode_hash = None

    if session_data.access_level in ['protected', 'unlisted'] and not session_data.passcode:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A passcode is required for protected or unlisted sessions.")
    
    if session_data.passcode:
        passcode_hash = auth.get_password_hash(session_data.passcode)

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        if session_data.access_level in ['public', 'protected']:
            cursor.execute(
                "SELECT id FROM sessions WHERE name = ? AND access_level IN ('public', 'protected') AND is_active = 1",
                (session_data.name,)
            )
            if cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A public or protected session with this name already exists.")

        cursor.execute(
            """INSERT INTO sessions (id, host_user_id, name, access_level, passcode_hash, created_at, last_accessed_at) 
               VALUES (?, ?, ?, ?, ?, datetime('now', 'utc'), datetime('now', 'utc'))""",
            (new_session_id, host_user_id, session_data.name, session_data.access_level, passcode_hash)
        )

        cursor.execute(
            "INSERT INTO session_participants (session_id, user_id) VALUES (?, ?)",
            (new_session_id, host_user_id)
        )
        
        conn.commit()
        print(f"--- LOG: 2. Session '{new_session_id}' created and saved to database. ---")

        cursor.execute("SELECT * FROM sessions WHERE id = ?", (new_session_id,))
        new_session_row = cursor.fetchone()
        
        session_dict = dict(new_session_row)

        if session_dict['access_level'] in ['public', 'protected']:
            print(f"--- LOG: 3. Session is public/protected. Preparing to broadcast to lobby. ---")
            broadcast_payload = models.SessionResponseModel(
                id=session_dict['id'],
                name=session_dict['name'],
                created_at=session_dict['created_at'],
                last_active=session_dict['last_accessed_at'],
                host_user_id=session_dict['host_user_id'],
                is_member=False,
                access_level=session_dict['access_level']
            )
            await state.broadcast_to_lobby({
                "type": "new_public_session",
                "payload": broadcast_payload.model_dump()
            })
        
        session_dict['is_member'] = True
        print(f"--- LOG: 5. Returning successful response to host client. ---")
        return models.SessionResponseModel(**session_dict)

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error creating session.")
    finally:
        if conn: conn.close()

@app.post("/register", response_model=models.RegistrationResponse, tags=["Authentication"])
async def register_new_user(
    request_data: models.RegistrationRequest,
    request: Request,
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    email = request_data.email.lower().strip()
    name = request_data.name.strip()

    if not name or not utils.is_valid_email(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid name or email format.")

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This email address is already registered.")
        
        plain_password = database.generate_secure_token(12)
        hashed_password = auth.get_password_hash(plain_password)
        
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (name, email, hashed_password, True)
        )
        user_id = cursor.lastrowid
        if not user_id:
            conn.rollback()
            raise sqlite3.Error("User insertion failed to return an ID.")

        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        parsed_base_url = urlparse(config.BASE_URL)
        parsed_login_route_url = urlparse(login_url_from_fastapi)
        login_page_url = parsed_base_url._replace(path=parsed_login_route_url.path).geturl()
        
        email_sent = await email_utils.send_registration_password_email(
            recipient_email=email, recipient_name=name, generated_password=plain_password, login_url=login_page_url
        )
        
        if not email_sent:
            conn.commit()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account created, but there was an issue sending your password email.")
        
        conn.commit()
        return models.RegistrationResponse(message="Account created successfully! Your password has been sent to your email address.")

    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    finally:
        if conn: conn.close()

@app.post("/forgot_password", response_model=models.ForgotPasswordResponse, tags=["Authentication"])
async def handle_forgot_password(
    request_data: models.ForgotPasswordRequest,
    request: Request,
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    email = request_data.email.lower().strip()
    generic_response = models.ForgotPasswordResponse(message="If an account with this email exists and is active, a password reset email has been sent.")

    if not utils.is_valid_email(email):
        return generic_response

    client_ip = request.client.host if request.client else "unknown_ip"
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO password_reset_attempts (email, ip_address, attempted_at) VALUES (?, ?, datetime('now'))",
            (email, client_ip)
        )
        
        time_window_start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=config.FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS)
        cursor.execute(
            "SELECT COUNT(*) FROM password_reset_attempts WHERE email = ? AND attempted_at >= ?",
            (email, time_window_start.isoformat())
        )
        attempt_count_row = cursor.fetchone()
        recent_attempts = attempt_count_row[0] if attempt_count_row else 0

        if recent_attempts > config.FORGOT_PASSWORD_ATTEMPT_LIMIT:
            conn.commit()
            return generic_response

        cursor.execute("SELECT id, name FROM users WHERE email = ? AND is_active = 1", (email,))
        user_row = cursor.fetchone()

        if not user_row:
            conn.commit()
            return generic_response

        user_id = user_row["id"]
        user_name = user_row["name"]

        new_plain_password = database.generate_secure_token(12)
        new_hashed_password = auth.get_password_hash(new_plain_password)

        cursor.execute("UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE id = ?", (new_hashed_password, user_id))
        
        login_url_from_fastapi = str(request.url_for('get_login_page_route'))
        parsed_base_url = urlparse(config.BASE_URL)
        parsed_login_route_url = urlparse(login_url_from_fastapi)
        login_page_url = parsed_base_url._replace(path=parsed_login_route_url.path).geturl()
        
        email_sent = await email_utils.send_password_reset_email(
            recipient_email=email, recipient_name=user_name, new_password=new_plain_password, login_url=login_page_url
        )

        if not email_sent:
            conn.rollback()
            return generic_response
        
        conn.commit()
        return generic_response
    except Exception as e:
        if conn: conn.rollback()
        return generic_response
    finally:
        if conn: conn.close()

async def _ensure_csrf_for_cookie_auth(request: Request, csrf_protect: CsrfProtect):
    auth_header = request.headers.get("authorization")
    is_bearer_auth = auth_header and auth_header.lower().startswith("bearer ")
    if not is_bearer_auth:
        await csrf_protect.validate_csrf(request)

@app.put("/api/me/llm-settings", response_model=models.UserLLMSettingsResponse, tags=["User Account Management", "LLM Configuration"])
async def update_user_llm_settings(
    request: Request,
    settings_update: models.UserLLMSettingsUpdateRequest,
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await _ensure_csrf_for_cookie_auth(request, csrf_protect)
    user_id = current_user["id"]
    conn = None

    if settings_update.selected_llm_provider_id:
        provider_config_info = config.LLM_PROVIDERS.get(settings_update.selected_llm_provider_id)
        if not provider_config_info:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid provider ID: {settings_update.selected_llm_provider_id}")
        
        available_models_for_provider = provider_config_info.get("available_models", [])
        if settings_update.selected_llm_model_id:
            model_found = any(model.get("model_id") == settings_update.selected_llm_model_id for model in available_models_for_provider)
            if not model_found:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Model ID not valid for provider.")
        elif available_models_for_provider:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"A model ID must be selected for this provider.")

    encrypted_api_key_to_store: Optional[str] = None
    if settings_update.user_llm_api_key is not None:
        if settings_update.user_llm_api_key == "":
            encrypted_api_key_to_store = None
        else:
            if not config.APP_SECRET_KEY:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="API key encryption service is unavailable.")
            encrypted_api_key_to_store = encryption_utils.encrypt_data(settings_update.user_llm_api_key)
            if not encrypted_api_key_to_store:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to secure API key.")
    
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?", (user_id,))
        current_db_settings = cursor.fetchone()
        if not current_db_settings:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User settings record not found.")

        final_provider_id = settings_update.selected_llm_provider_id if settings_update.selected_llm_provider_id is not None else current_db_settings["selected_llm_provider_id"]
        final_model_id = settings_update.selected_llm_model_id if settings_update.selected_llm_model_id is not None else current_db_settings["selected_llm_model_id"]
        final_api_key_encrypted = encrypted_api_key_to_store if settings_update.user_llm_api_key is not None else current_db_settings["user_llm_api_key_encrypted"]
        final_base_url_str = str(settings_update.selected_llm_base_url) if settings_update.selected_llm_base_url else None
        
        cursor.execute(
            """UPDATE users SET
               selected_llm_provider_id = ?, selected_llm_model_id = ?, user_llm_api_key_encrypted = ?,
               selected_llm_base_url = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (final_provider_id, final_model_id, final_api_key_encrypted, final_base_url_str, user_id)
        )
        conn.commit()

        has_user_api_key = bool(final_api_key_encrypted)
        updated_base_url_obj = HttpUrl(final_base_url_str) if final_base_url_str else None
        
        return models.UserLLMSettingsResponse(
            selected_llm_provider_id=final_provider_id,
            selected_llm_model_id=final_model_id,
            has_user_api_key=has_user_api_key,
            selected_llm_base_url=updated_base_url_obj
        )
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while updating settings.")
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
    user_name = current_user.get("name", "User")
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user_record = cursor.fetchone()
        if not user_record or not auth.verify_password(payload.current_password, user_record["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect current password.")

        new_plain_password = database.generate_secure_token(12)
        new_hashed_password = auth.get_password_hash(new_plain_password)
        
        cursor.execute("UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE id = ?", (new_hashed_password, user_id))
        
        login_url = request.url_for('get_login_page_route')
        email_sent = await email_utils.send_password_reset_email(
            recipient_email=user_email, recipient_name=user_name, new_password=new_plain_password, login_url=str(login_url)
        )
        if not email_sent:
            conn.rollback()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Password change rolled back due to email failure.")

        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            "UPDATE auth_tokens SET used_at = ?, expires_at = ? WHERE user_id = ? AND token_type = 'session' AND used_at IS NULL",
            (now_utc_iso, now_utc_iso, user_id)
        )
        conn.commit()
        return models.RegeneratePasswordResponse(message="Password regenerated successfully. Please check your email.")
    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
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
    new_email_normalized = payload.new_email.lower().strip()

    if not utils.is_valid_email(new_email_normalized):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid new email address format.")
    
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user_record = cursor.fetchone()
        if not user_record or not auth.verify_password(payload.current_password, user_record["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect current password.")

        cursor.execute("SELECT id FROM users WHERE email = ? AND id != ?", (new_email_normalized, user_id))
        if cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This email address is already in use.")

        cursor.execute("UPDATE users SET email = ?, updated_at = datetime('now') WHERE id = ?", (new_email_normalized, user_id))
        
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            "UPDATE auth_tokens SET used_at = ?, expires_at = ? WHERE user_id = ? AND token_type = 'session' AND used_at IS NULL",
            (now_utc_iso, now_utc_iso, user_id)
        )
        conn.commit()
        return models.UpdateEmailResponse(message="Email updated. You will be logged out.", new_email=new_email_normalized)
    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
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
    
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user_record = cursor.fetchone()
        if not user_record or not auth.verify_password(update_data.current_password, user_record["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect current password.")

        cursor.execute("UPDATE users SET name = ?, updated_at = datetime('now') WHERE id = ?", (new_name_stripped, user_id))
        conn.commit()
        return models.UpdateNameResponse(message="Name updated successfully.", new_name=new_name_stripped)
    except HTTPException as http_exc:
        if conn: conn.rollback()
        raise http_exc
    finally:
        if conn: conn.close()

@app.get("/api/llm/providers", response_model=List[models.LLMProviderDetail], tags=["LLM Configuration"])
async def list_llm_providers(
    current_user: Optional[Dict[str, Any]] = Depends(auth.get_current_user)
):
    response_providers = []
    for provider_id, provider_data in config.LLM_PROVIDERS.items():
        provider_runtime_config = config.get_provider_config(provider_id)
        if not provider_runtime_config:
            continue

        api_key_env_var_name = provider_runtime_config.get("api_key_env_var_name")
        is_system_key_configured = bool(api_key_env_var_name and os.getenv(api_key_env_var_name))
        
        provider_type_can_use_key = provider_id in config.PROVIDERS_TYPICALLY_USING_API_KEYS or bool(api_key_env_var_name)
        needs_api_key_from_user = provider_type_can_use_key and not is_system_key_configured
        can_accept_user_api_key = provider_id in config.PROVIDERS_ALLOWING_USER_KEYS_EVEN_IF_SYSTEM_CONFIGURED or needs_api_key_from_user
        
        response_providers.append(
            models.LLMProviderDetail(
                id=provider_id,
                display_name=provider_data.get("display_name", provider_id),
                type=provider_runtime_config.get("type", "unknown"),
                is_system_configured=is_system_key_configured or not provider_type_can_use_key,
                can_accept_user_api_key=can_accept_user_api_key,
                needs_api_key_from_user=needs_api_key_from_user,
                available_models=[models.LLMAvailableModel(**m) for m in provider_data.get("available_models", [])],
                can_accept_user_base_url=provider_runtime_config.get("type") == "openai_compatible_server"
            )
        )
    return response_providers

# In app/main.py

@app.get("/api/me/llm-settings", response_model=models.UserLLMSettingsResponse, tags=["User Account Management", "LLM Configuration"])
async def get_user_llm_settings(
    current_user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    user_id = current_user["id"]
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?", (user_id,))
        settings = cursor.fetchone()
        if not settings:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User LLM settings not found.")
        
        has_key = bool(settings["user_llm_api_key_encrypted"])
        base_url = HttpUrl(settings["selected_llm_base_url"]) if settings["selected_llm_base_url"] else None
        
        provider_id = settings["selected_llm_provider_id"]
        is_ready = False
        if provider_id and settings["selected_llm_model_id"]:
            provider_config_details = config.get_provider_config(provider_id)
            if provider_config_details:
                provider_type_needs_key = provider_id in config.PROVIDERS_TYPICALLY_USING_API_KEYS
                if not provider_type_needs_key:
                    is_ready = True
                else:
                    api_key_env_var = provider_config_details.get("api_key_env_var_name")
                    is_system_key_set = bool(api_key_env_var and os.getenv(api_key_env_var))
                    if has_key or is_system_key_set:
                        is_ready = True

        return models.UserLLMSettingsResponse(
            selected_llm_provider_id=settings["selected_llm_provider_id"],
            selected_llm_model_id=settings["selected_llm_model_id"],
            has_user_api_key=has_key,
            selected_llm_base_url=base_url,
            is_llm_ready=is_ready
        )
    finally:
        if conn: conn.close()

@app.get("/logout", tags=["Authentication"])
async def logout_route(
    request: Request,
    response: FastAPIResponse,
    session_token_value: Optional[str] = Depends(auth.cookie_scheme)
):
    await auth.logout_user(response, session_token_value)
    redirect_url = request.url_for('get_login_page_route')
    return RedirectResponse(url=str(redirect_url), status_code=status.HTTP_302_FOUND)

@app.get("/api/me", response_model=models.UserResponseModel, tags=["Users"])
async def get_current_user_details(
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    return models.UserResponseModel(id=user["id"], name=user["name"], email=user["email"])

@app.get("/api/sessions/{session_id}/code-results", response_model=List[Dict[str, Any]], tags=["Code Execution"])
async def get_session_code_execution_results(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch code results for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
) -> List[Dict[str, Any]]:
    user_id = user.get('id')
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    conn.close()
    return database.get_code_execution_results(session_id)


@app.get("/api/sessions/{session_id}/edited-blocks", response_model=Dict[str, str], tags=["Sessions"])
async def get_session_edited_blocks(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch edited code blocks for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user['id']))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    conn.close()
    return database.get_edited_code_blocks(session_id)

@app.get("/api/sessions/{session_id}/messages", response_model=List[models.MessageItem], tags=["Messages"])
async def get_chat_messages_for_session(
    session_id: str = FastApiPath(..., description="The ID of the session to fetch messages for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
) -> List[models.MessageItem]:
    user_id = user.get('id')
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        
        cursor.execute("SELECT * FROM chat_messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,))
        
        messages_to_return = []
        rows = cursor.fetchall()
        for row in rows:
            msg_dict = dict(row)
            project_data_for_model = None 

            # If the message content might contain file blocks, parse them
            if msg_dict.get("content"):
                parsed_files = project_utils.parse_file_blocks(msg_dict["content"])
                if parsed_files:
                    # Construct the project_data dictionary that the Pydantic model expects
                    project_data_for_model = {
                        "name": f"Project from message {msg_dict['id']}", # A placeholder name
                        "files": parsed_files
                    }
            
            # Add the key for Pydantic validation, even if it's None
            msg_dict["project_data"] = project_data_for_model
            
            messages_to_return.append(models.MessageItem(**msg_dict))
        return messages_to_return

    finally:
        if conn: conn.close()

@app.post("/api/sessions/{session_id}/join", status_code=status.HTTP_200_OK, tags=["Sessions"])
async def join_session(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the session to join."),
    payload: models.JoinSessionRequest = Body(None), # Can be empty or contain passcode
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    user_id = user['id']
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT access_level, passcode_hash FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        session = cursor.fetchone()

        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or is not active.")

        if session["access_level"] == 'protected':
            if not payload or not payload.passcode:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A passcode is required to join this protected session.")
            if not auth.verify_password(payload.passcode, session["passcode_hash"]):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Incorrect passcode.")
        elif session["access_level"] != 'public':
             raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This session is private and cannot be joined.")

        cursor.execute(
            "INSERT OR IGNORE INTO session_participants (session_id, user_id) VALUES (?, ?)",
            (session_id, user_id)
        )
        conn.commit()
        chat_url = request.url_for("get_chat_page_for_session", session_id=session_id)
        return {"redirect_url": str(chat_url)}
    except sqlite3.Error as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error while trying to join session.")
    finally:
        if conn: conn.close()

@app.get("/api/sessions/{session_id}/participants", response_model=List[models.UserResponseModel], tags=["Sessions"])
async def get_session_participants(
    session_id: str = FastApiPath(..., description="The session ID."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    user_id = user['id']
    participants = _get_session_participants_logic(session_id, user_id)
    if participants is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not a participant of this session.")
    return participants


PARTICIPANT_COLORS = [
    "#E0F2FE",  # sky-100
    "#D1FAE5",  # emerald-100
    "#FEF3C7",  # amber-100
    "#FFE4E6",  # rose-100
    "#F3E8FF",  # purple-100
    "#E0E7FF",  # indigo-100
    "#DBEAFE",  # blue-100
    "#CFFAFE",  # cyan-100
    "#D1F2EB",  # teal-100
    "#FCE7F3",  # pink-100
]

def _generate_unique_initials(participants: List[Dict]) -> List[Dict]:
    """Generates unique initials for a list of participants."""
    # This is a simplified version; a more robust one would handle collisions more gracefully
    for p in participants:
        names = p['name'].split()
        if len(names) > 1:
            p['initials'] = (names[0][0] + names[-1][0]).upper()
        else:
            p['initials'] = names[0][:2].upper()
    # In a real app, you'd add logic here to check for duplicate initials and extend them (e.g., VJ -> VJO)
    return participants

def _get_session_participants_logic(session_id: str, user_id: int) -> Optional[List[models.UserResponseModel]]:
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone():
            return None
        
        # This query now filters out bots AND any user named "AI Assistant"
        cursor.execute(
            """SELECT u.id, u.name, u.email 
               FROM users u JOIN session_participants sp ON u.id = sp.user_id 
               WHERE sp.session_id = ? AND u.is_bot = FALSE AND u.name != 'AI Assistant'
               ORDER BY u.name""",
            (session_id,)
        )
        participants = [dict(row) for row in cursor.fetchall()]
        
        participants = _generate_unique_initials(participants)
        for i, p in enumerate(participants):
            p['color'] = PARTICIPANT_COLORS[p['id'] % len(PARTICIPANT_COLORS)]

        return [models.UserResponseModel(**p) for p in participants]
    except sqlite3.Error:
        return None
    finally:
        if conn: conn.close()

def _get_session_participants_logic(session_id: str, user_id: int) -> Optional[List[models.UserResponseModel]]:
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone():
            return None
        
        # This query now filters out bots AND any user named "AI Assistant"
        cursor.execute(
            """SELECT u.id, u.name, u.email 
               FROM users u JOIN session_participants sp ON u.id = sp.user_id 
               WHERE sp.session_id = ? AND u.is_bot = FALSE AND u.name != 'AI Assistant'
               ORDER BY u.name""",
            (session_id,)
        )
        participants = [dict(row) for row in cursor.fetchall()]
        
        participants = _generate_unique_initials(participants)
        for i, p in enumerate(participants):
            p['color'] = PARTICIPANT_COLORS[p['id'] % len(PARTICIPANT_COLORS)]

        return [models.UserResponseModel(**p) for p in participants]
    except sqlite3.Error:
        return None
    finally:
        if conn: conn.close()

@app.websocket("/ws/{session_id_ws}/{client_js_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id_ws: str = FastApiPath(..., title="Session ID"),
    client_js_id: str = FastApiPath(..., title="Client JS ID")
):
    session_token_from_cookie = websocket.cookies.get(auth.SESSION_COOKIE_NAME)
    if not session_token_from_cookie:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    current_ws_user = await auth.get_user_by_session_token_internal(session_token_from_cookie)
    if not current_ws_user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    user_id = current_ws_user.get('id')
    user_name = current_ws_user.get('name')
    if not user_id:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    participants = _get_session_participants_logic(session_id_ws, user_id)
    if participants is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    await websocket.accept()
    await state.connect(session_id_ws, websocket)

    async def broadcast_participants():
        live_participants = _get_session_participants_logic(session_id_ws, user_id)
        if live_participants is not None:
            await state.broadcast(session_id_ws, {
                "type": "participants_update",
                "payload": [p.model_dump() for p in live_participants]
            }, exclude_websocket=websocket)

    await broadcast_participants()
    
    try:
        while True:
            received_data = await websocket.receive_text()
            message_data = json.loads(received_data)
            message_type = message_data.get("type")
            payload = message_data.get("payload")

            if message_type == "chat_message" and payload:
                user_input_raw = payload.get("user_input")
                turn_id = payload.get("turn_id")
                recipient_ids = payload.get("recipient_ids", [])
                reply_to_id = payload.get("reply_to_id")
                
                db_conn = database.get_db_connection()
                cursor = db_conn.cursor()
                cursor.execute(
                    """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, client_id_temp, turn_id, reply_to_message_id, timestamp) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))""",
                    (session_id_ws, user_id, user_name, 'user', user_input_raw, client_js_id, turn_id, reply_to_id)
                )
                message_id = cursor.lastrowid
                db_conn.commit()
                cursor.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
                new_msg_row = cursor.fetchone()
                db_conn.close()

                msg_dict = dict(new_msg_row)
                msg_dict["project_data"] = None

                current_participants = _get_session_participants_logic(session_id_ws, user_id)
                sender_info = next((p for p in current_participants if p.id == user_id), None)

                message_model = models.MessageItem(**msg_dict)
                if sender_info:
                    message_model.sender_color = sender_info.color

                await state.broadcast(
                    session_id_ws,
                    {"type": "new_message", "payload": message_model.model_dump()},
                    exclude_websocket=websocket
                )

                if any(recipient.upper() in ['AI', 'A'] for recipient in recipient_ids):
                    stream_id = f"{session_id_ws}-{client_js_id}-{turn_id}"
                    asyncio.create_task(
                        llm.invoke_llm_for_session(
                            session_id=session_id_ws,
                            websocket=websocket,
                            user_id=user_id,
                            user_input_raw=user_input_raw,
                            turn_id=turn_id,
                            stream_id=stream_id,
                            reply_to_message_id=reply_to_id
                        )
                    )
            
            elif message_type == "user_typing" and payload is not None:
                await state.broadcast(
                    session_id_ws,
                    {"type": "participant_typing", "payload": {"user_id": user_id, "user_name": user_name, "is_typing": payload.get("is_typing", False)}},
                    exclude_websocket=websocket
                )

            elif message_type == "run_code" and payload:
                project_data = payload.get("project_data")
                project_id = payload.get("project_id")
                language = payload.get("language")

                if not all([project_data, project_id, language]):
                    continue

                project_path = project_utils.create_project_directory_and_files(project_data)
                if not project_path:
                    await utils.send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": "Failed to create project files."})
                    continue

                lang_config = config.SUPPORTED_LANGUAGES.get(language.lower())
                if not lang_config:
                    await utils.send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": f"Language '{language}' is not configured."})
                    continue
                
                run_script = next((f for f in project_data.get("files", []) if f["path"].endswith('run.sh')), None)
                if not run_script:
                        await utils.send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": "Could not find run.sh in project."})
                        continue

                asyncio.create_task(docker_utils.run_code_in_docker(
                    websocket=websocket,
                    client_id=client_js_id,
                    project_id=project_id,
                    project_path=project_path,
                    run_command="sh run.sh",
                    lang_config=lang_config
                ))
            
            elif message_type == "stop_code" and payload:
                project_id = payload.get("project_id")
                if project_id:
                    await docker_utils.stop_container(project_id)

            elif message_type == "code_input" and payload:
                await docker_utils.send_input_to_container(payload['project_id'], payload['input'])
            
            elif message_type == "save_code_content" and payload:
                database.save_edited_code_content(
                    payload['session_id'], payload['code_block_id'], payload['language'], payload['code_content']
                )
                state.remove_memory_for_client(payload['session_id'])
            
            elif message_type == "save_code_result" and payload:
                payload['session_id'] = session_id_ws
                database.save_code_execution_result(**payload)
                state.remove_memory_for_client(session_id_ws)

    except WebSocketDisconnect:
        print(f"Client {client_js_id} (User {user_id}) disconnected.")
    finally:
        await state.disconnect(session_id_ws, websocket)
        await broadcast_participants()
        await docker_utils.cleanup_client_containers(client_js_id)

@app.get("/api/sessions", response_model=List[models.SessionResponseModel], tags=["Sessions"])
async def get_user_sessions(
    request: Request,
    response: FastAPIResponse,
    scope: str = "personal",
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
) -> List[models.SessionResponseModel]:
    response.headers["Cache-Control"] = "no-store"
    user_id = user.get('id')
    
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        user_session_ids = {row['session_id'] for row in cursor.execute("SELECT session_id FROM session_participants WHERE user_id = ? AND is_hidden = 0", (user_id,))}

        if scope == "joinable":
            cursor.execute(
                """SELECT s.id, s.name, s.created_at, s.last_accessed_at AS last_active, s.host_user_id, s.access_level, s.is_active
                   FROM sessions s
                   WHERE s.is_active = 1 AND s.access_level IN ('public', 'protected')
                   ORDER BY s.created_at DESC"""
            )
        else: # "personal" scope
            cursor.execute(
                """SELECT s.id, s.name, s.created_at, s.last_accessed_at AS last_active, s.host_user_id, s.access_level, s.is_active
                   FROM sessions s
                   JOIN session_participants sp ON s.id = sp.session_id
                   WHERE sp.user_id = ? AND sp.is_hidden = 0
                   ORDER BY s.is_active DESC, s.last_accessed_at DESC""",
                (user_id,)
            )
        
        rows = cursor.fetchall()
        sessions_list = []
        for row in rows:
            session_dict = dict(row)
            session_dict['is_member'] = session_dict['id'] in user_session_ids
            sessions_list.append(models.SessionResponseModel(**session_dict))
            
        return sessions_list
    finally:
        if conn: conn.close()

@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Sessions"])
async def delete_session_route(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the session to leave or delete."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    user_id = user.get('id')
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT host_user_id, access_level, is_active FROM sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()

        if not session:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        is_host = (session["host_user_id"] == user_id)

        if not session["is_active"]:
            # If the session is already inactive, the action is always to hide it from history.
            cursor.execute(
                "UPDATE session_participants SET is_hidden = 1 WHERE session_id = ? AND user_id = ?",
                (session_id, user_id)
            )
        elif is_host and session["access_level"] == 'private':
            cursor.execute("UPDATE sessions SET is_active = 0 WHERE id = ?", (session_id,))
        elif not is_host:
            cursor.execute(
                "DELETE FROM session_participants WHERE session_id = ? AND user_id = ?",
                (session_id, user_id)
            )
        
        conn.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        if conn: conn.close()

@app.delete("/api/sessions/{session_id}/edited-blocks/{code_block_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Code Execution"])
async def delete_edited_code_block_route(
    request: Request,
    session_id: str = FastApiPath(..., description="The session ID."),
    code_block_id: str = FastApiPath(..., description="The code block ID to restore."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user['id']))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    conn.close()
    if not database.delete_edited_code_block(session_id, code_block_id):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to restore code block.")
    state.remove_memory_for_client(session_id)
    return

@app.websocket("/ws/lobby")
async def websocket_lobby_endpoint(websocket: WebSocket):
    session_token = websocket.cookies.get(auth.SESSION_COOKIE_NAME)
    if not session_token or not await auth.get_user_by_session_token_internal(session_token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await state.connect_to_lobby(websocket)
    try:
        while True:
            # Keep the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        await state.disconnect_from_lobby(websocket)

@app.delete("/api/sessions/{session_id}/delete-by-host", status_code=status.HTTP_204_NO_CONTENT, tags=["Sessions"])
async def delete_session_by_host(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the session to delete."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    user_id = user.get('id')
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT host_user_id FROM sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()
        if not session or session['host_user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not the host of this session and cannot delete it.")
        
        # Mark the session as inactive instead of a hard delete
        cursor.execute("UPDATE sessions SET is_active = 0 WHERE id = ?", (session_id,))
        conn.commit()

        # Notify clients in the lobby and the session itself
        delete_payload = {"type": "session_deleted", "payload": {"session_id": session_id}}
        await state.broadcast_to_lobby(delete_payload)
        await state.broadcast(session_id, delete_payload)

        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except sqlite3.Error:
        if conn: conn.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during session deletion.")
    finally:
        if conn: conn.close()

@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Sessions"])
async def delete_session_route(
    request: Request,
    session_id: str = FastApiPath(..., description="The ID of the session to leave or delete."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user),
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    user_id = user.get('id')
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT host_user_id, access_level FROM sessions WHERE id = ? AND is_active = 1", (session_id,))
        session = cursor.fetchone()

        if not session:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        is_host = (session["host_user_id"] == user_id)

        if is_host:
            # A host cannot leave their own session via this method. 
            # This endpoint is now only for "leaving" as a participant or deleting a private session.
            # Deleting public/protected sessions will be a separate action.
            if session["access_level"] == 'private':
                cursor.execute("UPDATE sessions SET is_active = 0 WHERE id = ?", (session_id,))
                print(f"---- SERVER LOG: Private session '{session_id}' deleted by host '{user_id}'.")
            else:
                # A host trying to "leave" a public session does nothing here.
                # This action will be handled on the client-side as "hide".
                print(f"---- SERVER LOG: Host '{user_id}' attempted to leave public/protected session '{session_id}'. Action ignored on backend.")
                pass
        else:
            # If the user is not the host, they are "leaving" the session.
            cursor.execute(
                "DELETE FROM session_participants WHERE session_id = ? AND user_id = ?",
                (session_id, user_id)
            )
            print(f"---- SERVER LOG: User '{user_id}' left session '{session_id}'.")

        conn.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except sqlite3.Error as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error while processing your request.")
    finally:
        if conn: conn.close()

@app.on_event("startup")
async def startup_event():
    print("Application startup: Initializing database...")
    db_parent_dir = config.DATABASE_PATH.parent
    if not db_parent_dir.exists():
        db_parent_dir.mkdir(parents=True, exist_ok=True)
    database.init_db()
    print("Database initialization check complete.")

if config.STATIC_DIR and config.STATIC_DIR.is_dir():
    dist_dir = config.STATIC_DIR / "dist"
    if dist_dir.is_dir():
        app.mount("/dist", StaticFiles(directory=dist_dir), name="dist_assets")
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static_general")

