import os
if os.getenv("RENDER") is None:
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from config import settings
from routes import auth, dashboard, vehicles, drivers, expenses, emi, vendors, driver_portal, fuel, billing, purse, gst, access
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


app = FastAPI(title="Vigneshwara Enterprises Fleet Management", lifespan=lifespan)
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
app.include_router(access.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
