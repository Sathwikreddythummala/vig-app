from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates
from collections import defaultdict
from datetime import datetime

router = APIRouter(prefix="/fuel", tags=["fuel"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def fuel_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="fuel.html", context={"user": user})


@router.get("/api/list")
async def list_fuel(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    driver: str = "",
    fuel_type: str = "",
    page: int = 1,
    per_page: int = 25,
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("FuelEntries")
    if date_from:
        records = [r for r in records if str(r.get("EntryDate", "")) >= date_from]
    if date_to:
        records = [r for r in records if str(r.get("EntryDate", "")) <= date_to]
    if vehicle:
        records = [r for r in records if str(r.get("VehicleNumber", "")) == vehicle]
    if driver:
        records = [r for r in records if str(r.get("DriverName", "")) == driver]
    if fuel_type:
        records = [r for r in records if str(r.get("FuelType", "")) == fuel_type]
    records.sort(key=lambda x: str(x.get("EntryDate", "")), reverse=True)
    total = len(records)
    total_amount = sum(float(r.get("Amount", 0) or 0) for r in records)
    total_litres = sum(float(r.get("Litres", 0) or 0) for r in records)
    start = (page - 1) * per_page
    paginated = records[start:start + per_page]
    return {
        "entries": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 1,
        "total_amount": total_amount,
        "total_litres": total_litres,
    }


@router.get("/api/stats")
async def fuel_stats(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("FuelEntries")
    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().strftime("%Y-%m-01")
    today_records = [r for r in records if str(r.get("EntryDate", "")) == today]
    month_records = [r for r in records if str(r.get("EntryDate", "")) >= month_start]
    total_today = sum(float(r.get("Amount", 0) or 0) for r in today_records)
    total_month = sum(float(r.get("Amount", 0) or 0) for r in month_records)
    month_litres = sum(float(r.get("Litres", 0) or 0) for r in month_records)
    vehicle_wise = defaultdict(float)
    driver_wise = defaultdict(float)
    for r in month_records:
        vehicle_wise[str(r.get("VehicleNumber", "")) or "Unknown"] += float(r.get("Amount", 0) or 0)
        driver_wise[str(r.get("DriverName", "")) or "Unknown"] += float(r.get("Amount", 0) or 0)
    monthly_trend = defaultdict(float)
    for r in records:
        d = str(r.get("EntryDate", ""))
        if len(d) >= 7:
            monthly_trend[d[:7]] += float(r.get("Amount", 0) or 0)
    sorted_months = sorted(monthly_trend.keys())[-12:]
    return {
        "total_today": total_today,
        "total_month": total_month,
        "month_litres": month_litres,
        "month_entries": len(month_records),
        "vehicle_wise": {"labels": list(vehicle_wise.keys()), "values": list(vehicle_wise.values())},
        "driver_wise": {"labels": list(driver_wise.keys()), "values": list(driver_wise.values())},
        "monthly_trend": {"labels": sorted_months, "values": [monthly_trend[m] for m in sorted_months]},
    }


@router.post("/api/add")
async def add_fuel(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    from utils.duplicate_check import is_duplicate
    if is_duplicate("FuelEntries", {
        "EntryDate": data.get("EntryDate", ""),
        "VehicleNumber": data.get("VehicleNumber", ""),
        "DriverName": data.get("DriverName", ""),
        "FuelType": data.get("FuelType", "Diesel"),
        "Litres": data.get("Litres", ""),
        "Amount": data.get("Amount", 0),
        "Kilometre": data.get("Kilometre", ""),
    }):
        return JSONResponse({"error": "Duplicate entry already exists"}, 400)
    fid = gen_id("FUEL")
    from services.sheets_service import build_row
    vals = {**data, "FuelID": fid, "FuelType": data.get("FuelType", "Diesel"), "PaymentMode": data.get("PaymentMode", "Cash"), "CreatedDate": now_str()}
    row = build_row("FuelEntries", vals)
    append_row("FuelEntries", row)
    add_audit_log("CREATE", "FuelEntries", fid, f"Fuel ₹{data.get('Amount',0)} for {data.get('VehicleNumber','')}", user["email"])
    return {"success": True, "fuel_id": fid}


@router.put("/api/{fuel_id}")
async def update_fuel(request: Request, fuel_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("FuelEntries", fuel_id)
    if not result:
        return JSONResponse({"error": "Entry not found"}, 404)
    row_num, existing = result
    from services.sheets_service import build_row
    vals = {**existing, **data, "FuelID": fuel_id, "FuelType": data.get("FuelType", "Diesel"), "PaymentMode": data.get("PaymentMode", "Cash"), "CreatedDate": existing.get("CreatedDate", now_str())}
    row = build_row("FuelEntries", vals)
    update_row("FuelEntries", row_num, row)
    add_audit_log("UPDATE", "FuelEntries", fuel_id, f"Fuel entry updated ₹{data.get('Amount',0)}", user["email"])
    return {"success": True}


@router.delete("/api/{fuel_id}")
async def delete_fuel(request: Request, fuel_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("FuelEntries", fuel_id)
    if not result:
        return JSONResponse({"error": "Entry not found"}, 404)
    row_num, record = result
    delete_row("FuelEntries", row_num)
    add_audit_log("DELETE", "FuelEntries", fuel_id, f"Fuel ₹{record.get('Amount',0)} deleted", user["email"])
    return {"success": True}
