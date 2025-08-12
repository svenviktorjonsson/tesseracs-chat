from pydantic import BaseModel, Field, EmailStr, StringConstraints, HttpUrl
from pydantic_settings import BaseSettings
from typing import Optional, List, Dict, Any, Annotated

class LLMAvailableModel(BaseModel):
    model_id: str
    display_name: str
    context_window: Optional[int] = None

class LLMProviderDetail(BaseModel):
    id: str
    display_name: str
    type: str
    is_system_configured: bool
    needs_api_key_from_user: bool
    can_accept_user_api_key: bool
    can_accept_user_base_url: bool
    available_models: List[LLMAvailableModel]

class UserLLMSettingsResponse(BaseModel):
    selected_llm_provider_id: Optional[str] = None
    selected_llm_model_id: Optional[str] = None
    has_user_api_key: bool = False
    selected_llm_base_url: Optional[HttpUrl] = None

class UserLLMSettingsUpdateRequest(BaseModel):
    selected_llm_provider_id: Optional[str] = Field(None, description="ID of the selected LLM provider (e.g., 'ollama_local'). Use null to clear.")
    selected_llm_model_id: Optional[str] = Field(None, description="ID of the selected model (e.g., 'qwen2:7b-instruct-q4_K_M'). Use null to clear if provider allows.")
    user_llm_api_key: Optional[str] = Field(None, description="User-provided API key. Will be encrypted. Send empty string or null to clear.")
    selected_llm_base_url: Optional[HttpUrl] = Field(None, description="User-defined base URL. Send empty string or null to clear.")

class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    user_email: Optional[EmailStr] = None

class UserResponseModel(BaseModel):
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

class SessionUpdateRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

class SessionResponseModel(BaseModel):
    id: str
    name: Optional[str] = None
    created_at: Optional[str] = None
    last_active: Optional[str] = None
    host_user_id: Optional[int] = None

class SessionListContainerResponse(BaseModel):
    sessions: List[SessionResponseModel]

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
    turn_id: Optional[int] = None
    model_provider_id: Optional[str] = None
    model_id: Optional[str] = None

class MessageListContainerResponse(BaseModel):
    messages: List[MessageItem]