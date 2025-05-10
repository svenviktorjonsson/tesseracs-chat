# app/llm.py
import sys
import os
import traceback 
from typing import List, Callable, Optional, Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, Runnable, RunnableLambda
from langchain_core.messages import BaseMessage, HumanMessage 
from langchain_ollama.llms import OllamaLLM
from langchain_openai import ChatOpenAI 
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

from . import config 

def get_model(
    provider_id: str, 
    model_id: str, 
    api_key: Optional[str] = None, # User-provided API key (takes precedence)
    base_url_override: Optional[str] = None 
) -> Optional[Any]:
    """
    Initializes and returns an LLM instance.
    - User-provided api_key takes precedence.
    - For "openai_compatible", if no user api_key, it checks the ENV var defined in config.
    - For "google_genai" and "anthropic", api_key MUST be provided (either by user or system ENV if that was the design, but current design is user-only for these).
    """
    print(f"LLM: Attempting to get model for provider='{provider_id}', model='{model_id}', base_url_override='{base_url_override}'")
    provider_config = config.get_provider_config(provider_id)

    if not provider_config:
        print(f"LLM_ERROR: Provider ID '{provider_id}' not found in LLM_PROVIDERS configuration.")
        return None

    provider_type = provider_config.get("type")
    
    final_base_url: Optional[str] = None
    if base_url_override and base_url_override.strip():
        final_base_url = base_url_override.strip()
        print(f"LLM: Using provided base_url_override: '{final_base_url}' for provider '{provider_id}'.")
    else:
        final_base_url = provider_config.get("base_url") 
        if provider_type == "ollama" and not final_base_url: 
            final_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            print(f"LLM_INFO: Ollama base URL not in provider_config or overridden, using OLLAMA_BASE_URL env var or default: '{final_base_url}'")

    if provider_type in ["ollama", "openai_compatible"] and not final_base_url:
        print(f"LLM_ERROR: Final base URL for provider '{provider_id}' (type: {provider_type}) is missing.")
        return None

    # API Key Resolution
    resolved_api_key = api_key # Prioritize user-provided key
    api_key_env_name = provider_config.get("api_key_env_var_name") # e.g., "OPENAI_COMPATIBLE_API_KEY" or None

    if not resolved_api_key and api_key_env_name: # If no user key AND an ENV var is specified for this provider type
        resolved_api_key = os.getenv(api_key_env_name)
        if resolved_api_key:
            print(f"LLM: Using API key from system environment variable '{api_key_env_name}' for provider '{provider_id}'.")
    
    # Now, check if a key is strictly required by the provider type and if we have one.
    # For Google and Anthropic, api_key_env_name is None in config, so this check relies on `resolved_api_key` (from user) being present.
    # For openai_compatible, api_key_env_name IS defined, so if resolved_api_key is still None here, it means neither user nor ENV key was found.

    try:
        if provider_type == "ollama":
            print(f"LLM: Initializing OllamaLLM with model='{model_id}', final_base_url='{final_base_url}'")
            model_instance = OllamaLLM(model=model_id, base_url=final_base_url)
            return model_instance

        elif provider_type == "openai_compatible":
            # OpenAI-compatible might work without a key if self-hosted & unsecured, but usually needs one.
            # The `api_key_env_var_name` being set in config implies a key is generally expected.
            if api_key_env_name and not resolved_api_key: # If it's expected via ENV fallback but not found
                 print(f"LLM_WARNING: API key for openai_compatible provider '{provider_id}' not found (checked user input and ENV var '{api_key_env_name}'). Proceeding without API key if possible.")
            print(f"LLM: Initializing ChatOpenAI for '{provider_id}' with model='{model_id}', final_base_url='{final_base_url}'")
            model_instance = ChatOpenAI(
                model_name=model_id,
                openai_api_base=final_base_url,
                openai_api_key=resolved_api_key, # Can be None
            )
            return model_instance
        
        elif provider_type == "google_genai":
            if not resolved_api_key: # Google GenAI strictly requires an API key from the user.
                print(f"LLM_ERROR: Google API Key is required for provider '{provider_id}' but was not provided by the user.")
                return None
            print(f"LLM: Initializing ChatGoogleGenerativeAI with model='{model_id}'")
            model_instance = ChatGoogleGenerativeAI(model=model_id, google_api_key=resolved_api_key)
            return model_instance

        elif provider_type == "anthropic":
            if not resolved_api_key: # Anthropic strictly requires an API key from the user.
                print(f"LLM_ERROR: Anthropic API Key is required for provider '{provider_id}' but was not provided by the user.")
                return None
            print(f"LLM: Initializing ChatAnthropic with model='{model_id}'")
            model_instance = ChatAnthropic(model=model_id, api_key=resolved_api_key)
            return model_instance
            
        else:
            print(f"LLM_ERROR: Unknown provider type '{provider_type}' for provider ID '{provider_id}'.")
            return None

    except Exception as e:
        print(f"LLM_CRITICAL_ERROR: Failed to initialize model for provider '{provider_id}', model '{model_id}': {e}")
        traceback.print_exc() 
        return None

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful AI assistant chatting in a web interface. Keep your thinking process very short! When you think only write down key information and how they relate. Answer the user's questions concisely. Always use katex for math ($...$ or $$...$$). For a literal dollar sign use \\$. When providing code, use standard markdown code blocks (e.g., ```python ... ```)."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}") 
])

output_parser = StrOutputParser()

def create_chain(
    provider_id: str,
    model_id: str,
    memory_loader_func: Callable[[dict], List[BaseMessage]],
    api_key: Optional[str] = None,
    base_url_override: Optional[str] = None
) -> Optional[Runnable]:
    print(f"LLM Chain: Creating chain with provider='{provider_id}', model='{model_id}', base_url_override='{base_url_override}'")
    try:
        current_model_instance = get_model(
            provider_id=provider_id, 
            model_id=model_id, 
            api_key=api_key,
            base_url_override=base_url_override
        )
        if not current_model_instance:
            print(f"LLM Chain ERROR: Failed to get model instance for provider '{provider_id}', model '{model_id}'. Cannot create chain.")
            return None 

        chain = (
            RunnablePassthrough.assign(history=RunnableLambda(memory_loader_func))
            | prompt
            | current_model_instance 
            | output_parser
        )
        print(f"LLM Chain: Successfully created chain for '{model_id}' from '{provider_id}'.")
        return chain
    except Exception as e:
        print(f"LLM Chain CRITICAL_ERROR: Failed to create LangChain chain: {e}")
        traceback.print_exc()
        return None

