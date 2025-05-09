# app/email_utils.py
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pathlib import Path
import traceback
from . import config # Import your config module

# --- Email Configuration ---
# Create the ConnectionConfig using settings from config.py
# This configuration is used to connect to the email server.
conf = ConnectionConfig(
    MAIL_USERNAME=config.MAIL_CONFIG.get("MAIL_USERNAME"),
    MAIL_PASSWORD=config.MAIL_CONFIG.get("MAIL_PASSWORD"),
    MAIL_FROM=config.MAIL_CONFIG.get("MAIL_FROM"),
    MAIL_PORT=config.MAIL_CONFIG.get("MAIL_PORT", 587), # Default to 587 if not in config
    MAIL_SERVER=config.MAIL_CONFIG.get("MAIL_SERVER"),
    MAIL_FROM_NAME=config.MAIL_CONFIG.get("MAIL_FROM_NAME"),
    MAIL_STARTTLS=config.MAIL_CONFIG.get("MAIL_STARTTLS", True), # Default to True
    MAIL_SSL_TLS=config.MAIL_CONFIG.get("MAIL_SSL_TLS", False),   # Default to False
    USE_CREDENTIALS=config.MAIL_CONFIG.get("USE_CREDENTIALS", True),
    VALIDATE_CERTS=config.MAIL_CONFIG.get("VALIDATE_CERTS", True) # Default to True
)

# Initialize FastMail instance with the configuration
fm = FastMail(conf)

# Define the base path for email templates
# config.APP_DIR should point to the 'app' directory.
EMAIL_TEMPLATES_DIR = config.APP_DIR / "static" / "email_templates"

async def send_registration_password_email(
    recipient_email: str,
    recipient_name: str,
    generated_password: str,
    login_url: str
) -> bool:
    """
    Sends the generated password to a new user using an HTML template.
    The password sent is the user's actual password.
    """
    # Basic check for essential mail server configuration
    if not conf.MAIL_FROM or not conf.MAIL_SERVER:
        print("EMAIL ERROR: Mail configuration (MAIL_FROM, MAIL_SERVER) is incomplete. Cannot send registration password email.")
        return False

    template_path = EMAIL_TEMPLATES_DIR / "registration_email.html"
    subject = "Welcome to Tesseracs Chat - Your Account Details"

    try:
        # Read the HTML template from file
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        # Replace placeholders in the template
        # Ensure all placeholders match those in the HTML template
        html_body = html_template.replace("{{ recipient_name }}", recipient_name)
        html_body = html_body.replace("{{ email }}", recipient_email) # Changed from {{ recipient_email }} for consistency
        html_body = html_body.replace("{{ password }}", generated_password)
        html_body = html_body.replace("{{ login_url }}", login_url)

    except FileNotFoundError:
        print(f"EMAIL ERROR: Registration email template not found at {template_path}")
        return False
    except Exception as e:
        print(f"EMAIL ERROR: Failed to read or process registration email template: {e}")
        traceback.print_exc()
        return False

    # Create the email message schema
    message = MessageSchema(
        subject=subject,
        recipients=[recipient_email],
        body=html_body,
        subtype="html"
    )

    try:
        # Attempt to send the email
        print(f"EMAIL: Attempting to send registration password to {recipient_email} via {conf.MAIL_SERVER}:{conf.MAIL_PORT}")
        await fm.send_message(message)
        print(f"EMAIL: Registration password email successfully sent to {recipient_email}.")
        return True  # Indicate success
    except Exception as e:
        # Log any errors during email sending
        print(f"EMAIL ERROR: Failed to send registration password email to {recipient_email}")
        print(f"EMAIL ERROR DETAILS: {type(e).__name__} - {e}")
        traceback.print_exc()
        return False # Indicate failure

async def send_password_reset_email(
    recipient_email: str,
    recipient_name: str,
    new_password: str,
    login_url: str
) -> bool:
    """
    Sends an email containing a newly generated password after a reset request,
    using an HTML template.
    """
    # Basic check for essential mail server configuration
    if not conf.MAIL_FROM or not conf.MAIL_SERVER:
        print("EMAIL ERROR: Mail configuration (MAIL_FROM, MAIL_SERVER) is incomplete. Cannot send password reset email.")
        return False

    template_path = EMAIL_TEMPLATES_DIR / "password_reset_email.html"
    subject = "Your Tesseracs Chat Password Has Been Reset"

    try:
        # Read the HTML template from file
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        # Replace placeholders in the template
        # Ensure all placeholders match those in the HTML template
        html_body = html_template.replace("{{ recipient_name }}", recipient_name)
        html_body = html_body.replace("{{ email }}", recipient_email) # Changed from {{ recipient_email }} for consistency
        html_body = html_body.replace("{{ password }}", new_password)
        html_body = html_body.replace("{{ login_url }}", login_url)

    except FileNotFoundError:
        print(f"EMAIL ERROR: Password reset email template not found at {template_path}")
        return False
    except Exception as e:
        print(f"EMAIL ERROR: Failed to read or process password reset email template: {e}")
        traceback.print_exc()
        return False

    # Create the email message schema
    message = MessageSchema(
        subject=subject,
        recipients=[recipient_email],
        body=html_body,
        subtype="html"
    )

    try:
        # Attempt to send the email
        print(f"EMAIL: Attempting to send password reset to {recipient_email} via {conf.MAIL_SERVER}:{conf.MAIL_PORT}")
        await fm.send_message(message)
        print(f"EMAIL: Password reset email successfully sent to {recipient_email}.")
        return True # Indicate success
    except Exception as e:
        # Log any errors during email sending
        print(f"EMAIL ERROR: Failed to send password reset email to {recipient_email}")
        print(f"EMAIL ERROR DETAILS: {type(e).__name__} - {e}")
        traceback.print_exc()
        return False # Indicate failure
