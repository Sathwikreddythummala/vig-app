from config import settings


def is_email_allowed(email: str) -> bool:
    if email.lower() in [e.lower() for e in settings.ALLOWED_EMAILS if e]:
        return True
    from services.sheets_service import get_all_records
    try:
        users = get_all_records("Users")
        for u in users:
            if str(u.get("Email", "")).strip().lower() == email.lower() and str(u.get("Status", "")) == "Active":
                return True
    except Exception:
        pass
    try:
        drivers = get_all_records("Drivers")
        for d in drivers:
            if str(d.get("Email", "")).strip().lower() == email.lower():
                return True
    except Exception:
        pass
    return False


def get_user_role(email: str) -> str:
    if email.lower() in [e.lower() for e in settings.ALLOWED_EMAILS if e]:
        return "admin"
    from services.sheets_service import get_all_records
    try:
        users = get_all_records("Users")
        for u in users:
            if str(u.get("Email", "")).strip().lower() == email.lower() and str(u.get("Status", "")) == "Active":
                return str(u.get("Role", "viewer")).lower()
    except Exception:
        pass
    return "viewer"


def get_driver_by_email(email: str):
    from services.sheets_service import get_all_records
    try:
        drivers = get_all_records("Drivers")
        for d in drivers:
            if str(d.get("Email", "")).strip().lower() == email.lower():
                return d
    except Exception:
        pass
    return None


def is_admin(email: str) -> bool:
    return get_user_role(email) == "admin"
