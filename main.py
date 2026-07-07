import os
if os.getenv("RENDER") is None:
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from config import settings
from routes import auth, dashboard, vehicles, drivers, expenses, emi, vendors, driver_portal, fuel, billing, purse, gst, access, outside, attendance, documents
from services.sheets_service import initialize_sheets
from services.drive_service import initialize_drive_folders


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        initialize_sheets()
        print("PostgreSQL database initialized successfully")
    except Exception as e:
        print(f"DB initialization warning: {e}")
    try:
        initialize_drive_folders()
        print("Google Drive initialized successfully")
    except Exception as e:
        print(f"Drive initialization warning: {e}")
    yield


app = FastAPI(title="Vigneshwara Enterprises Fleet Management", lifespan=lifespan)
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse as JR


import time as _time

DRIVER_ALLOWED_PATHS = ("/driver-portal", "/auth", "/static")


class RoleEnforcementMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        user = request.session.get("user") if "session" in request.scope else None
        if not user:
            return await call_next(request)

        path = request.url.path
        if path.startswith(("/static", "/auth")):
            return await call_next(request)

        from fastapi.responses import RedirectResponse as RR

        # Re-validate role on every non-static request (uses cached sheet data so it's fast)
        try:
            from services.auth_service import get_user_role, is_email_allowed, get_driver_by_email, is_driver_record
            email = user.get("email", "")
            if not is_email_allowed(email):
                request.session.clear()
                return RR("/auth/login-page")
            driver = get_driver_by_email(email)
            if driver and is_driver_record(driver):
                if user.get("role") != "driver":
                    user["role"] = "driver"
                    user["driver_id"] = driver.get("DriverID", "")
                    user["driver_name"] = driver.get("DriverName", "")
                    user["assigned_vehicle"] = driver.get("AssignedVehicle", "")
                    request.session["user"] = user
            else:
                fresh_role = get_user_role(email)
                if fresh_role != user.get("role"):
                    user["role"] = fresh_role
                    request.session["user"] = user
        except Exception:
            pass

        # Drivers can ONLY access driver-portal routes
        if user.get("role") == "driver":
            if not any(path.startswith(p) for p in DRIVER_ALLOWED_PATHS):
                return RR("/driver-portal")

        # Viewers cannot make changes
        if user.get("role") == "viewer":
            if request.method in ("POST", "PUT", "DELETE"):
                if "/api/" in path and "/api/list" not in path and "/api/my-data" not in path and "/api/summary" not in path and "/api/stats" not in path and "/api/categories" not in path and "/api/sales" not in path and "/api/purchases" not in path and "/api/receivables" not in path:
                    return JR({"error": "Viewers cannot make changes"}, status_code=403)

        return await call_next(request)


app.add_middleware(RoleEnforcementMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, max_age=settings.SESSION_MAX_AGE)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(vehicles.router)
app.include_router(drivers.router)
app.include_router(expenses.router)
app.include_router(emi.router)
app.include_router(vendors.router)
app.include_router(driver_portal.router)
app.include_router(fuel.router)
app.include_router(billing.router)
app.include_router(purse.router)
app.include_router(gst.router)
app.include_router(outside.router)
app.include_router(access.router)
app.include_router(attendance.router)
app.include_router(documents.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
