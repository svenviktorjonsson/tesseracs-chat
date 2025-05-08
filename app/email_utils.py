# app/email_utils.py
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pathlib import Path
import traceback
from . import config # Import your config module

# Create the ConnectionConfig using settings from config.py
# REMOVED TEMPLATE_FOLDER setting as we are not using file-based templates currently.
conf = ConnectionConfig(
    MAIL_USERNAME=config.MAIL_CONFIG.get("MAIL_USERNAME"),
    MAIL_PASSWORD=config.MAIL_CONFIG.get("MAIL_PASSWORD"),
    MAIL_FROM=config.MAIL_CONFIG.get("MAIL_FROM"),
    MAIL_PORT=config.MAIL_CONFIG.get("MAIL_PORT", 587),
    MAIL_SERVER=config.MAIL_CONFIG.get("MAIL_SERVER"),
    MAIL_FROM_NAME=config.MAIL_CONFIG.get("MAIL_FROM_NAME"),
    MAIL_STARTTLS=config.MAIL_CONFIG.get("MAIL_STARTTLS", True),
    MAIL_SSL_TLS=config.MAIL_CONFIG.get("MAIL_SSL_TLS", False),
    USE_CREDENTIALS=config.MAIL_CONFIG.get("USE_CREDENTIALS", True),
    VALIDATE_CERTS=config.MAIL_CONFIG.get("VALIDATE_CERTS", True)
    # TEMPLATE_FOLDER removed
)

# Initialize FastMail instance
fm = FastMail(conf)

async def send_magic_link_email(
    recipient_email: str,
    recipient_name: str,
    magic_link: str,
    duration_minutes: int
):
    """Sends the magic link email asynchronously."""

    # Basic check if email config seems valid before attempting
    if not conf.MAIL_USERNAME or not conf.MAIL_PASSWORD or not conf.MAIL_SERVER:
        print("EMAIL ERROR: Mail configuration is incomplete. Cannot send email.")
        # In a real app, you might raise an internal error or log more formally
        return False # Indicate failure

    subject = "Your Tesseracs Chat Login Link"

    # Simple HTML Body (consider using templates for more complex emails later)
    html_body = f"""
    <html>
        <body>
            <h2>Tesseracs Chat Login</h2>
            <p>Hello {recipient_name},</p>
            <p>Click the button below or copy the link to log in to Tesseracs Chat. This link is valid for {duration_minutes} minutes.</p>
            <p style="text-align: center; margin: 25px 0;">
                <a href="{magic_link}"
                   style="background-color: #2563eb; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-size: 16px;"
                   target="_blank">
                   Login to Tesseracs Chat
                </a>
            </p>
            <p>If the button doesn't work, copy and paste this link into your browser:</p>
            <p><a href="{magic_link}" target="_blank">{magic_link}</a></p>
            <p>If you didn't request this email, please ignore it.</p>
            <hr>
            <p><small>Sent from Tesseracs Chat</small></p>
        </body>
    </html>
    """

    message = MessageSchema(
        subject=subject,
        recipients=[recipient_email],
        body=html_body, # Use body for HTML content
        subtype="html" # Specify HTML subtype
    )

    try:
        print(f"EMAIL: Attempting to send magic link to {recipient_email} via {conf.MAIL_SERVER}:{conf.MAIL_PORT}")
        await fm.send_message(message)
        print(f"EMAIL: Magic link email successfully sent to {recipient_email}.")
        return True # Indicate success
    except Exception as e:
        print(f"EMAIL ERROR: Failed to send magic link email to {recipient_email}")
        print(f"EMAIL ERROR DETAILS: {type(e).__name__} - {e}")
        traceback.print_exc()
        # Depending on the error, you might want specific handling
        # e.g., authentication errors (bad password), connection errors, etc.
        return False # Indicate failure

