import os
if os.getenv("RENDER") is None:
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from config import settings
from routes import auth, dashboard, vehicles, drivers, expenses, emi, vendors, driver_portal, fuel, billing, purse, gst, access, outside
from services.sheets_service import initialize_sheets
from services.drive_service import initialize_drive_folders


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        initialize_sheets()
        initialize_drive_folders()
        print("Google Sheets and Drive initialized successfully")
    except Exception as e:
        print(f"Initialization warning: {e}")
        print("Ensure credentials.json is present and SPREADSHEET_ID is set")
    yield


app = FastAPI(title="TSR Enterprises Fleet Management", lifespan=lifespan)
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse as JR


class ViewerGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        user = request.session.get("user") if "session" in request.scope else None
        if user and user.get("role") == "viewer":
            if request.method in ("POST", "PUT", "DELETE"):
                path = request.url.path
                if "/api/" in path and "/api/list" not in path and "/api/my-data" not in path and "/api/summary" not in path and "/api/stats" not in path and "/api/categories" not in path and "/api/sales" not in path and "/api/purchases" not in path and "/api/receivables" not in path:
                    return JR({"error": "Viewers cannot make changes"}, status_code=403)
        return await call_next(request)


app.add_middleware(ViewerGuardMiddleware)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
