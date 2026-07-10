from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
_IST = ZoneInfo("Asia/Kolkata")
import pandas as pd
import io

router = APIRouter(prefix="/income", tags=["income"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def income_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="income.html", context={"user": user})


@router.get("/api/list")
async def list_income(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    vendor: str = "",
    payment_status: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = 25,
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Income")
    if date_from:
        records = [r for r in records if str(r.get("IncomeDate", "")) >= date_from]
    if date_to:
        records = [r for r in records if str(r.get("IncomeDate", "")) <= date_to]
    from utils.filters import filter_multi
    records = filter_multi(records, "VehicleNumber", vehicle)
    records = filter_multi(records, "VendorName", vendor)
    records = filter_multi(records, "PaymentStatus", payment_status)
    if search:
        s = search.lower()
        records = [r for r in records if s in str(r.get("VehicleNumber", "")).lower() or s in str(r.get("VendorName", "")).lower() or s in str(r.get("TripFrom", "")).lower() or s in str(r.get("TripTo", "")).lower() or s in str(r.get("Material", "")).lower()]
    records.sort(key=lambda x: str(x.get("IncomeDate", "")), reverse=True)
    total = len(records)
    total_amount = sum(float(r.get("Amount", 0) or 0) for r in records)
    total_pending = sum(float(r.get("Amount", 0) or 0) for r in records if str(r.get("PaymentStatus", "")) == "Pending")
    total_received = sum(float(r.get("Amount", 0) or 0) for r in records if str(r.get("PaymentStatus", "")) == "Received")
    start = (page - 1) * per_page
    paginated = records[start:start + per_page]
    return {
        "income": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 1,
        "total_amount": total_amount,
        "total_pending": total_pending,
        "total_received": total_received,
    }


@router.get("/api/stats")
async def income_stats(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Income")
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    month_start = datetime.now(_IST).strftime("%Y-%m-01")
    month_records = [r for r in records if str(r.get("IncomeDate", "")) >= month_start]
    today_records = [r for r in records if str(r.get("IncomeDate", "")) == today]
    total_today = sum(float(r.get("Amount", 0) or 0) for r in today_records)
    total_month = sum(float(r.get("Amount", 0) or 0) for r in month_records)
    total_pending = sum(float(r.get("Amount", 0) or 0) for r in records if str(r.get("PaymentStatus", "")) == "Pending")
    vendor_wise = defaultdict(float)
    vehicle_wise = defaultdict(float)
    for r in month_records:
        vendor_wise[str(r.get("VendorName", "")) or "Unknown"] += float(r.get("Amount", 0) or 0)
        vehicle_wise[str(r.get("VehicleNumber", "")) or "Unknown"] += float(r.get("Amount", 0) or 0)
    monthly_trend = defaultdict(float)
    for r in records:
        d = str(r.get("IncomeDate", ""))
        if len(d) >= 7:
            monthly_trend[d[:7]] += float(r.get("Amount", 0) or 0)
    sorted_months = sorted(monthly_trend.keys())[-12:]
    return {
        "total_today": total_today,
        "total_month": total_month,
        "total_pending": total_pending,
        "total_trips": len(month_records),
        "vendor_wise": {"labels": list(vendor_wise.keys()), "values": list(vendor_wise.values())},
        "vehicle_wise": {"labels": list(vehicle_wise.keys()), "values": list(vehicle_wise.values())},
        "monthly_trend": {"labels": sorted_months, "values": [monthly_trend[m] for m in sorted_months]},
    }


@router.post("/api/add")
async def add_income(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    from utils.duplicate_check import is_duplicate
    if is_duplicate("Income", {
        "IncomeDate": data.get("IncomeDate", ""),
        "VehicleNumber": data.get("VehicleNumber", ""),
        "VendorName": data.get("VendorName", ""),
        "TripFrom": data.get("TripFrom", ""),
        "TripTo": data.get("TripTo", ""),
        "Amount": data.get("Amount", ""),
    }):
        return JSONResponse({"error": "Duplicate income entry already exists"}, 400)
    iid = gen_id("INC")
    amount = data.get("Amount", "")
    if not amount:
        qty = float(data.get("Quantity", 0) or 0)
        rate = float(data.get("Rate", 0) or 0)
        if qty and rate:
            amount = qty * rate
    from services.sheets_service import build_row
    vals = {**data, "IncomeID": iid, "Amount": amount, "PaymentStatus": data.get("PaymentStatus", "Pending"), "CreatedDate": now_str()}
    row = build_row("Income", vals)
    append_row("Income", row)
    add_audit_log("CREATE", "Income", iid, f"Income ₹{amount} from {data.get('VendorName','')}", user["email"])
    return {"success": True, "income_id": iid}


@router.put("/api/{income_id}")
async def update_income(request: Request, income_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Income", income_id)
    if not result:
        return JSONResponse({"error": "Income not found"}, 404)
    row_num, existing = result
    amount = data.get("Amount", "")
    if not amount:
        qty = float(data.get("Quantity", 0) or 0)
        rate = float(data.get("Rate", 0) or 0)
        if qty and rate:
            amount = qty * rate
    from services.sheets_service import build_row
    vals = {**existing, **data, "IncomeID": income_id, "Amount": amount, "PaymentStatus": data.get("PaymentStatus", existing.get("PaymentStatus", "Pending")), "CreatedDate": existing.get("CreatedDate", now_str())}
    row = build_row("Income", vals)
    update_row("Income", row_num, row)
    add_audit_log("UPDATE", "Income", income_id, f"Income updated to ₹{amount}", user["email"])
    return {"success": True}


@router.delete("/api/{income_id}")
async def delete_income(request: Request, income_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Income", income_id)
    if not result:
        return JSONResponse({"error": "Income not found"}, 404)
    row_num, record = result
    delete_row("Income", row_num)
    add_audit_log("DELETE", "Income", income_id, f"Income ₹{record.get('Amount',0)} deleted", user["email"])
    return {"success": True}


@router.get("/api/export/excel")
async def export_income_excel(request: Request, date_from: str = "", date_to: str = "", vehicle: str = "", vendor: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Income")
    if date_from:
        records = [r for r in records if str(r.get("IncomeDate", "")) >= date_from]
    if date_to:
        records = [r for r in records if str(r.get("IncomeDate", "")) <= date_to]
    from utils.filters import filter_multi
    records = filter_multi(records, "VehicleNumber", vehicle)
    records = filter_multi(records, "VendorName", vendor)
    df = pd.DataFrame(records)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=income.xlsx"})
