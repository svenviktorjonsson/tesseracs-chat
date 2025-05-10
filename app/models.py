# app/models.py
from pydantic import BaseModel, Field, EmailStr, StringConstraints, HttpUrl # Added HttpUrl
from typing import Optional, List, Dict, Any, Annotated # Removed Pattern, not used

# --- LLM Provider and Model Information Models (for API response) ---
class LLMAvailableModel(BaseModel):
    model_id: str
    display_name: str
    context_window: Optional[int] = None

class LLMProviderDetail(BaseModel):
    id: str 
    display_name: str
    type: str 
    is_system_configured: bool 
    requires_api_key: bool 
    needs_api_key_config_for_user: bool 
    available_models: List[LLMAvailableModel]

# --- User LLM Settings Models ---
class UserLLMSettingsResponse(BaseModel):
    selected_llm_provider_id: Optional[str] = None
    selected_llm_model_id: Optional[str] = None
    has_user_api_key: bool = False 
    selected_llm_base_url: Optional[HttpUrl] = None # Use HttpUrl for validation

class UserLLMSettingsUpdateRequest(BaseModel):
    selected_llm_provider_id: Optional[str] = Field(None, description="ID of the selected LLM provider (e.g., 'ollama_local').")
    selected_llm_model_id: Optional[str] = Field(None, description="ID of the selected model (e.g., 'qwen2:7b-instruct-q4_K_M').")
    user_llm_api_key: Optional[str] = Field(None, description="User-provided API key. Will be encrypted. Send empty string or null to clear.") # min_length removed to allow empty string for clearing
    selected_llm_base_url: Optional[HttpUrl] = Field(None, description="User-defined base URL for the selected provider, if applicable. Send empty string or null to clear.")


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
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

class RegistrationResponse(BaseModel):
    message: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ForgotPasswordResponse(BaseModel):
    message: str

class UpdateNameRequest(BaseModel):
    new_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    current_password: str = Field(..., min_length=1) 

class UpdateNameResponse(BaseModel):
    message: str
    new_name: str

class RegeneratePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, description="The user's current password for verification.")

class RegeneratePasswordResponse(BaseModel):
    message: str 

class UpdateEmailRequest(BaseModel):
    new_email: EmailStr 
    current_password: str = Field(..., min_length=1, description="The user's current password for verification.")

class UpdateEmailResponse(BaseModel):
    message: str 
    new_email: EmailStr


# --- Session & Message Models ---
class SessionUpdateRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

class SessionListItem(BaseModel):
    id: str
    name: Optional[str] = None 
    last_active: Optional[str] = None 

class SessionListResponse(BaseModel): 
    sessions: List[SessionListItem]

class MessageItem(BaseModel): 
    id: int
    session_id: str
    user_id: Optional[int] = None
    sender_name: Optional[str] = None
    sender_type: str 
    content: str
    client_id_temp: Optional[str] = None
    thinking_content: Optional[str] = None
    timestamp: str 
    turn_id: Optional[int] = None # Added turn_id as it's in your DB and API response for messages

class MessageListResponse(BaseModel): 
    messages: List[MessageItem]

