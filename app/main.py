# app/main.py


from pydantic import BaseModel, Field # Add these for the request body model

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
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, Response # Ensure Response is imported
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState
import datetime
from typing import Optional, Dict, Any, List # Ensure Optional is imported

# Project local imports
from . import config
from . import state
from . import llm
from . import docker_utils
from . import utils
from . import database
from . import auth
from . import email_utils

# --- FastAPI App Initialization ---
app = FastAPI(title="Tesseracs Chat")

class SessionUpdateRequest(BaseModel):
    """Defines the expected request body for updating a session's name."""
    name: str = Field(
        ..., 
        min_length=1, 
        max_length=100, 
        description="The new name for the session (1-100 characters)."
    )

class UserResponseModel(BaseModel):
    id: int
    name: str
    email: str

# --- Database Initialization on Startup ---
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

@app.get("/api/me", response_model=UserResponseModel, tags=["Users"])
async def get_current_user_details(
    request: Request, 
    user: Dict[str, Any] = Depends(auth.get_current_active_user) # Uses your existing auth
):
    """
    Retrieves the details (id, name, email) of the currently authenticated user.
    """
    if not user:
        # This case should ideally be handled by get_current_active_user raising an HTTPException
        # if the user is not authenticated, but as a safeguard:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Not authenticated"
        )
    
    # The 'user' dict comes from your auth.get_current_active_user dependency
    # Ensure it contains 'id', 'name', and 'email' keys.
    # If your auth.get_user_by_session_token (used by get_current_active_user)
    # fetches these from the DB, they should be present.
    user_id = user.get("id")
    user_name = user.get("name")
    user_email = user.get("email")

    if user_id is None or user_name is None or user_email is None:
        # This might indicate an issue with how user data is stored/retrieved by auth
        print(f"ERROR in /api/me: User object from auth is missing expected fields. User: {user}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User data is incomplete on the server."
        )

    return {"id": user_id, "name": user_name, "email": user_email}
# --- Authentication HTTP Routes ---

@app.patch("/api/sessions/{session_id}", response_model=Dict[str, Any])
async def rename_session(
    session_id: str = FastApiPath(..., title="Session ID", description="The ID of the session to rename."),
    update_data: SessionUpdateRequest = Body(...), 
    user: Dict[str, Any] = Depends(auth.get_current_active_user) 
):
    """
    Updates the name of a specific chat session.
    Requires the user to be a participant in the session.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = user['id']
    new_name = update_data.name.strip() 

    if not new_name:
        raise HTTPException(status_code=400, detail="Session name cannot be empty.")

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT s.id 
            FROM sessions s
            JOIN session_participants sp ON s.id = sp.session_id
            WHERE s.id = ? AND sp.user_id = ? AND s.is_active = 1 
            """,
            (session_id, user_id)
        )
        session_row = cursor.fetchone()

        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found or user lacks permission.") 
        
        cursor.execute(
            "UPDATE sessions SET name = ? WHERE id = ?",
            (new_name, session_id)
        )
        
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Session not found during update.")

        conn.commit()
        print(f"API: Renamed session {session_id} to '{new_name}' for user ID {user_id}")
        
        return {"id": session_id, "name": new_name, "message": "Session renamed successfully"}

    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"API ERROR (/api/sessions/{session_id} PATCH): Database error for user {user_id}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Database error renaming session.")
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        if conn: conn.rollback()
        print(f"API ERROR (/api/sessions/{session_id} PATCH): Unexpected error for user {user_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Server error renaming session.")
    finally:
        if conn: conn.close()

@app.get("/login", response_class=HTMLResponse)
async def get_login_page_route(request: Request, user: Optional[Dict] = Depends(auth.get_current_user)):
    if user:
        return RedirectResponse(url="/", status_code=302)
    login_html_path = config.STATIC_DIR / "login.html"
    if not login_html_path.is_file():
        raise HTTPException(status_code=404, detail="login.html not found.")
    return FileResponse(login_html_path)

@app.post("/login")
async def handle_login_or_register_route(
    request: Request, response: FastAPIResponse, name: str = Form(...), email_form: str = Form(..., alias="email")
):
    email = email_form.lower().strip()
    cleaned_name = name.strip()
    if not cleaned_name or not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Valid name and email are required.")
    conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        message_to_client = ""; magic_link_sent_flag = False; user_id: Optional[int] = None
        cursor.execute("SELECT id, name, is_active FROM users WHERE email = ?", (email,))
        user_row = cursor.fetchone()
        if user_row:
            user_id = user_row["id"]
            if not user_row["is_active"]: raise HTTPException(status_code=403, detail="Account inactive.")
            if user_row["name"] != cleaned_name: cursor.execute("UPDATE users SET name = ? WHERE id = ?", (cleaned_name, user_id))
        else:
            cursor.execute("INSERT INTO users (name, email, is_active) VALUES (?, ?, ?)", (cleaned_name, email, True))
            user_id = cursor.lastrowid
            if user_id is None: raise sqlite3.Error("Failed to get lastrowid.")
        if user_id is not None:
            magic_token_raw = await auth.create_magic_link_token(user_id=user_id, conn=conn)
            base_url = str(request.base_url).rstrip('/')
            magic_login_path = request.url_for("process_magic_link_route").path
            full_magic_link = f"{base_url}{magic_login_path}?token={magic_token_raw}"
            email_sent = await email_utils.send_magic_link_email(
                recipient_email=email, recipient_name=cleaned_name,
                magic_link=full_magic_link, duration_minutes=auth.MAGIC_LINK_DURATION_MINUTES
            )
            if email_sent:
                magic_link_sent_flag = True
                message_to_client = f"Magic link sent successfully to {email}. Please check your inbox."
                conn.commit()
            else:
                conn.rollback()
                raise HTTPException(status_code=502, detail="Could not send the login email.")
        else:
            conn.rollback(); raise HTTPException(status_code=500, detail="User ID not established.")
    except HTTPException as http_exc:
        if conn: conn.rollback(); raise http_exc
    except Exception as e:
        if conn: conn.rollback(); print(f"ROUTE /login: Unhandled error: {type(e).__name__} - {e}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"An internal server error occurred ({type(e).__name__}).")
    finally:
        if conn: conn.close()
    return {"message": message_to_client, "magic_link_sent": magic_link_sent_flag}


@app.get("/magic_login", response_class=HTMLResponse) 
async def process_magic_link_route(token: str, request: Request, response: Response): 
    user_data = await auth.verify_magic_link_token(token)

    if user_data and user_data.get("id"):
        await auth.create_user_session(response, user_data["id"])
        print(f"User {user_data['email']} logged in via magic link. Cookie set on 'response' object. Headers: {response.headers.raw}")

        redirect_url = request.url_for("get_session_choice_page")
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Logging In...</title>
            <meta http-equiv="refresh" content="0.5;url={redirect_url}">
            <script type="text/javascript">
                setTimeout(function() {{
                    window.location.href = "{redirect_url}";
                }}, 500); 
            </script>
            <style> body {{ font-family: sans-serif; padding: 2em; }} </style>
        </head>
        <body>
            <h1>Login Successful!</h1>
            <p>You are being redirected to the application...</p>
            <p>If you are not redirected automatically, <a href="{redirect_url}">click here</a>.</p>
        </body>
        </html>
        """
        
        response.status_code = 200
        response.media_type = "text/html"
        response.body = html_content.encode("utf-8")
        return response 

    else:
        print(f"Magic link verification failed for token: {token[:10]}...")
        login_url = request.url_for("get_login_page_route")
        error_content = f"""
        <html><head><title>Login Error</title></head><body>
        <h1>Login Link Error</h1>
        <p>This login link is invalid, expired, or has already been used.</p>
        <p><a href='{login_url}'>Please try requesting a new link via the login page.</a></p>
        </body></html>
        """
        return HTMLResponse(content=error_content, status_code=400)

@app.get("/logout")
async def logout_route(response: FastAPIResponse, session_token_value: Optional[str] = Depends(auth.cookie_scheme)):
    await auth.logout_user(response, session_token_value)
    return RedirectResponse(url="/login", status_code=302)

# --- Main Application Routes (Protected) ---

@app.get("/", response_class=HTMLResponse, name="get_session_choice_page") 
async def get_session_choice_page(request: Request, user: Optional[Dict] = Depends(auth.get_current_active_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    session_choice_html_path = config.STATIC_DIR / "session-choice.html"
    if not session_choice_html_path.is_file():
        raise HTTPException(status_code=404, detail="session-choice.html not found.")
    try:
        with open(session_choice_html_path, "r", encoding="utf-8") as f: html_content = f.read()
        html_content = html_content.replace("[User Name]", user.get("name", "User"))
        return HTMLResponse(content=html_content)
    except Exception as e:
        print(f"Error reading/serving session-choice.html: {e}"); traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error loading session choice page.")

@app.post("/sessions/create", status_code=302) 
async def create_new_session_route(
    request: Request,
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """
    Creates a new chat session, sets the default name based on creation time,
    adds the creator as a participant, and redirects to the new chat page.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    new_session_id = str(uuid.uuid4())
    host_user_id = user["id"]
    conn = None
    default_session_name = "" 

    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO sessions (id, host_user_id, name, is_active) 
            VALUES (?, ?, ?, ?)
            """,
            (new_session_id, host_user_id, None, True) 
        )

        cursor.execute(
            "SELECT created_at FROM sessions WHERE id = ?", (new_session_id,)
        )
        session_row = cursor.fetchone()
        
        if not session_row or not session_row["created_at"]:
            default_session_name = f"Session ({new_session_id[:4]})"
            print(f"WARNING: Could not fetch created_at for session {new_session_id}. Using fallback name.")
        else:
            try:
                created_at_str = session_row["created_at"].replace('Z', '+00:00')
                created_dt = datetime.datetime.fromisoformat(created_at_str)
                default_session_name = created_dt.strftime("%b %d, %Y %I:%M %p") 
            except (ValueError, TypeError) as fmt_err:
                print(f"WARNING: Error formatting timestamp '{session_row['created_at']}': {fmt_err}. Using fallback name.")
                default_session_name = f"Session ({new_session_id[:4]})"

        cursor.execute(
            "UPDATE sessions SET name = ? WHERE id = ?",
            (default_session_name, new_session_id)
        )

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
    return RedirectResponse(url=str(chat_url), status_code=303)

@app.get("/chat/{session_id}", response_class=HTMLResponse, name="get_chat_page_for_session") 
async def get_chat_page_for_session(
    request: Request, session_id: str = FastApiPath(...), user: Optional[Dict] = Depends(auth.get_current_active_user)
):
    if not user: return RedirectResponse(url="/login", status_code=302)
    user_id = user['id']; conn = None
    try:
        conn = database.get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM sessions WHERE id = ? AND is_active = 1", (session_id,)) # Also check is_active
        session_row = cursor.fetchone()
        if not session_row: raise HTTPException(status_code=404, detail="Chat session not found or is inactive.")
        cursor.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        if not cursor.fetchone(): raise HTTPException(status_code=403, detail="You do not have access to this chat session.")
        
        # Update last_accessed_at for the session
        current_time_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute("UPDATE sessions SET last_accessed_at = ? WHERE id = ?", (current_time_utc_iso, session_id))
        conn.commit()

        print(f"User {user['email']} accessing chat for session: {session_id}. Last accessed updated.")
    except HTTPException as http_exc: 
        if conn: conn.rollback() # Rollback on HTTP exception if transaction started
        raise http_exc
    except Exception as e: 
        if conn: conn.rollback()
        print(f"Error verifying session access or updating last_accessed_at: {e}"); traceback.print_exc(); 
        raise HTTPException(status_code=500, detail="Error verifying session access.")
    finally:
        if conn: conn.close()
    chat_html_path = config.STATIC_DIR / "chat-session.html"
    if not chat_html_path.is_file(): raise HTTPException(status_code=404, detail="Chat interface file not found.")
    return FileResponse(chat_html_path)

# In app/main.py

# (Keep existing imports: Any, Dict, WebSocket, WebSocketState, json, sqlite3, traceback, database, state, llm, etc.)

async def handle_chat_message(
    chain: Any, memory: Any, websocket: WebSocket, client_js_id: str,
    current_user: Dict, session_id: str, user_input: str
):
    user_name = current_user.get('name', 'User')
    user_db_id = current_user['id']
    full_response = ""
    db_conn_user_msg = None

    # Save user message to DB
    try:
        db_conn_user_msg = database.get_db_connection()
        db_cursor_user_msg = db_conn_user_msg.cursor()
        db_cursor_user_msg.execute(
            """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, client_id_temp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, user_db_id, user_name, 'user', user_input, client_js_id)
        )
        db_conn_user_msg.commit()
    except Exception as db_err:
        print(f"DB ERROR saving user message for session {session_id} (client {client_js_id}): {db_err}")
        if db_conn_user_msg:
            db_conn_user_msg.rollback()
    finally:
        if db_conn_user_msg:
            db_conn_user_msg.close()

    # Process message with LLM chain and stream response
    try:
        async for chunk_data in chain.astream({"input": user_input}):
            chunk_str = str(chunk_data)
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WS: WebSocket disconnected during LLM stream for session {session_id}. Aborting.")
                return
            await websocket.send_text(chunk_str)
            full_response += chunk_str
    except Exception as chain_exc:
        error_msg = f"<ERROR>LLM Error: Processing your message failed. Please try again. Details: {chain_exc}"
        print(f"LLM chain error for session {session_id} (client {client_js_id}): {chain_exc}")
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(error_msg)
            except Exception as send_err:
                print(f"WS ERROR: Could not send LLM error to client for session {session_id}: {send_err}")
        return # Stop processing on chain error

    # After successful streaming
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            # Send End-Of-Stream marker
            await websocket.send_text("<EOS>")

            # Save context to the in-memory object
            memory.save_context({"input": user_input}, {"output": full_response})
            print(f"WS: Context saved to in-memory object for session {session_id} (client {client_js_id}).")

            # --- ADDED: Save memory state to database ---
            if state and hasattr(state, 'save_memory_state_to_db'):
                try:
                    state.save_memory_state_to_db(session_id, memory)
                    # Log success is handled inside save_memory_state_to_db
                except Exception as save_mem_err:
                    # Log error but continue, as the main message saving is more critical
                    print(f"ERROR saving memory state to DB for session {session_id}: {save_mem_err}")
            else:
                print(f"Warning: state.save_memory_state_to_db function not found. Memory not persisted to DB.")
            # --- END OF ADDED CALL ---

            # Save AI message to DB
            db_conn_ai_msg = None
            try:
                db_conn_ai_msg = database.get_db_connection()
                db_cursor_ai_msg = db_conn_ai_msg.cursor()
                db_cursor_ai_msg.execute(
                    """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, client_id_temp)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (session_id, None, "AI", 'ai', full_response, client_js_id)
                )
                db_conn_ai_msg.commit()
            except Exception as db_err:
                print(f"DB ERROR saving AI message for session {session_id} (client {client_js_id}): {db_err}")
                if db_conn_ai_msg:
                    db_conn_ai_msg.rollback()
            finally:
                if db_conn_ai_msg:
                    db_conn_ai_msg.close()

        except Exception as post_stream_err:
            print(f"CHAT ERROR: Post-stream processing for session {session_id} (client {client_js_id}): {post_stream_err}")
            traceback.print_exc()

@app.websocket("/ws/{session_id_ws}/{client_js_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id_ws: str = FastApiPath(..., title="Session ID", description="The ID of the chat session."),
    client_js_id: str = FastApiPath(..., title="Client JS ID", description="A unique ID generated by the client-side JavaScript.")
):
    # Authenticate user via session cookie
    session_token_from_cookie = websocket.cookies.get(auth.SESSION_COOKIE_NAME)
    current_ws_user: Optional[Dict[str, Any]] = None

    if session_token_from_cookie:
        current_ws_user = await auth.get_user_by_session_token(session_token_from_cookie)

    ws_log_prefix_unauth = f"WS ({websocket.client.host}:{websocket.client.port}) for session {session_id_ws}, client {client_js_id}:"
    if not current_ws_user:
        print(f"{ws_log_prefix_unauth} Authentication failed. Cookie value was {'present but invalid/expired' if session_token_from_cookie else 'missing'}. Closing WebSocket.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # User authenticated, set up logging prefix with user info
    user_id = current_ws_user['id']
    user_email = current_ws_user.get('email', f'UserID_{user_id}')
    ws_log_prefix = f"WS (User: {user_email}, Session: {session_id_ws}, ClientJS: {client_js_id}):"
    print(f"{ws_log_prefix} User successfully authenticated via session token.")

    # Verify session existence and user participation
    conn_verify = None
    is_participant = False
    try:
        conn_verify = database.get_db_connection()
        cursor_verify = conn_verify.cursor()

        cursor_verify.execute("SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id_ws,))
        if not cursor_verify.fetchone():
            print(f"{ws_log_prefix} Session {session_id_ws} not found or is inactive. Closing WebSocket.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        cursor_verify.execute("SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?", (session_id_ws, user_id))
        is_participant = cursor_verify.fetchone() is not None
        if not is_participant:
            print(f"{ws_log_prefix} User is NOT a participant in this session. Closing WebSocket.")

    except Exception as e:
        print(f"{ws_log_prefix} DB error verifying participation: {e}")
        traceback.print_exc()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return
    finally:
        if conn_verify: conn_verify.close()

    if not is_participant:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    print(f"{ws_log_prefix} User confirmed as participant.")

    # Accept the WebSocket connection
    try:
        await websocket.accept()
        print(f"{ws_log_prefix} WebSocket connection accepted.")
    except Exception as accept_err:
        print(f"{ws_log_prefix} Error accepting WebSocket connection: {accept_err}")
        return

    # Get or load conversation memory for this session
    memory_key = f"session_{session_id_ws}"
    memory = state.get_memory_for_client(session_id_ws) # This now handles DB loading

    # Define the function to load memory for the LangChain chain
    def load_memory_for_current_session(_ignored_input_map=None):
        # Load memory variables from the ConversationBufferMemory instance
        memory_vars = memory.load_memory_variables({})

        # --- ADDED LOGGING ---
        print(f"--- Loading Memory for Session {session_id_ws} ---")
        print(f"Raw memory variables loaded: {memory_vars}") # See the whole dict

        history = memory_vars.get("history", [])

        print(f"Extracted 'history' (type: {type(history)}):")
        if isinstance(history, list):
            for i, msg in enumerate(history):
                # Log the type and content of each message in the history
                print(f"  [{i}] Type: {type(msg).__name__}, Content: '{getattr(msg, 'content', 'N/A')[:100]}...'")
        else:
            print(f"  History is not a list: {history}")
        print(f"--- Finished Loading Memory ---")
        # --- END OF ADDED LOGGING ---

        return history

    # Create the LangChain processing chain
    chain: Any
    try:
        chain = llm.create_chain(load_memory_for_current_session)
        print(f"{ws_log_prefix} LLM chain created successfully.")
    except Exception as chain_init_error:
        print(f"{ws_log_prefix} ERROR creating LCEL chain: {chain_init_error}")
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            # Using await utils.send_ws_message assumes utils.py has this async helper
            # If not, implement direct websocket.send_json or similar here
            await websocket.send_json({"type": "error", "payload": {"message": "Server error: Could not initialize chat."}})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # Main loop to handle incoming messages
    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"{ws_log_prefix} WebSocket no longer connected. Breaking message loop.")
                break

            received_data = await websocket.receive_text()
            print(f"{ws_log_prefix} Received data: {received_data[:100]}{'...' if len(received_data) > 100 else ''}")

            try:
                # Attempt to parse as JSON for specific commands (run_code, stop_code)
                message_data = json.loads(received_data)
                message_type = message_data.get("type")
                payload = message_data.get("payload")

                if message_type == "run_code" and payload and payload.get("code_block_id"):
                    language = payload.get("language")
                    code = payload.get("code")
                    code_block_id = payload.get("code_block_id")
                    print(f"{ws_log_prefix} Received 'run_code' command for block {code_block_id}, lang: {language}.")
                    if language and code is not None:
                        # Schedule code execution in Docker without blocking the WebSocket loop
                        asyncio.create_task(docker_utils.run_code_in_docker_stream(websocket, client_js_id, code_block_id, language, code))
                    else:
                        print(f"{ws_log_prefix} Invalid 'run_code' payload for block {code_block_id}.")
                        # Send error back to client if payload is invalid
                        await websocket.send_json({"type": "code_finished", "payload": {"code_block_id": payload.get("code_block_id","unknown"), "exit_code": -1, "error": "Invalid run_code payload: Missing language or code."}})

                elif message_type == "stop_code" and payload and payload.get("code_block_id"):
                    code_block_id = payload.get("code_block_id")
                    print(f"{ws_log_prefix} Received 'stop_code' command for block {code_block_id}.")
                    # Schedule container stop without blocking
                    asyncio.create_task(docker_utils.stop_docker_container(code_block_id))

                else:
                    # If JSON but not a recognized command type, treat as chat message
                    print(f"{ws_log_prefix} Received JSON, but unknown type '{message_type}'. Treating as chat message.")
                    chat_input_text = received_data # Send the raw JSON string as chat input? Or extract text?
                    # Decide how to handle generic JSON messages - for now, pass raw string
                    await handle_chat_message(chain, memory, websocket, client_js_id, current_ws_user, session_id_ws, chat_input_text)

            except json.JSONDecodeError:
                # If data is not JSON, treat it as a plain text chat message
                print(f"{ws_log_prefix} Data not JSON, treating as plain chat message.")
                await handle_chat_message(chain, memory, websocket, client_js_id, current_ws_user, session_id_ws, received_data)

            except Exception as handler_exc:
                # Catch errors during message handling (JSON parsing, command processing, chat handling)
                print(f"{ws_log_prefix} ERROR handling received message: {handler_exc}")
                traceback.print_exc()
                if websocket.client_state == WebSocketState.CONNECTED:
                     await websocket.send_json({"type": "error", "payload": {"message": "Server error processing your request."}})

    except WebSocketDisconnect:
        print(f"{ws_log_prefix} WebSocket disconnected by client.")
    except Exception as e:
        # Catch errors in the main WebSocket loop itself
        print(f"{ws_log_prefix} ERROR in WebSocket main loop: {e}")
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            except Exception:
                pass # Ignore errors during close after another error
    finally:
        # Cleanup resources associated with this connection
        print(f"{ws_log_prefix} Cleaning up resources...")

        # Memory is no longer removed from the global cache here for persistence
        # state.remove_memory_for_client(memory_key)
        print(f"{ws_log_prefix} Memory object for {memory_key} NOT explicitly removed from cache on disconnect.")

        # Cleanup any Docker containers specifically started by this client connection
        await docker_utils.cleanup_client_containers(client_js_id)

        # Ensure WebSocket is closed
        if websocket.client_state == WebSocketState.CONNECTED:
            print(f"{ws_log_prefix} WebSocket still connected in finally block, attempting graceful close.")
            try:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            except Exception as final_close_err:
                print(f"{ws_log_prefix} Error during final WebSocket close: {final_close_err}")
        print(f"{ws_log_prefix} Cleanup complete. WebSocket connection definitively closed.")

@app.get("/api/sessions", response_model=List[Dict[str, Any]]) 
async def get_user_sessions(
    request: Request, 
    user: Dict[str, Any] = Depends(auth.get_current_active_user) 
):
    """
    Fetches a list of chat sessions the current user is a participant in.
    Returns a list of session objects, each containing 'id', 'name', and 'last_active',
    ordered by the most recently active session first.
    """
    user_id = user['id']
    sessions_list = [] 
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT s.id, s.name, s.last_accessed_at AS last_active
            FROM sessions s
            JOIN session_participants sp ON s.id = sp.session_id
            WHERE sp.user_id = ? AND s.is_active = 1 
            ORDER BY s.last_accessed_at DESC 
            """,
            (user_id,)
        )

        rows = cursor.fetchall()
        for row in rows:
            sessions_list.append(dict(row)) 

        print(f"API: Fetched {len(sessions_list)} sessions for user ID {user_id}, ordered by last active.")
        return sessions_list

    except sqlite3.Error as db_err:
        print(f"API ERROR (/api/sessions): Database error for user {user_id}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Database error fetching sessions.")
    except Exception as e:
        print(f"API ERROR (/api/sessions): Unexpected error for user {user_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Server error fetching sessions.")
    finally:
        if conn:
            conn.close()

@app.get("/api/sessions/{session_id}/messages", response_model=List[Dict[str, Any]])
async def get_chat_messages_for_session(
    session_id: str = FastApiPath(..., title="Session ID", description="The ID of the session to fetch messages for."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user)
):
    """
    Fetches all chat messages for a given session, ordered by timestamp.
    Ensures the user is a participant in the session.
    """
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = user['id']
    messages_list = []
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT 1 FROM sessions WHERE id = ? AND is_active = 1", (session_id,)
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or is inactive.")

        cursor.execute(
            "SELECT 1 FROM session_participants WHERE session_id = ? AND user_id = ?",
            (session_id, user_id)
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this chat session.")

        cursor.execute(
            """
            SELECT id, session_id, user_id, sender_name, sender_type, content, client_id_temp, thinking_content, timestamp
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
            """,
            (session_id,)
        )
        rows = cursor.fetchall()
        for row in rows:
            messages_list.append(dict(row)) 

        print(f"API: Fetched {len(messages_list)} messages for session {session_id} for user ID {user_id}")
        return messages_list

    except sqlite3.Error as db_err:
        print(f"API ERROR (/api/sessions/{session_id}/messages): Database error for user {user_id}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching messages.")
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"API ERROR (/api/sessions/{session_id}/messages): Unexpected error for user {user_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error fetching messages.")
    finally:
        if conn:
            conn.close()


@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT) 
async def delete_session_route(
    session_id: str = FastApiPath(..., title="Session ID", description="The ID of the session to delete."),
    user: Dict[str, Any] = Depends(auth.get_current_active_user) 
):
    """
    Deletes a specific chat session.
    Requires the user to be the host of the session to delete it.
    """
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = user['id']
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT host_user_id FROM sessions WHERE id = ? AND is_active = 1",
            (session_id,)
        )
        session_row = cursor.fetchone()

        if not session_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or is inactive.")
        
        if session_row["host_user_id"] != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this session.")

        cursor.execute(
            "DELETE FROM sessions WHERE id = ?", 
            (session_id,)
        )
        
        if cursor.rowcount == 0:
            conn.rollback() 
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session found but could not be deleted (e.g., already deleted by another request).")

        conn.commit()
        print(f"API: Deleted session {session_id} by user ID {user_id}")
        
        return 

    except sqlite3.Error as db_err:
        if conn: conn.rollback()
        print(f"API ERROR (DELETE /api/sessions/{session_id}): Database error for user {user_id}: {db_err}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error deleting session.")
    except HTTPException as http_exc:
        if conn: conn.rollback() 
        raise http_exc
    except Exception as e:
        if conn: conn.rollback()
        print(f"API ERROR (DELETE /api/sessions/{session_id}): Unexpected error for user {user_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error deleting session.")
    finally:
        if conn:
            conn.close()

# --- Server Start Function ---
def start_server():
    host = os.getenv("HOST", "127.0.0.1"); port = int(os.getenv("PORT", 8001)); url = f"http://{host}:{port}"
    print("-" * 30); print(f"Tesseracs Chat Server Starting..."); print(f"Access via: {url}/login")
    print(f"Using Config: Model='{config.MODEL_ID}', Ollama='{config.OLLAMA_BASE_URL}'")
    print(f"Static files from: {config.STATIC_DIR}, Bundles from: {dist_dir}")
    print("-" * 30)
    try: webbrowser.open(f"{url}/login")
    except Exception as browser_err: print(f"Warning: Could not open browser: {browser_err}")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info", reload=True)

if __name__ == "__main__":
    start_server()