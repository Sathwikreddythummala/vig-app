import os
import json
import tempfile
from dotenv import load_dotenv

load_dotenv()


def _get_service_account_file():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if creds_json:
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp.write(creds_json)
        tmp.close()
        return tmp.name
    return os.getenv("SERVICE_ACCOUNT_FILE", "credentials.json")


class Settings:
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")
    GOOGLE_DRIVE_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    ALLOWED_EMAILS: list = [e.strip() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()]
    SERVICE_ACCOUNT_FILE: str = _get_service_account_file()
    SCOPES: list = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SESSION_MAX_AGE: int = 60 * 60 * 24 * 365  # 1 year — session persists until explicit logout
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_VERIFY_SERVICE_SID: str = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")


settings = Settings()
