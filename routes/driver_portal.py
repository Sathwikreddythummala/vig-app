from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row,
    build_row, gen_id, now_str, add_audit_log,
)
from services.auth_service import is_driver_record
from utils.templates import templates
from datetime import datetime
from zoneinfo import ZoneInfo
_IST = ZoneInfo("Asia/Kolkata")

router = APIRouter(prefix="/driver-portal", tags=["driver-portal"])

PORTAL_CODE_KEY = "DriverPortalCode"


# ---------------------------------------------------------------------------
# Identity: a driver may arrive two ways —
#   1) Google login whose email maps to a Driver record (role == "driver")
#   2) Shared access-code login, stored in session["portal_driver"]
# get_driver_user() returns a uniform dict for either, or None.
# ---------------------------------------------------------------------------
def get_driver_user(request: Request):
    user = request.session.get("user")
    if user and user.get("role") == "driver":
        return {
            "driver_name": user.get("driver_name", ""),
            "assigned_vehicle": user.get("assigned_vehicle", ""),
            "email": user.get("email", ""),
            "picture": user.get("picture", ""),
            "via": "google",
        }
    pd = request.session.get("portal_driver")
    if pd and pd.get("name"):
        return {
            "driver_name": pd.get("name", ""),
            "assigned_vehicle": pd.get("vehicle", ""),
            "email": "driver-portal",
            "picture": "",
            "via": "code",
        }
    return None


def _active_drivers() -> list[dict]:
    out = []
    for d in get_all_records("Drivers"):
        status = str(d.get("Status", "Active")).strip().lower()
        if status == "inactive":
            continue
        if not is_driver_record(d):
            continue
        out.append(d)
    out.sort(key=lambda x: str(x.get("DriverName", "")).lower())
    return out


def get_portal_code() -> str:
    for s in get_all_records("Settings"):
        if str(s.get("Key", "")) == PORTAL_CODE_KEY:
            return str(s.get("Value", "")).strip()
    return ""


def set_portal_code(code: str):
    code = str(code).strip()
    existing = None
    for s in get_all_records("Settings"):
        if str(s.get("Key", "")) == PORTAL_CODE_KEY:
            existing = s
            break
    if existing:
        row = build_row("Settings", {"Key": PORTAL_CODE_KEY, "Value": code, "UpdatedDate": now_str()})
        update_row("Settings", PORTAL_CODE_KEY, row)
    else:
        row = build_row("Settings", {"Key": PORTAL_CODE_KEY, "Value": code, "UpdatedDate": now_str()})
        append_row("Settings", row)


# ---------------------------------------------------------------------------
# Shared-code login flow
# ---------------------------------------------------------------------------
@router.get("/login")
async def portal_login_page(request: Request):
    # Already signed in? go straight to the portal.
    if get_driver_user(request):
        return RedirectResponse("/driver-portal")
    return templates.TemplateResponse(request=request, name="driver_login.html", context={})


@router.post("/api/verify-code")
async def verify_code(request: Request):
    data = await request.json()
    code = str(data.get("code", "")).strip()
    portal_code = get_portal_code()
    if not portal_code:
        return JSONResponse({"error": "Driver app access code is not set yet. Ask the office to set it."}, 400)
    if code != portal_code:
        return JSONResponse({"error": "Wrong access code"}, 401)
    # Code ok -> allow fetching the roster for this attempt.
    request.session["portal_code_ok"] = True
    drivers = [
        {"name": d.get("DriverName", ""), "vehicle": d.get("AssignedVehicle", "")}
        for d in _active_drivers() if d.get("DriverName", "")
    ]
    return {"success": True, "drivers": drivers}


@router.post("/api/login")
async def portal_login(request: Request):
    data = await request.json()
    code = str(data.get("code", "")).strip()
    name = str(data.get("name", "")).strip()
    portal_code = get_portal_code()
    if not portal_code or code != portal_code:
        return JSONResponse({"error": "Wrong access code"}, 401)
    driver = None
    for d in _active_drivers():
        if str(d.get("DriverName", "")).strip() == name:
            driver = d
            break
    if not driver:
        return JSONResponse({"error": "Select your name"}, 400)
    request.session.pop("portal_code_ok", None)
    request.session["portal_driver"] = {
        "name": driver.get("DriverName", ""),
        "vehicle": driver.get("AssignedVehicle", ""),
    }
    add_audit_log("LOGIN", "DriverPortal", driver.get("DriverID", ""),
                  f"Driver {driver.get('DriverName','')} signed in via access code", "driver-portal")
    return {"success": True}


@router.get("/logout")
async def portal_logout(request: Request):
    # Clear both the shared-code session and (if present) a Google driver session.
    request.session.pop("portal_driver", None)
    request.session.pop("portal_code_ok", None)
    if request.session.get("user"):
        request.session.clear()
    return RedirectResponse("/driver-portal/login")


# ---------------------------------------------------------------------------
# Service worker (served from this prefix so its scope covers /driver-portal)
# ---------------------------------------------------------------------------
_SERVICE_WORKER = """
const CACHE = 'fleet-driver-v1';
const SHELL = ['/driver-portal', '/driver-portal/login', '/static/img/driver-app-icon.svg'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(()=>{}));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // never cache POSTs (data entry)
  const url = new URL(req.url);
  if (url.pathname.includes('/api/')) return;       // always fresh data
  e.respondWith(
    fetch(req).then(res => {
      if (res && res.ok && url.origin === location.origin) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy)).catch(()=>{});
      }
      return res;
    }).catch(() => caches.match(req).then(r => r || caches.match('/driver-portal')))
  );
});
"""


@router.get("/sw.js")
async def service_worker():
    return Response(
        content=_SERVICE_WORKER,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/driver-portal", "Cache-Control": "no-cache"},
    )


# ---------------------------------------------------------------------------
# Admin: manage the driver-app access code + share/install link
# ---------------------------------------------------------------------------
def _is_admin_user(request: Request) -> bool:
    user = request.session.get("user")
    return bool(user and user.get("role") in ("admin", "editor"))


@router.get("/manage")
async def portal_manage(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/auth/login-page")
    if user.get("role") not in ("admin", "editor"):
        return RedirectResponse("/driver-portal")
    return templates.TemplateResponse(
        request=request, name="driver_app_manage.html",
        context={"user": user, "code": get_portal_code()},
    )


@router.post("/api/set-code")
async def portal_set_code(request: Request):
    if not _is_admin_user(request):
        return JSONResponse({"error": "Admins only"}, 403)
    data = await request.json()
    code = str(data.get("code", "")).strip()
    if len(code) < 4:
        return JSONResponse({"error": "Use at least 4 characters"}, 400)
    set_portal_code(code)
    user = request.session.get("user", {})
    add_audit_log("UPDATE", "DriverPortal", PORTAL_CODE_KEY,
                  "Driver app access code changed", user.get("email", ""))
    return {"success": True}


# ---------------------------------------------------------------------------
# The portal itself
# ---------------------------------------------------------------------------
@router.get("")
async def portal_home(request: Request):
    user = get_driver_user(request)
    if not user:
        return RedirectResponse("/driver-portal/login")
    return templates.TemplateResponse(request=request, name="driver_portal.html", context={"user": user})


@router.get("/api/my-data")
async def my_data(request: Request, month: str = ""):
    user = get_driver_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    driver_name = user.get("driver_name", "")
    if not month:
        month = datetime.now(_IST).strftime("%Y-%m")
    month_start = month + "-01"
    month_parts = month.split("-")
    y, m = int(month_parts[0]), int(month_parts[1])
    if m == 12:
        month_end = f"{y+1}-01-01"
    else:
        month_end = f"{y}-{m+1:02d}-01"
    drivers = get_all_records("Drivers")
    driver = None
    for d in drivers:
        if d.get("DriverName", "") == driver_name:
            driver = d
            break
    if not driver:
        return JSONResponse({"error": "Driver not found"}, 404)
    expenses = get_all_records("Expenses")
    my_expenses = [e for e in expenses if str(e.get("DriverName", "")) == driver_name]
    month_expenses = [e for e in my_expenses if month_start <= str(e.get("ExpenseDate", "")) < month_end]
    month_expenses.sort(key=lambda x: str(x.get("ExpenseDate", "")), reverse=True)
    def get_for_month(e):
        fm = str(e.get("ForMonth", "")).strip()
        if fm:
            return fm
        return str(e.get("ExpenseDate", ""))[:7]
    salary_entries = [e for e in my_expenses if e.get("SubCategory") == "Salary" and get_for_month(e) == month]
    advance_entries = [e for e in my_expenses if e.get("SubCategory") == "Advance" and get_for_month(e) == month]
    meals_entries = [e for e in my_expenses if e.get("SubCategory") == "Meals" and get_for_month(e) == month]
    fuel_records = get_all_records("FuelEntries")
    diesel_entries = [f for f in fuel_records if str(f.get("DriverName", "")) == driver_name and month_start <= str(f.get("EntryDate", "")) < month_end]
    diesel_entries.sort(key=lambda x: str(x.get("EntryDate", "")), reverse=True)
    total_salary = sum(float(e.get("Amount", 0) or 0) for e in salary_entries)
    total_advance = sum(float(e.get("Amount", 0) or 0) for e in advance_entries)
    total_meals = sum(float(e.get("Amount", 0) or 0) for e in meals_entries)
    month_diesel_litres = sum(float(f.get("Litres", 0) or 0) for f in diesel_entries)
    return {
        "driver": driver,
        "month": month,
        "salary_entries": salary_entries,
        "advance_entries": advance_entries,
        "meals_entries": meals_entries,
        "diesel_entries": diesel_entries,
        "total_salary": total_salary,
        "total_advance": total_advance,
        "total_meals": total_meals,
        "month_diesel_litres": month_diesel_litres,
        "recent_expenses": month_expenses,
    }


@router.post("/api/diesel")
async def add_diesel(request: Request):
    user = get_driver_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    vehicle = user.get("assigned_vehicle", "")
    if data.get("VehicleNumber"):
        vehicle = data["VehicleNumber"]
    status = (data.get("PaymentStatus") or "Paid").strip() or "Paid"
    station = str(data.get("FuelStation", "")).strip()
    if status == "Unpaid" and not station:
        return JSONResponse({"error": "Enter the fuel station for credit (unpaid) diesel"}, 400)
    from utils.duplicate_check import is_duplicate
    if is_duplicate("FuelEntries", {
        "EntryDate": data.get("Date", datetime.now(_IST).strftime("%Y-%m-%d")),
        "VehicleNumber": vehicle,
        "DriverName": user.get("driver_name", ""),
        "FuelType": data.get("FuelType", "Diesel"),
        "Litres": data.get("Litres", ""),
        "Amount": data.get("Amount", 0),
        "Kilometre": data.get("Kilometre", ""),
    }):
        return JSONResponse({"error": "Duplicate entry already exists"}, 400)
    fid = gen_id("FUEL")
    vals = {
        "FuelID": fid,
        "EntryDate": data.get("Date", datetime.now(_IST).strftime("%Y-%m-%d")),
        "VehicleNumber": vehicle,
        "DriverName": user.get("driver_name", ""),
        "FuelType": data.get("FuelType", "Diesel"),
        "Litres": data.get("Litres", ""),
        "Amount": data.get("Amount", 0),
        "Kilometre": data.get("Kilometre", ""),
        "FuelStation": station,
        "PaymentMode": data.get("PaymentMode", "Cash"),
        "PaymentStatus": status,
        "PaidDate": now_str()[:10] if status == "Paid" else "",
        "CreatedDate": now_str(),
    }
    row = build_row("FuelEntries", vals)
    append_row("FuelEntries", row)
    add_audit_log("CREATE", "FuelEntries", fid,
                  f"Fuel Rs.{data.get('Amount',0)} ({status}) by driver {user.get('driver_name','')}",
                  user["email"])
    return {"success": True, "fuel_id": fid}
