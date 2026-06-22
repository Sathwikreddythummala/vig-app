from fastapi import Request
from fastapi.responses import JSONResponse


def get_user(request: Request):
    return request.session.get("user")


def require_editor(request: Request):
    user = request.session.get("user")
    if not user:
        return None, JSONResponse({"error": "Unauthorized"}, 401)
    if user.get("role") == "viewer":
        return None, JSONResponse({"error": "You have read-only access"}, 403)
    return user, None
