from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates
from utils.duplicate_check import is_duplicate
from datetime import datetime

router = APIRouter(prefix="/billing", tags=["billing"])


def get_user(request: Request):
    return request.session.get("user")


def next_invoice_number():
    records = get_all_records("Billing")
    month = datetime.now().strftime("%y%m")
    count = len([r for r in records if str(r.get("InvoiceNumber", "")).startswith(f"TSR-{month}")]) + 1
    return f"TSR-{month}-{count:04d}"


def recalc_bill(bill_id: str):
    result = find_row_by_id("Billing", bill_id)
    if not result:
        return
    row_num, bill = result
    receivables = get_all_records("Receivables")
    paid = sum(float(r.get("Amount", 0) or 0) for r in receivables if str(r.get("BillID", "")) == bill_id)
    total = float(bill.get("TotalAmount", 0) or 0)
    balance = total - paid
    status = "Paid" if balance <= 0 else "Partial" if paid > 0 else "Pending"
    from services.sheets_service import SHEET_HEADERS
    headers = SHEET_HEADERS["Billing"]
    row_data = [str(bill.get(h, "")) for h in headers]
    row_data[headers.index("PaymentStatus")] = status
    row_data[headers.index("PaidAmount")] = str(paid)
    row_data[headers.index("BalanceAmount")] = str(balance)
    row_data[headers.index("UpdatedDate")] = now_str()
    update_row("Billing", row_num, row_data)


@router.get("")
async def billing_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="billing.html", context={"user": user})


@router.get("/api/list")
async def list_bills(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    vendor: str = "",
    status: str = "",
    page: int = 1,
    per_page: int = 25,
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Billing")
    if date_from:
        records = [r for r in records if str(r.get("InvoiceDate", "")) >= date_from]
    if date_to:
        records = [r for r in records if str(r.get("InvoiceDate", "")) <= date_to]
    if vehicle:
        records = [r for r in records if str(r.get("VehicleNumber", "")) == vehicle]
    if vendor:
        records = [r for r in records if str(r.get("VendorName", "")) == vendor]
    if status:
        records = [r for r in records if str(r.get("PaymentStatus", "")) == status]
    records.sort(key=lambda x: str(x.get("InvoiceDate", "")), reverse=True)
    total = len(records)
    total_amount = sum(float(r.get("TotalAmount", 0) or 0) for r in records)
    total_paid = sum(float(r.get("PaidAmount", 0) or 0) for r in records)
    total_balance = sum(float(r.get("BalanceAmount", 0) or 0) for r in records)
    start = (page - 1) * per_page
    paginated = records[start:start + per_page]
    return {
        "bills": paginated,
        "total": total,
        "page": page,
        "total_pages": (total + per_page - 1) // per_page if total else 1,
        "total_amount": total_amount,
        "total_paid": total_paid,
        "total_balance": total_balance,
    }


@router.post("/api/add")
async def add_bill(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    fixed = float(data.get("FixedAmount", 0) or 0)
    variable = float(data.get("VariableAmount", 0) or 0)
    challan = float(data.get("TrafficChallan", 0) or 0)
    tolls = float(data.get("Tollgates", 0) or 0)
    sub_total = fixed + variable - challan - tolls
    sgst = round(sub_total * 0.09, 2)
    cgst = round(sub_total * 0.09, 2)
    tds = float(data.get("TDS", 0) or 0)
    total = round(sub_total + sgst + cgst - tds, 2)
    inv_num = data.get("InvoiceNumber", "") or next_invoice_number()
    bid = gen_id("BILL")
    row = [
        bid, inv_num, data.get("InvoiceDate", ""),
        data.get("VehicleNumber", ""), data.get("VendorName", ""),
        fixed, variable, challan, tolls,
        sub_total, sgst, cgst, tds, total,
        "Pending", 0, total,
        data.get("Description", ""), now_str(), now_str(),
    ]
    append_row("Billing", row)
    add_audit_log("CREATE", "Billing", bid, f"Bill {inv_num} ₹{total} for {data.get('VendorName','')}", user["email"])
    return {"success": True, "bill_id": bid, "invoice_number": inv_num}


@router.put("/api/{bill_id}")
async def update_bill(request: Request, bill_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Billing", bill_id)
    if not result:
        return JSONResponse({"error": "Bill not found"}, 404)
    row_num, existing = result
    fixed = float(data.get("FixedAmount", 0) or 0)
    variable = float(data.get("VariableAmount", 0) or 0)
    challan = float(data.get("TrafficChallan", 0) or 0)
    tolls = float(data.get("Tollgates", 0) or 0)
    sub_total = fixed + variable - challan - tolls
    sgst = round(sub_total * 0.09, 2)
    cgst = round(sub_total * 0.09, 2)
    tds = float(data.get("TDS", 0) or 0)
    total = round(sub_total + sgst + cgst - tds, 2)
    paid = float(existing.get("PaidAmount", 0) or 0)
    balance = total - paid
    status = "Paid" if balance <= 0 else "Partial" if paid > 0 else "Pending"
    row = [
        bill_id, data.get("InvoiceNumber", existing.get("InvoiceNumber", "")),
        data.get("InvoiceDate", ""),
        data.get("VehicleNumber", ""), data.get("VendorName", ""),
        fixed, variable, challan, tolls,
        sub_total, sgst, cgst, tds, total,
        status, paid, balance,
        data.get("Description", ""), existing.get("CreatedDate", now_str()), now_str(),
    ]
    update_row("Billing", row_num, row)
    add_audit_log("UPDATE", "Billing", bill_id, f"Bill updated ₹{total}", user["email"])
    return {"success": True}


@router.delete("/api/{bill_id}")
async def delete_bill(request: Request, bill_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Billing", bill_id)
    if not result:
        return JSONResponse({"error": "Bill not found"}, 404)
    row_num, record = result
    delete_row("Billing", row_num)
    add_audit_log("DELETE", "Billing", bill_id, f"Bill {record.get('InvoiceNumber','')} deleted", user["email"])
    return {"success": True}


@router.get("/api/receivables")
async def list_receivables(request: Request, bill_id: str = "", vendor: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Receivables")
    if bill_id:
        records = [r for r in records if str(r.get("BillID", "")) == bill_id]
    if vendor:
        records = [r for r in records if str(r.get("VendorName", "")) == vendor]
    records.sort(key=lambda x: str(x.get("ReceiveDate", "")), reverse=True)
    return {"receivables": records}


@router.post("/api/receivables/add")
async def add_receivable(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    rid = gen_id("RCV")
    row = [
        rid, data.get("ReceiveDate", ""), data.get("BillID", ""),
        data.get("VendorName", ""), data.get("Amount", 0),
        data.get("PaymentMode", "Bank Transfer"), data.get("ReferenceNumber", ""),
        data.get("Description", ""), now_str(),
    ]
    append_row("Receivables", row)
    add_audit_log("CREATE", "Receivables", rid, f"Received ₹{data.get('Amount',0)} from {data.get('VendorName','')}", user["email"])
    if data.get("BillID"):
        recalc_bill(data["BillID"])
    return {"success": True, "receivable_id": rid}


@router.delete("/api/receivables/{recv_id}")
async def delete_receivable(request: Request, recv_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Receivables", recv_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, record = result
    bill_id = record.get("BillID", "")
    delete_row("Receivables", row_num)
    add_audit_log("DELETE", "Receivables", recv_id, f"Receivable deleted", user["email"])
    if bill_id:
        recalc_bill(bill_id)
    return {"success": True}
