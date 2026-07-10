from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log, SHEET_HEADERS,
)
from utils.templates import templates
from datetime import datetime
from zoneinfo import ZoneInfo
_IST = ZoneInfo("Asia/Kolkata")
import io

router = APIRouter(prefix="/emi", tags=["emi"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def emi_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="emi.html", context={"user": user})


def calc_next_due(emi_day_str):
    today = datetime.now(_IST)
    try:
        day = int(emi_day_str)
        next_d = today.replace(day=day)
        if next_d.date() < today.date():
            if today.month == 12:
                next_d = next_d.replace(year=today.year + 1, month=1)
            else:
                next_d = next_d.replace(month=today.month + 1)
        return next_d.strftime("%Y-%m-%d"), (next_d.date() - today.date()).days
    except (ValueError, TypeError):
        return "", None


@router.get("/api/list")
async def list_emis(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    vehicles = get_all_records("Vehicles")
    today = datetime.now(_IST)
    vehicle_emis = []
    for v in vehicles:
        if str(v.get("LoanAvailable", "")).lower() == "yes":
            emi_amount = float(v.get("EMIAmount", 0) or 0)
            emi_day = str(v.get("EMIDate", "")).strip()
            status = "Active"
            try:
                end = datetime.strptime(str(v.get("LoanEndDate", "")), "%Y-%m-%d")
                if end < today:
                    status = "Completed"
            except (ValueError, TypeError):
                pass
            next_due, days_left = calc_next_due(emi_day) if status == "Active" else ("", None)
            vehicle_emis.append({
                "VehicleID": v.get("VehicleID", ""),
                "VehicleNumber": v.get("VehicleNumber", ""),
                "VehicleType": v.get("VehicleType", ""),
                "BankName": v.get("BankName", ""),
                "LoanAccountNumber": v.get("LoanAccountNumber", ""),
                "EMIAmount": emi_amount,
                "EMIDate": emi_day,
                "LoanStartDate": v.get("LoanStartDate", ""),
                "LoanEndDate": v.get("LoanEndDate", ""),
                "NextDue": next_due,
                "DaysLeft": days_left,
                "Status": status,
            })
    other_emis = get_all_records("OtherEMIs")
    for oe in other_emis:
        if str(oe.get("Status", "")).lower() != "active":
            continue
        try:
            end = datetime.strptime(str(oe.get("EndDate", "")), "%Y-%m-%d")
            if end < today:
                oe["Status"] = "Completed"
        except (ValueError, TypeError):
            pass
        emi_day = str(oe.get("EMIDate", "")).strip()
        if oe.get("Status", "") == "Active":
            nd, dl = calc_next_due(emi_day)
            oe["NextDue"] = nd
            oe["DaysLeft"] = dl
        else:
            oe["NextDue"] = ""
            oe["DaysLeft"] = None
    expenses = get_all_records("Expenses")
    emi_expenses = [e for e in expenses if str(e.get("Category", "")) == "EMI"]
    emi_expenses.sort(key=lambda x: str(x.get("ExpenseDate", "")), reverse=True)
    active_vehicle = sum(e["EMIAmount"] for e in vehicle_emis if e["Status"] == "Active")
    active_other = sum(float(oe.get("EMIAmount", 0) or 0) for oe in other_emis if oe.get("Status", "") == "Active")
    total_paid = sum(float(e.get("Amount", 0) or 0) for e in emi_expenses)
    active_v_count = len([e for e in vehicle_emis if e["Status"] == "Active"])
    active_o_count = len([oe for oe in other_emis if oe.get("Status", "") == "Active"])
    completed_v = len([e for e in vehicle_emis if e["Status"] == "Completed"])
    completed_o = len([oe for oe in other_emis if oe.get("Status", "") == "Completed"])
    return {
        "vehicle_emis": vehicle_emis,
        "other_emis": other_emis,
        "emi_expenses": emi_expenses[:50],
        "total_monthly": active_vehicle + active_other,
        "total_active": active_v_count + active_o_count,
        "total_completed": completed_v + completed_o,
        "total_paid": total_paid,
    }


@router.get("/api/export/excel")
async def export_excel(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    import pandas as pd
    data = await list_emis(request)
    vehicle_emis = data["vehicle_emis"]
    other_emis = data["other_emis"]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Vehicle EMIs sheet
        v_rows = [{
            "Vehicle Number": e["VehicleNumber"],
            "Vehicle Type": e.get("VehicleType", ""),
            "Bank Name": e["BankName"],
            "Loan Account": e["LoanAccountNumber"],
            "EMI Amount": e["EMIAmount"],
            "EMI Day": e["EMIDate"],
            "Loan Start": e["LoanStartDate"],
            "Loan End": e["LoanEndDate"],
            "Next Due": e["NextDue"],
            "Days Left": e["DaysLeft"],
            "Status": e["Status"],
        } for e in vehicle_emis]
        pd.DataFrame(v_rows).to_excel(writer, sheet_name="Vehicle EMIs", index=False)

        # Other EMIs sheet
        o_rows = [{
            "EMI Name": e.get("EMIName", ""),
            "Category": e.get("Category", ""),
            "Vehicle": e.get("VehicleNumber", ""),
            "Lender": e.get("LenderName", ""),
            "Total Amount": e.get("TotalAmount", ""),
            "Down Payment": e.get("DownPayment", ""),
            "EMI Amount": e.get("EMIAmount", ""),
            "EMI Day": e.get("EMIDate", ""),
            "Start Date": e.get("StartDate", ""),
            "End Date": e.get("EndDate", ""),
            "Paid Installments": e.get("PaidInstallments", ""),
            "Total Installments": e.get("TotalInstallments", ""),
            "Next Due": e.get("NextDue", ""),
            "Days Left": e.get("DaysLeft", ""),
            "Status": e.get("Status", ""),
        } for e in other_emis]
        pd.DataFrame(o_rows).to_excel(writer, sheet_name="Other EMIs", index=False)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=emi_report.xlsx"},
    )


@router.post("/api/other/add")
async def add_other_emi(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    from utils.duplicate_check import is_duplicate
    if is_duplicate("OtherEMIs", {
        "EMIName": data.get("EMIName", ""),
        "Category": data.get("Category", ""),
        "LenderName": data.get("LenderName", ""),
        "TotalAmount": data.get("TotalAmount", ""),
        "EMIAmount": data.get("EMIAmount", ""),
    }):
        return JSONResponse({"error": "Duplicate EMI already exists"}, 400)
    eid = gen_id("EMI")
    from services.sheets_service import build_row
    vals = {**data, "EMIID": eid, "DownPayment": data.get("DownPayment", "0"), "PaidInstallments": data.get("PaidInstallments", "0"), "Status": data.get("Status", "Active"), "CreatedDate": now_str(), "UpdatedDate": now_str()}
    row = build_row("OtherEMIs", vals)
    append_row("OtherEMIs", row)
    add_audit_log("CREATE", "OtherEMIs", eid, f"Other EMI '{data.get('EMIName','')}' added", user["email"])
    return {"success": True, "emi_id": eid}


@router.put("/api/other/{emi_id}")
async def update_other_emi(request: Request, emi_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("OtherEMIs", emi_id)
    if not result:
        return JSONResponse({"error": "EMI not found"}, 404)
    row_num, existing = result
    from services.sheets_service import build_row
    vals = {**existing, **data, "EMIID": emi_id, "Status": data.get("Status", existing.get("Status", "Active")), "CreatedDate": existing.get("CreatedDate", now_str()), "UpdatedDate": now_str()}
    row = build_row("OtherEMIs", vals)
    update_row("OtherEMIs", row_num, row)
    add_audit_log("UPDATE", "OtherEMIs", emi_id, f"Other EMI '{data.get('EMIName','')}' updated", user["email"])
    return {"success": True}


@router.delete("/api/other/{emi_id}")
async def delete_other_emi(request: Request, emi_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("OtherEMIs", emi_id)
    if not result:
        return JSONResponse({"error": "EMI not found"}, 404)
    row_num, record = result
    delete_row("OtherEMIs", row_num)
    add_audit_log("DELETE", "OtherEMIs", emi_id, f"Other EMI '{record.get('EMIName','')}' deleted", user["email"])
    return {"success": True}


@router.put("/api/vehicle-emi/{vehicle_id}")
async def update_vehicle_emi(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    row_num, existing = result
    headers = SHEET_HEADERS["Vehicles"]
    row_data = [existing.get(h, "") for h in headers]
    for field in ["BankName", "LoanAccountNumber", "EMIAmount", "EMIDate", "LoanStartDate", "LoanEndDate", "LoanAvailable"]:
        if field in data:
            row_data[headers.index(field)] = data[field]
    row_data[headers.index("UpdatedDate")] = now_str()
    update_row("Vehicles", row_num, row_data)
    add_audit_log("UPDATE", "Vehicles", vehicle_id, f"Vehicle EMI updated for {existing.get('VehicleNumber','')}", user["email"])
    return {"success": True}


@router.delete("/api/vehicle-emi/{vehicle_id}")
async def remove_vehicle_emi(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    row_num, existing = result
    headers = SHEET_HEADERS["Vehicles"]
    row_data = [existing.get(h, "") for h in headers]
    for field in ["LoanAvailable", "BankName", "LoanAccountNumber", "EMIAmount", "EMIDate", "LoanStartDate", "LoanEndDate"]:
        row_data[headers.index(field)] = ""
    row_data[headers.index("UpdatedDate")] = now_str()
    update_row("Vehicles", row_num, row_data)
    add_audit_log("DELETE", "Vehicles", vehicle_id, f"Loan removed from {existing.get('VehicleNumber','')}", user["email"])
    return {"success": True}
