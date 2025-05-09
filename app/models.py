# app/models.py
from pydantic import BaseModel, Field, EmailStr, StringConstraints # Removed constr, Added StringConstraints
from typing import Optional, List, Dict, Any, Pattern, Annotated # Added Annotated

# --- Authentication & User Models ---
class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    user_email: Optional[EmailStr] = None

class UserResponseModel(BaseModel): # For /api/me
    id: int
    name: str
    email: EmailStr

class EmailCheckRequest(BaseModel):
    email: EmailStr

class EmailCheckResponse(BaseModel):
    exists: bool
    user_name: Optional[str] = None

class RegistrationRequest(BaseModel):
    email: EmailStr
    # Using Annotated with StringConstraints for Pydantic V2
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

class RegistrationResponse(BaseModel):
    message: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ForgotPasswordResponse(BaseModel):
    message: str

class UpdateNameRequest(BaseModel):
    # Correct Pydantic V2 way to apply string constraints
    new_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    current_password: str = Field(..., min_length=1) # min_length for string fields is fine with Field

class UpdateNameResponse(BaseModel):
    message: str
    new_name: str

class RegeneratePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, description="The user's current password for verification.")

class RegeneratePasswordResponse(BaseModel):
    message: str # e.g., "Password regenerated successfully. An email has been sent with your new password. You will now be logged out."

# --- ADDED: Models for Update Email (User Settings) ---
class UpdateEmailRequest(BaseModel):
    new_email: EmailStr # Ensures the new email is in a valid format.
    current_password: str = Field(..., min_length=1, description="The user's current password for verification.")

class UpdateEmailResponse(BaseModel):
    message: str # e.g., "Email updated successfully. You will now be logged out."
    new_email: EmailStr
# --- END OF ADDED Models ---


# --- Session & Message Models (from app/main.py context) ---
class SessionUpdateRequest(BaseModel):
    # Correct Pydantic V2 way for name field here as well
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

# If you have Pydantic models for session lists or message lists, they would go here too.
# For example, if you decide to type the response for /api/sessions:
class SessionListItem(BaseModel):
    id: str
    name: Optional[str] = None # Name can be None initially
    last_active: Optional[str] = None # Or datetime, adjust as per your DB/API response

class SessionListResponse(BaseModel): # Example, not currently used in main.py
    sessions: List[SessionListItem]

# Example for /api/sessions/{session_id}/messages response items
class MessageItem(BaseModel): # Example, not currently used in main.py
    id: int
    session_id: str
    user_id: Optional[int] = None
    sender_name: Optional[str] = None
    sender_type: str # 'user', 'ai', 'system', 'anon_user'
    content: str
    client_id_temp: Optional[str] = None
    thinking_content: Optional[str] = None
    timestamp: str # Or datetime

class MessageListResponse(BaseModel): # Example, not currently used in main.py
    messages: List[MessageItem]

# Add any other Pydantic models your application uses or will use.
