import secrets
key = secrets.token_urlsafe(32)  # Generates a 32-byte random string, URL-safe
print(key)