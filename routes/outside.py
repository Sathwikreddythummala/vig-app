from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates
from datetime import datetime
from zoneinfo import ZoneInfo
_IST = ZoneInfo("Asia/Kolkata")

router = APIRouter(prefix="/outside", tags=["outside"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def outside_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="outside.html", context={"user": user})


@router.get("/api/vehicles")
async def list_outside_vehicles(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    vehicles = get_all_records("OutsideVehicles")
    vehicles.sort(key=lambda v: str(v.get("VehicleNumber", "")))
    return {"vehicles": vehicles}


@router.post("/api/vehicles/add")
async def add_outside_vehicle(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    vnum = str(data.get("VehicleNumber", "")).strip().upper()
    if not vnum or not data.get("OwnerName", "").strip():
        return JSONResponse({"error": "Vehicle number and owner name required"}, 400)
    existing = get_all_records("OutsideVehicles")
    for v in existing:
        if str(v.get("VehicleNumber", "")).strip().upper() == vnum:
            return JSONResponse({"error": "Vehicle already exists"}, 400)
    vid = gen_id("OV")
    from services.sheets_service import build_row
    vals = {**data, "OVID": vid, "VehicleNumber": vnum, "Status": data.get("Status", "Active"), "CreatedDate": now_str(), "UpdatedDate": now_str()}
    row = build_row("OutsideVehicles", vals)
    append_row("OutsideVehicles", row)
    add_audit_log("CREATE", "OutsideVehicles", vid, f"Outside vehicle {vnum} added", user["email"])
    return {"success": True, "ov_id": vid}


@router.put("/api/vehicles/{ov_id}")
async def update_outside_vehicle(request: Request, ov_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("OutsideVehicles", ov_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, existing = result
    from services.sheets_service import build_row
    vals = {**existing, **data, "OVID": ov_id, "VehicleNumber": str(data.get("VehicleNumber", "")).strip().upper(), "Status": data.get("Status", existing.get("Status", "Active")), "CreatedDate": existing.get("CreatedDate", now_str()), "UpdatedDate": now_str()}
    row = build_row("OutsideVehicles", vals)
    update_row("OutsideVehicles", row_num, row)
    add_audit_log("UPDATE", "OutsideVehicles", ov_id, f"Outside vehicle updated", user["email"])
    return {"success": True}


@router.delete("/api/vehicles/{ov_id}")
async def delete_outside_vehicle(request: Request, ov_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("OutsideVehicles", ov_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    delete_row("OutsideVehicles", result[0])
    add_audit_log("DELETE", "OutsideVehicles", ov_id, "Outside vehicle deleted", user["email"])
    return {"success": True}


@router.get("/api/transactions")
async def list_transactions(request: Request, vehicle: str = "", month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("OutsideTransactions")
    from utils.filters import filter_multi
    records = filter_multi(records, "VehicleNumber", vehicle)
    if month:
        records = [r for r in records if str(r.get("ForMonth", "")) == month]
    records.sort(key=lambda x: str(x.get("Date", "")), reverse=True)
    return {"transactions": records}


@router.get("/api/summary")
async def outside_summary(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    if not month:
        month = datetime.now(_IST).strftime("%Y-%m")
    vehicles = get_all_records("OutsideVehicles")
    transactions = get_all_records("OutsideTransactions")
    bills = get_all_records("Billing")
    ov_numbers = [str(v.get("VehicleNumber", "")).upper() for v in vehicles]
    month_txns = [t for t in transactions if str(t.get("ForMonth", "")) == month]
    month_bills = [b for b in bills if (str(b.get("PaymentMonth", "")) or str(b.get("InvoiceDate", ""))[:7]) == month]
    summaries = []
    for v in vehicles:
        if v.get("Status", "") != "Active":
            continue
        vnum = v.get("VehicleNumber", "")
        payment = sum(float(b.get("TotalAmount", 0) or 0) for b in month_bills if str(b.get("VehicleNumber", "")).upper() == vnum.upper())
        vtxns = [t for t in month_txns if str(t.get("VehicleNumber", "")) == vnum]
        advance = sum(float(t.get("Amount", 0) or 0) for t in vtxns if t.get("Type") == "Advance")
        diesel = sum(float(t.get("Amount", 0) or 0) for t in vtxns if t.get("Type") == "Diesel")
        other_deduction = sum(float(t.get("Amount", 0) or 0) for t in vtxns if t.get("Type") == "Deduction")
        total_deductions = advance + diesel + other_deduction
        balance = payment - total_deductions
        summaries.append({
            "VehicleNumber": vnum,
            "OwnerName": v.get("OwnerName", ""),
            "Payment": payment,
            "Advance": advance,
            "Diesel": diesel,
            "OtherDeduction": other_deduction,
            "TotalDeductions": total_deductions,
            "Balance": balance,
        })
    total_payment = sum(s["Payment"] for s in summaries)
    total_deductions = sum(s["TotalDeductions"] for s in summaries)
    total_balance = sum(s["Balance"] for s in summaries)
    return {
        "month": month,
        "summaries": summaries,
        "total_payment": total_payment,
        "total_deductions": total_deductions,
        "total_balance": total_balance,
    }


@router.post("/api/transactions/add")
async def add_transaction(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    vehicles = get_all_records("OutsideVehicles")
    owner = ""
    for v in vehicles:
        if str(v.get("VehicleNumber", "")) == data.get("VehicleNumber", ""):
            owner = v.get("OwnerName", "")
            break
    for_month = data.get("ForMonth", "")
    if not for_month:
        for_month = str(data.get("Date", ""))[:7]
    tid = gen_id("OTX")
    from services.sheets_service import build_row
    vals = {**data, "TransID": tid, "ForMonth": for_month, "OwnerName": owner, "PaymentMode": data.get("PaymentMode", "Cash"), "CreatedDate": now_str()}
    row = build_row("OutsideTransactions", vals)
    append_row("OutsideTransactions", row)
    add_audit_log("CREATE", "OutsideTransactions", tid,
                  f"{data.get('Type','')} ₹{data.get('Amount',0)} for {data.get('VehicleNumber','')}", user["email"])
    return {"success": True}


@router.delete("/api/transactions/{trans_id}")
async def delete_transaction(request: Request, trans_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("OutsideTransactions", trans_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    delete_row("OutsideTransactions", result[0])
    add_audit_log("DELETE", "OutsideTransactions", trans_id, "Transaction deleted", user["email"])
    return {"success": True}
