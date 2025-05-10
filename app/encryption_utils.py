# app/encryption_utils.py
import os
from cryptography.fernet import Fernet, InvalidToken
# Import config from the current package
from . import config 
from typing import Optional

# Global variable to hold the Fernet instance, initialized once
_fernet_instance: Optional[Fernet] = None

def _get_fernet() -> Fernet:
    """
    Initializes and returns the Fernet instance for encryption/decryption.
    Raises ValueError if APP_SECRET_KEY is not configured or invalid.
    """
    global _fernet_instance
    if _fernet_instance is None:
        if not config.APP_SECRET_KEY:
            print("CRITICAL ERROR: APP_SECRET_KEY is not configured in the environment. "
                  "Cannot perform encryption/decryption of sensitive data.")
            raise ValueError("APP_SECRET_KEY is not configured. Encryption services are unavailable.")
        
        try:
            # APP_SECRET_KEY must be a URL-safe base64-encoded 32-byte key.
            key_bytes = config.APP_SECRET_KEY.encode('utf-8')
            _fernet_instance = Fernet(key_bytes)
            print("Fernet encryption service initialized successfully.")
        except Exception as e:
            # This can happen if the key is not correctly formatted (e.g., wrong length, not base64)
            print(f"CRITICAL ERROR: Failed to initialize Fernet with APP_SECRET_KEY: {e}. "
                  "Ensure APP_SECRET_KEY is a valid Fernet key (URL-safe base64 encoded 32-byte key). "
                  "You can generate one using: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
            raise ValueError(f"Invalid APP_SECRET_KEY format for Fernet encryption: {e}") from e
            
    return _fernet_instance

def encrypt_data(data: str) -> Optional[str]:
    """
    Encrypts a string using the configured Fernet instance.

    Args:
        data: The string data to encrypt.

    Returns:
        The encrypted string, or None if encryption fails or data is empty.
    """
    if not data:
        return None # Do not encrypt empty strings, return None or empty as per policy
    
    try:
        fernet_cipher = _get_fernet()
        encrypted_bytes = fernet_cipher.encrypt(data.encode('utf-8'))
        return encrypted_bytes.decode('utf-8') # Store the encrypted data as a string
    except ValueError as ve: # Raised by _get_fernet if APP_SECRET_KEY is not set/invalid
        print(f"Encryption failed due to configuration issue: {ve}")
        # Depending on policy, you might re-raise or return a specific error indicator.
        # For now, returning None as the operation could not be completed.
        return None
    except Exception as e:
        print(f"Error during data encryption: {e}")
        # import traceback
        # traceback.print_exc() # Uncomment for detailed stack trace during development
        return None

def decrypt_data(encrypted_data: str) -> Optional[str]:
    """
    Decrypts a string using the configured Fernet instance.

    Args:
        encrypted_data: The encrypted string data.

    Returns:
        The decrypted string, or None if decryption fails (e.g., invalid token, key mismatch, or data corruption)
        or if the input is empty.
    """
    if not encrypted_data:
        return None
    
    try:
        fernet_cipher = _get_fernet()
        decrypted_bytes = fernet_cipher.decrypt(encrypted_data.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    except InvalidToken:
        # This is a common error if the token is tampered with, the key is wrong,
        # or the data is not valid Fernet-encrypted data.
        print("Error during data decryption: Invalid token. Data might be corrupted or key mismatch.")
        return None
    except ValueError as ve: # Raised by _get_fernet if APP_SECRET_KEY is not set/invalid
        print(f"Decryption failed due to configuration issue: {ve}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during data decryption: {e}")
        # import traceback
        # traceback.print_exc() # Uncomment for detailed stack trace during development
        return None

# Example usage (for testing purposes, typically not run directly like this in production)
if __name__ == '__main__':
    # This block will only run if the script is executed directly.
    # It requires APP_SECRET_KEY to be set in the environment or .env file.
    print("Running encryption_utils.py for testing...")
    if not config.APP_SECRET_KEY:
        print("Skipping tests: APP_SECRET_KEY is not set in environment.")
    else:
        try:
            # Ensure Fernet can be initialized
            _get_fernet() 
            print("Fernet instance obtained successfully for testing.")

            original_text = "This is a super secret API key!"
            print(f"Original text: '{original_text}'")

            encrypted = encrypt_data(original_text)
            if encrypted:
                print(f"Encrypted text: '{encrypted}'")
                
                decrypted = decrypt_data(encrypted)
                if decrypted:
                    print(f"Decrypted text: '{decrypted}'")
                    assert original_text == decrypted, "Decryption did not match original!"
                    print("Encryption and decryption test successful!")
                else:
                    print("Decryption FAILED.")
            else:
                print("Encryption FAILED.")
            
            print("\nTesting with empty string:")
            encrypted_empty = encrypt_data("")
            print(f"Encrypting empty string: {encrypted_empty}")
            decrypted_empty_from_none = decrypt_data(None) # type: ignore
            print(f"Decrypting None: {decrypted_empty_from_none}")

            print("\nTesting decryption of invalid token:")
            invalid_decryption = decrypt_data("this_is_not_a_valid_fernet_token")
            print(f"Decrypting invalid token: {invalid_decryption}")

        except ValueError as e:
            print(f"Test failed due to ValueError (likely APP_SECRET_KEY issue): {e}")
        except Exception as e:
            print(f"An unexpected error occurred during testing: {e}")

