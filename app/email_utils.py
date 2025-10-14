# app/email_utils.py
import smtplib
import ssl
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from . import config

EMAIL_TEMPLATES_DIR = config.APP_DIR / "static" / "email_templates"

def create_smtp_server():
    """Creates and returns an SMTP server instance based on config."""
    mail_config = config.MAIL_CONFIG
    if mail_config.get("MAIL_SSL_TLS"):
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(mail_config["MAIL_SERVER"], mail_config["MAIL_PORT"], context=context)
    else:
        server = smtplib.SMTP(mail_config["MAIL_SERVER"], mail_config["MAIL_PORT"])
    
    if mail_config.get("MAIL_STARTTLS"):
        server.starttls()
        
    if mail_config.get("USE_CREDENTIALS"):
        server.login(mail_config["MAIL_USERNAME"], mail_config["MAIL_PASSWORD"])
        
    return server

async def send_registration_password_email(
    recipient_email: str,
    recipient_name: str,
    generated_password: str,
    login_url: str
) -> bool:
    if not config.MAIL_CONFIG.get("MAIL_FROM") or not config.MAIL_CONFIG.get("MAIL_SERVER"):
        print("EMAIL ERROR: Mail configuration is incomplete.")
        return False

    subject = "Welcome to Tesseracs Chat - Your Account Details"
    template_path = EMAIL_TEMPLATES_DIR / "registration_email.html"

    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        html_body = html_template.replace("{{ recipient_name }}", recipient_name)
        html_body = html_body.replace("{{ email }}", recipient_email)
        html_body = html_body.replace("{{ password }}", generated_password)
        html_body = html_body.replace("{{ login_url }}", login_url)

        text_body = f"""
        Hello {recipient_name},

        Welcome to Tesseracs Chat! Your account has been created.
        Email: {recipient_email}
        Password: {generated_password}

        You can log in at: {login_url}
        """

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{config.MAIL_CONFIG['MAIL_FROM_NAME']} <{config.MAIL_CONFIG['MAIL_FROM']}>"
        message["To"] = recipient_email

        message.attach(MIMEText(text_body, "plain"))
        message.attach(MIMEText(html_body, "html"))

        with create_smtp_server() as server:
            server.sendmail(config.MAIL_CONFIG["MAIL_FROM"], recipient_email, message.as_string())
            
        print(f"EMAIL: Registration email successfully sent to {recipient_email}.")
        return True

    except Exception as e:
        print(f"EMAIL ERROR: Failed to send registration email: {e}")
        traceback.print_exc()
        return False

async def send_password_reset_email(
    recipient_email: str,
    recipient_name: str,
    new_password: str,
    login_url: str
) -> bool:
    if not config.MAIL_CONFIG.get("MAIL_FROM") or not config.MAIL_CONFIG.get("MAIL_SERVER"):
        print("EMAIL ERROR: Mail configuration is incomplete.")
        return False

    subject = "Your Tesseracs Chat Password Has Been Reset"
    template_path = EMAIL_TEMPLATES_DIR / "password_reset_email.html"
    
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        html_body = html_template.replace("{{ recipient_name }}", recipient_name)
        html_body = html_body.replace("{{ email }}", recipient_email)
        html_body = html_body.replace("{{ password }}", new_password)
        html_body = html_body.replace("{{ login_url }}", login_url)

        text_body = f"""
        Hello {recipient_name},

        Your password for Tesseracs Chat has been reset.
        Your new password is: {new_password}

        You can log in at: {login_url}
        """

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{config.MAIL_CONFIG['MAIL_FROM_NAME']} <{config.MAIL_CONFIG['MAIL_FROM']}>"
        message["To"] = recipient_email

        message.attach(MIMEText(text_body, "plain"))
        message.attach(MIMEText(html_body, "html"))

        with create_smtp_server() as server:
            server.sendmail(config.MAIL_CONFIG["MAIL_FROM"], recipient_email, message.as_string())

        print(f"EMAIL: Password reset email successfully sent to {recipient_email}.")
        return True

    except Exception as e:
        print(f"EMAIL ERROR: Failed to send password reset email: {e}")
        traceback.print_exc()
        return False

