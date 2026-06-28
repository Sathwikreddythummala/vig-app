from config import settings


def normalize_employee_type(employee_type: str) -> str:
    return str(employee_type or "Driver").strip().lower()


def is_driver_record(record: dict) -> bool:
    return normalize_employee_type(record.get("EmployeeType", "Driver")) == "driver"


def normalize_role(role: str) -> str:
    role = str(role or "viewer").strip().lower()
    return role if role in {"admin", "editor", "viewer", "driver"} else "viewer"


def is_email_allowed(email: str) -> bool:
    email = str(email or "").strip().lower()
    if email in [e.strip().lower() for e in settings.ALLOWED_EMAILS if e]:
        return True
    from services.sheets_service import get_all_records
    try:
        users = get_all_records("Users")
        for u in users:
            if str(u.get("Email", "")).strip().lower() == email and str(u.get("Status", "")).strip().lower() == "active":
                return True
    except Exception:
        pass
    try:
        drivers = get_all_records("Drivers")
        for d in drivers:
            status = str(d.get("Status", "Active")).strip().lower()
            if str(d.get("Email", "")).strip().lower() == email and status != "inactive":
                return True
    except Exception:
        pass
    return False


def get_user_role(email: str) -> str:
    email = str(email or "").strip().lower()
    if email in [e.strip().lower() for e in settings.ALLOWED_EMAILS if e]:
        return "admin"
    from services.sheets_service import get_all_records
    try:
        users = get_all_records("Users")
        for u in users:
            if str(u.get("Email", "")).strip().lower() == email and str(u.get("Status", "")).strip().lower() == "active":
                return normalize_role(u.get("Role", "viewer"))
    except Exception:
        pass
    return "viewer"


def get_driver_by_email(email: str):
    email = str(email or "").strip().lower()
    from services.sheets_service import get_all_records
    try:
        drivers = get_all_records("Drivers")
        for d in drivers:
            status = str(d.get("Status", "Active")).strip().lower()
            if str(d.get("Email", "")).strip().lower() == email and status != "inactive":
                return d
    except Exception:
        pass
    return None


def is_admin(email: str) -> bool:
    return get_user_role(email) == "admin"
