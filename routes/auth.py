from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from services.auth_service import is_email_allowed, is_admin, get_driver_by_email, get_user_role
from services.sheets_service import add_audit_log
from utils.templates import templates
from config import settings
from urllib.parse import urlencode
import httpx
import secrets

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


@router.get("/login")
async def login(request: Request):
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
async def callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        })
        if token_resp.status_code != 200:
            raise HTTPException(status_code=401, detail=f"Token exchange failed: {token_resp.text}")
        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user info")
        user_info = user_resp.json()
    email = user_info.get("email", "")
    if not is_email_allowed(email):
        return RedirectResponse("/auth/unauthorized")
    driver = get_driver_by_email(email)
    if driver and driver.get("EmployeeType", "Driver") == "Driver":
        request.session["user"] = {
            "email": email,
            "name": driver.get("DriverName", user_info.get("name", "")),
            "picture": user_info.get("picture", ""),
            "role": "driver",
            "driver_id": driver.get("DriverID", ""),
            "driver_name": driver.get("DriverName", ""),
            "assigned_vehicle": driver.get("AssignedVehicle", ""),
        }
        try:
            add_audit_log("LOGIN", "Auth", "", f"Driver {email} logged in", email)
        except Exception:
            pass
        return RedirectResponse("/driver-portal")
    else:
        role = get_user_role(email)
        request.session["user"] = {
            "email": email,
            "name": user_info.get("name", ""),
            "picture": user_info.get("picture", ""),
            "role": role,
        }
        try:
            add_audit_log("LOGIN", "Auth", "", f"{role.title()} {email} logged in", email)
        except Exception:
            pass
        return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    user = request.session.get("user", {})
    try:
        add_audit_log("LOGOUT", "Auth", "", f"User {user.get('email','')} logged out", user.get("email", ""))
    except Exception:
        pass
    request.session.clear()
    return RedirectResponse("/auth/login-page")


@router.get("/login-page")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/unauthorized")
async def unauthorized(request: Request):
    return templates.TemplateResponse(request=request, name="unauthorized.html")
