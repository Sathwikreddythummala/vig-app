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

router = APIRouter(prefix="/gst", tags=["gst"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def gst_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="gst.html", context={"user": user})


@router.get("/api/purchases")
async def list_purchases(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("GSTpurchases")
    if month:
        records = [r for r in records if str(r.get("InvoiceDate", ""))[:7] == month]
    records.sort(key=lambda x: str(x.get("InvoiceDate", "")), reverse=True)
    total_amount = sum(float(r.get("Amount", 0) or 0) for r in records)
    total_sgst = sum(float(r.get("SGST", 0) or 0) for r in records)
    total_cgst = sum(float(r.get("CGST", 0) or 0) for r in records)
    total = sum(float(r.get("TotalAmount", 0) or 0) for r in records)
    return {
        "purchases": records,
        "total_amount": total_amount,
        "total_sgst": total_sgst,
        "total_cgst": total_cgst,
        "total": total,
    }


@router.get("/api/sales")
async def get_sales(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    bills = get_all_records("Billing")
    if month:
        bills = [b for b in bills if str(b.get("InvoiceDate", ""))[:7] == month]
    # Group by InvoiceNumber — one row per invoice
    from collections import defaultdict
    inv_groups = defaultdict(lambda: {"SubTotal": 0, "SGST": 0, "CGST": 0, "TotalAmount": 0, "VendorName": "", "InvoiceDate": "", "InvoiceNumber": "", "VehicleCount": 0})
    for b in bills:
        inv = str(b.get("InvoiceNumber", "")).upper()
        g = inv_groups[inv]
        g["InvoiceNumber"] = inv
        g["InvoiceDate"] = g["InvoiceDate"] or b.get("InvoiceDate", "")
        g["VendorName"] = g["VendorName"] or b.get("VendorName", "")
        g["SubTotal"] += float(b.get("SubTotal", 0) or 0)
        g["SGST"] += float(b.get("SGST", 0) or 0)
        g["CGST"] += float(b.get("CGST", 0) or 0)
        g["TotalAmount"] += float(b.get("TotalAmount", 0) or 0)
        g["VehicleCount"] += 1
    grouped = sorted(inv_groups.values(), key=lambda x: x["InvoiceDate"], reverse=True)
    total_amount = sum(g["SubTotal"] for g in grouped)
    total_sgst = sum(g["SGST"] for g in grouped)
    total_cgst = sum(g["CGST"] for g in grouped)
    total = sum(g["TotalAmount"] for g in grouped)
    return {
        "sales": grouped,
        "total_amount": total_amount,
        "total_sgst": total_sgst,
        "total_cgst": total_cgst,
        "total": total,
    }


@router.get("/api/summary")
async def gst_summary(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    if not month:
        month = datetime.now(_IST).strftime("%Y-%m")
    purchases = get_all_records("GSTpurchases")
    purchases = [r for r in purchases if str(r.get("InvoiceDate", ""))[:7] == month]
    bills = get_all_records("Billing")
    bills = [b for b in bills if str(b.get("InvoiceDate", ""))[:7] == month]
    purchase_sgst = sum(float(r.get("SGST", 0) or 0) for r in purchases)
    purchase_cgst = sum(float(r.get("CGST", 0) or 0) for r in purchases)
    purchase_total_gst = purchase_sgst + purchase_cgst
    sales_sgst = sum(float(b.get("SGST", 0) or 0) for b in bills)
    sales_cgst = sum(float(b.get("CGST", 0) or 0) for b in bills)
    sales_total_gst = sales_sgst + sales_cgst
    sgst_payable = sales_sgst - purchase_sgst
    cgst_payable = sales_cgst - purchase_cgst
    total_payable = sgst_payable + cgst_payable
    return {
        "month": month,
        "purchase_sgst": purchase_sgst,
        "purchase_cgst": purchase_cgst,
        "purchase_total_gst": purchase_total_gst,
        "purchase_count": len(purchases),
        "sales_sgst": sales_sgst,
        "sales_cgst": sales_cgst,
        "sales_total_gst": sales_total_gst,
        "sales_count": len(bills),
        "sgst_payable": sgst_payable,
        "cgst_payable": cgst_payable,
        "total_payable": total_payable,
    }


@router.post("/api/purchases/add")
async def add_purchase(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    amt = float(data.get("Amount", 0) or 0)
    sgst = round(amt * 0.09, 2)
    cgst = round(amt * 0.09, 2)
    if data.get("SGST"):
        sgst = float(data["SGST"])
    if data.get("CGST"):
        cgst = float(data["CGST"])
    total = round(amt + sgst + cgst, 2)
    pid = gen_id("GST")
    from services.sheets_service import build_row
    vals = {**data, "PurchaseID": pid, "Amount": amt, "SGST": sgst, "CGST": cgst, "TotalAmount": total, "CreatedDate": now_str()}
    row = build_row("GSTpurchases", vals)
    append_row("GSTpurchases", row)
    add_audit_log("CREATE", "GSTpurchases", pid, f"GST Purchase ₹{total} from {data.get('CompanyName','')}", user["email"])
    return {"success": True, "purchase_id": pid}


@router.put("/api/purchases/{purchase_id}")
async def update_purchase(request: Request, purchase_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("GSTpurchases", purchase_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, existing = result
    amt = float(data.get("Amount", 0) or 0)
    sgst = round(amt * 0.09, 2)
    cgst = round(amt * 0.09, 2)
    if data.get("SGST"):
        sgst = float(data["SGST"])
    if data.get("CGST"):
        cgst = float(data["CGST"])
    total = round(amt + sgst + cgst, 2)
    from services.sheets_service import build_row
    vals = {**existing, **data, "PurchaseID": purchase_id, "Amount": amt, "SGST": sgst, "CGST": cgst, "TotalAmount": total, "CreatedDate": existing.get("CreatedDate", now_str())}
    row = build_row("GSTpurchases", vals)
    update_row("GSTpurchases", row_num, row)
    add_audit_log("UPDATE", "GSTpurchases", purchase_id, f"GST Purchase updated ₹{total}", user["email"])
    return {"success": True}


@router.delete("/api/purchases/{purchase_id}")
async def delete_purchase(request: Request, purchase_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("GSTpurchases", purchase_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, record = result
    delete_row("GSTpurchases", row_num)
    add_audit_log("DELETE", "GSTpurchases", purchase_id, "GST Purchase deleted", user["email"])
    return {"success": True}
