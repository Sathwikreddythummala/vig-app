from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from services.sheets_service import (
    get_all_records, append_row, gen_id, now_str, add_audit_log,
)
from utils.templates import templates
from datetime import datetime

router = APIRouter(prefix="/driver-portal", tags=["driver-portal"])


def get_driver_user(request: Request):
    user = request.session.get("user")
    if not user or user.get("role") != "driver":
        return None
    return user


@router.get("")
async def portal_home(request: Request):
    user = get_driver_user(request)
    if not user:
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="driver_portal.html", context={"user": user})


@router.get("/api/my-data")
async def my_data(request: Request, month: str = ""):
    user = get_driver_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    driver_name = user.get("driver_name", "")
    if not month:
        month = datetime.now().strftime("%Y-%m")
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
    from utils.duplicate_check import is_duplicate
    if is_duplicate("FuelEntries", {
        "EntryDate": data.get("Date", datetime.now().strftime("%Y-%m-%d")),
        "VehicleNumber": vehicle,
        "DriverName": user.get("driver_name", ""),
        "FuelType": data.get("FuelType", "Diesel"),
        "Litres": data.get("Litres", ""),
        "Amount": data.get("Amount", 0),
        "Kilometre": data.get("Kilometre", ""),
    }):
        return JSONResponse({"error": "Duplicate entry already exists"}, 400)
    fid = gen_id("FUEL")
    row = [
        fid,
        data.get("Date", datetime.now().strftime("%Y-%m-%d")),
        vehicle,
        user.get("driver_name", ""),
        data.get("FuelType", "Diesel"),
        data.get("Litres", ""),
        data.get("Amount", 0),
        data.get("Kilometre", ""),
        data.get("FuelStation", ""),
        data.get("PaymentMode", "Cash"),
        now_str(),
    ]
    append_row("FuelEntries", row)
    add_audit_log("CREATE", "FuelEntries", fid,
                  f"Fuel ₹{data.get('Amount',0)} by driver {user.get('driver_name','')}",
                  user["email"])
    return {"success": True, "fuel_id": fid}
