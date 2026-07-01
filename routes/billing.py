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
    count = len([r for r in records if str(r.get("InvoiceNumber", "")).startswith(f"VE-{month}")]) + 1
    return f"VE-{month}-{count:04d}"


def recalc_bill(bill_id: str):
    result = find_row_by_id("Billing", bill_id)
    if not result:
        return
    row_num, bill = result
    receivables = get_all_records("Receivables")
    paid = sum(float(r.get("Amount", 0) or 0) for r in receivables if str(r.get("BillID", "")) == bill_id)
    total = float(bill.get("TotalAmount", 0) or 0)
    balance = total - paid
    status = "Paid" if (paid > 0 and balance <= 0) else "Partial" if paid > 0 else "Pending"
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
    # attach receivables to each bill
    all_recv = get_all_records("Receivables")
    recv_by_bill = {}
    for r in all_recv:
        bid = r.get("BillID", "")
        if bid:
            recv_by_bill.setdefault(bid, []).append({
                "ReceivableID": r.get("ReceivableID", ""),
                "ReceiveDate": r.get("ReceiveDate", ""),
                "PaymentMonth": r.get("PaymentMonth", "") or str(r.get("ReceiveDate", ""))[:7],
                "Amount": r.get("Amount", "0"),
            })
    start = (page - 1) * per_page
    paginated = records[start:start + per_page]
    for b in paginated:
        b["_receivables"] = recv_by_bill.get(b.get("BillID", ""), [])
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
    inv_num = (data.get("InvoiceNumber", "") or next_invoice_number()).strip().upper()
    bid = gen_id("BILL")
    from services.sheets_service import build_row
    vals = {**data, "BillID": bid, "InvoiceNumber": inv_num, "FixedAmount": fixed, "VariableAmount": variable, "TrafficChallan": challan, "Tollgates": tolls, "SubTotal": sub_total, "SGST": sgst, "CGST": cgst, "TDS": tds, "TotalAmount": total, "PaymentStatus": "Pending", "PaidAmount": 0, "BalanceAmount": total, "CreatedDate": now_str(), "UpdatedDate": now_str()}
    row = build_row("Billing", vals)
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
    # Partial update (e.g. just PaymentMonth from inline edit)
    if data.get("_partial"):
        from services.sheets_service import build_row
        vals = {**existing, **{k: v for k, v in data.items() if k != "_partial"}, "UpdatedDate": now_str()}
        row = build_row("Billing", vals)
        update_row("Billing", row_num, row)
        return {"success": True}
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
    status = data.get("_statusOverride") or ("Paid" if (paid > 0 and balance <= 0) else "Partial" if paid > 0 else "Pending")
    from services.sheets_service import build_row
    vals = {**existing, **{k: v for k, v in data.items() if not k.startswith("_")}, "BillID": bill_id, "InvoiceNumber": (data.get("InvoiceNumber", existing.get("InvoiceNumber", "")) or "").strip().upper(), "FixedAmount": fixed, "VariableAmount": variable, "TrafficChallan": challan, "Tollgates": tolls, "SubTotal": sub_total, "SGST": sgst, "CGST": cgst, "TDS": tds, "TotalAmount": total, "PaymentStatus": status, "PaidAmount": paid, "BalanceAmount": balance, "CreatedDate": existing.get("CreatedDate", now_str()), "UpdatedDate": now_str()}
    row = build_row("Billing", vals)
    update_row("Billing", row_num, row)
    add_audit_log("UPDATE", "Billing", bill_id, f"Bill updated ₹{total}", user["email"])
    return {"success": True}


@router.put("/api/invoice/{inv_num}/description")
async def update_invoice_description(request: Request, inv_num: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    desc = data.get("InvoiceDescription", "")
    inv_num = inv_num.strip().upper()
    all_bills = get_all_records("Billing")
    from services.sheets_service import build_row
    updated = 0
    for bill in all_bills:
        if str(bill.get("InvoiceNumber", "")).upper() == inv_num:
            result = find_row_by_id("Billing", bill["BillID"])
            if result:
                row_num, existing = result
                vals = {**existing, "InvoiceDescription": desc, "UpdatedDate": now_str()}
                row = build_row("Billing", vals)
                update_row("Billing", row_num, row)
                updated += 1
    add_audit_log("UPDATE", "Billing", inv_num, f"Invoice description updated for {inv_num}", user["email"])
    return {"success": True, "updated": updated}


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
async def list_receivables(request: Request, bill_id: str = "", vendor: str = "", payment_month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Receivables")
    if bill_id:
        records = [r for r in records if str(r.get("BillID", "")) == bill_id]
    if vendor:
        records = [r for r in records if str(r.get("VendorName", "")) == vendor]
    if payment_month:
        records = [r for r in records if (str(r.get("PaymentMonth", "")) or str(r.get("ReceiveDate", ""))[:7]) == payment_month]
    # enrich with InvoiceNumber from Billing
    bills = get_all_records("Billing")
    bill_map = {b.get("BillID", ""): b for b in bills}
    for r in records:
        bid = r.get("BillID", "")
        bill = bill_map.get(bid, {})
        r["InvoiceNumber"] = bill.get("InvoiceNumber", "")
        r["VehicleNumber"] = bill.get("VehicleNumber", r.get("VehicleNumber", ""))
        r["PaymentMonth"] = r.get("PaymentMonth", "") or str(r.get("ReceiveDate", ""))[:7]
    records.sort(key=lambda x: (str(x.get("InvoiceNumber", "")), str(x.get("ReceiveDate", ""))))
    total_received = sum(float(r.get("Amount", 0) or 0) for r in records)
    return {"receivables": records, "total_received": total_received}


@router.post("/api/receivables/add")
async def add_receivable(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    payment_month = data.get("PaymentMonth", "") or str(data.get("ReceiveDate", ""))[:7]
    from services.sheets_service import build_row
    bill_id = data.get("BillID", "")

    # Invoice-level payment: split proportionally across all bills under this invoice
    if str(bill_id).startswith("INV:"):
        inv_num = bill_id[4:].strip().upper()
        all_bills = get_all_records("Billing")
        inv_bills = [b for b in all_bills if str(b.get("InvoiceNumber", "")).upper() == inv_num and b.get("PaymentStatus") != "Paid"]
        if not inv_bills:
            return JSONResponse({"error": "No unpaid bills found for this invoice"}, 400)
        total_balance = sum(float(b.get("BalanceAmount", 0) or 0) for b in inv_bills)
        total_payment = float(data.get("Amount", 0) or 0)
        created = []
        for b in inv_bills:
            bill_balance = float(b.get("BalanceAmount", 0) or 0)
            proportion = (bill_balance / total_balance) if total_balance > 0 else (1 / len(inv_bills))
            split_amount = round(total_payment * proportion, 2)
            rid = gen_id("RCV")
            vals = {**data, "ReceivableID": rid, "BillID": b["BillID"], "Amount": split_amount,
                    "PaymentMonth": payment_month, "PaymentMode": data.get("PaymentMode", "Bank Transfer"),
                    "Description": (data.get("Description", "") + f" [{inv_num}]").strip(),
                    "CreatedDate": now_str()}
            row = build_row("Receivables", vals)
            append_row("Receivables", row)
            recalc_bill(b["BillID"])
            created.append(rid)
        add_audit_log("CREATE", "Receivables", inv_num, f"Invoice payment ₹{total_payment} split across {len(inv_bills)} bills", user["email"])
        return {"success": True, "receivable_ids": created, "split_count": len(created)}

    # Single bill payment
    rid = gen_id("RCV")
    vals = {**data, "ReceivableID": rid, "PaymentMonth": payment_month, "PaymentMode": data.get("PaymentMode", "Bank Transfer"), "CreatedDate": now_str()}
    row = build_row("Receivables", vals)
    append_row("Receivables", row)
    add_audit_log("CREATE", "Receivables", rid, f"Received ₹{data.get('Amount',0)} from {data.get('VendorName','')}", user["email"])
    if bill_id:
        recalc_bill(bill_id)
    return {"success": True, "receivable_id": rid}


@router.put("/api/receivables/{recv_id}")
async def update_receivable(request: Request, recv_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Receivables", recv_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, existing = result
    from services.sheets_service import build_row
    vals = {**existing, **data, "ReceivableID": recv_id}
    row = build_row("Receivables", vals)
    update_row("Receivables", row_num, row)
    add_audit_log("UPDATE", "Receivables", recv_id, f"Receivable updated", user["email"])
    return {"success": True}


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
