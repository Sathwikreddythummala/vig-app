from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates
from datetime import datetime

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
    today = datetime.now()
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
    today = datetime.now()
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
    row = [
        eid,
        data.get("EMIName", ""),
        data.get("Category", ""),
        data.get("Description", ""),
        data.get("VehicleNumber", ""),
        data.get("LenderName", ""),
        data.get("TotalAmount", ""),
        data.get("DownPayment", "0"),
        data.get("EMIAmount", ""),
        data.get("EMIDate", ""),
        data.get("StartDate", ""),
        data.get("EndDate", ""),
        data.get("TotalInstallments", ""),
        data.get("PaidInstallments", "0"),
        data.get("Status", "Active"),
        now_str(),
        now_str(),
    ]
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
    row = [
        emi_id,
        data.get("EMIName", ""),
        data.get("Category", ""),
        data.get("Description", ""),
        data.get("VehicleNumber", ""),
        data.get("LenderName", ""),
        data.get("TotalAmount", ""),
        data.get("DownPayment", existing.get("DownPayment", "0")),
        data.get("EMIAmount", ""),
        data.get("EMIDate", ""),
        data.get("StartDate", ""),
        data.get("EndDate", ""),
        data.get("TotalInstallments", ""),
        data.get("PaidInstallments", data.get("PaidInstallments", "0")),
        data.get("Status", "Active"),
        existing.get("CreatedDate", now_str()),
        now_str(),
    ]
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
