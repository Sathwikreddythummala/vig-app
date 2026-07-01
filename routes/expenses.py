from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates
import pandas as pd
import io

router = APIRouter(prefix="/expenses", tags=["expenses"])

CATEGORIES = {
    "Fuel": ["Diesel", "AdBlue", "Engine Oil", "Coolant"],
    "Driver Expense": ["Salary", "Advance", "Meals", "Phone Recharge", "Travel", "Accommodation"],
    "Tyres": ["Tyre Purchase", "Tyre Puncture", "Tube", "Alignment", "Balancing"],
    "Maintenance": ["Servicing", "Greasing", "Mechanic Charges", "Electrical", "Welding", "Battery", "Clutch Plate", "Gear Box", "Oil Change", "Engine Repair"],
    "EMI": [],
    "Fastag": [],
    "Insurance": [],
    "Permit": [],
    "Accident": [],
    "Penalty": [],
    "Helper Expense": [],
    "Office Expense": ["Registration", "Xerox", "Stationery", "GST", "Miscellaneous"],
    "Other": [],
}


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def expenses_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="expenses.html", context={"user": user})


@router.get("/api/categories")
async def get_categories(request: Request):
    return {"categories": CATEGORIES}


@router.get("/api/list")
async def list_expenses(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    category: str = "",
    subcategory: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = 25,
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    expenses = get_all_records("Expenses")
    if date_from:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) >= date_from]
    if date_to:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) <= date_to]
    if vehicle:
        expenses = [e for e in expenses if str(e.get("VehicleNumber", "")) == vehicle]
    if category:
        expenses = [e for e in expenses if str(e.get("Category", "")) == category]
    if subcategory:
        expenses = [e for e in expenses if str(e.get("SubCategory", "")) == subcategory]
    if search:
        s = search.lower()
        expenses = [e for e in expenses if s in str(e.get("ExpenseID", "")).lower() or s in str(e.get("Description", "")).lower() or s in str(e.get("VehicleNumber", "")).lower() or s in str(e.get("DriverName", "")).lower() or s in str(e.get("Category", "")).lower()]
    expenses.sort(key=lambda x: str(x.get("CreatedDate", "")), reverse=True)
    total = len(expenses)
    start = (page - 1) * per_page
    paginated = expenses[start:start + per_page]
    total_amount = sum(float(e.get("Amount", 0) or 0) for e in expenses)
    return {
        "expenses": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "total_amount": total_amount,
    }


@router.post("/api/add")
async def add_expense(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    from utils.duplicate_check import is_duplicate
    if is_duplicate("Expenses", {
        "ExpenseDate": data.get("ExpenseDate", ""),
        "VehicleNumber": data.get("VehicleNumber", ""),
        "Category": data.get("Category", ""),
        "SubCategory": data.get("SubCategory", ""),
        "Amount": data.get("Amount", 0),
        "Description": data.get("Description", ""),
    }):
        return JSONResponse({"error": "Duplicate expense already exists"}, 400)
    eid = gen_id("EXP")
    for_month = data.get("ForMonth", "")
    if not for_month:
        for_month = str(data.get("ExpenseDate", ""))[:7]
    from services.sheets_service import build_row
    vals = {**data, "ExpenseID": eid, "ForMonth": for_month, "ExpenseFor": data.get("ExpenseFor", "Vehicle Expense"), "PaymentMode": data.get("PaymentMode", "Cash"), "CreatedDate": now_str()}
    row = build_row("Expenses", vals)
    append_row("Expenses", row)
    add_audit_log("CREATE", "Expenses", eid, f"Expense ₹{data.get('Amount',0)} added for {data.get('VehicleNumber','')}", user["email"])
    return {"success": True, "expense_id": eid}


@router.put("/api/{expense_id}")
async def update_expense(request: Request, expense_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Expenses", expense_id)
    if not result:
        return JSONResponse({"error": "Expense not found"}, 404)
    row_num, existing = result
    for_month = data.get("ForMonth", "")
    if not for_month:
        for_month = existing.get("ForMonth", str(data.get("ExpenseDate", ""))[:7])
    from services.sheets_service import build_row
    vals = {**existing, **data, "ExpenseID": expense_id, "ForMonth": for_month, "ExpenseFor": data.get("ExpenseFor", "Vehicle Expense"), "PaymentMode": data.get("PaymentMode", "Cash"), "CreatedDate": existing.get("CreatedDate", now_str())}
    row = build_row("Expenses", vals)
    update_row("Expenses", row_num, row)
    add_audit_log("UPDATE", "Expenses", expense_id, f"Expense updated to ₹{data.get('Amount',0)}", user["email"])
    return {"success": True}


@router.delete("/api/{expense_id}")
async def delete_expense_api(request: Request, expense_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Expenses", expense_id)
    if not result:
        return JSONResponse({"error": "Expense not found"}, 404)
    row_num, record = result
    delete_row("Expenses", row_num)
    add_audit_log("DELETE", "Expenses", expense_id, f"Expense ₹{record.get('Amount',0)} deleted", user["email"])
    return {"success": True}


@router.get("/api/export/excel")
async def export_excel(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    category: str = "",
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    expenses = get_all_records("Expenses")
    if date_from:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) >= date_from]
    if date_to:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) <= date_to]
    if vehicle:
        expenses = [e for e in expenses if str(e.get("VehicleNumber", "")) == vehicle]
    if category:
        expenses = [e for e in expenses if str(e.get("Category", "")) == category]
    df = pd.DataFrame(expenses)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=expenses.xlsx"},
    )


@router.get("/api/export/pdf")
async def export_pdf(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    category: str = "",
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    expenses = get_all_records("Expenses")
    if date_from:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) >= date_from]
    if date_to:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) <= date_to]
    if vehicle:
        expenses = [e for e in expenses if str(e.get("VehicleNumber", "")) == vehicle]
    if category:
        expenses = [e for e in expenses if str(e.get("Category", "")) == category]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph("Vigneshwara Enterprises - Expense Report", styles["Title"]), Spacer(1, 20)]
    header = ["Date", "Vehicle", "Driver", "Category", "SubCategory", "Description", "Amount", "Mode"]
    data = [header]
    total = 0
    for e in expenses:
        amt = float(e.get("Amount", 0) or 0)
        total += amt
        data.append([
            str(e.get("ExpenseDate", "")),
            str(e.get("VehicleNumber", "")),
            str(e.get("DriverName", "")),
            str(e.get("Category", "")),
            str(e.get("SubCategory", "")),
            str(e.get("Description", ""))[:30],
            f"₹{amt:,.2f}",
            str(e.get("PaymentMode", "")),
        ])
    data.append(["", "", "", "", "", "Total", f"₹{total:,.2f}", ""])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFD54F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FFF9C4")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#FFFDE7")]),
    ]))
    elements.append(table)
    doc.build(elements)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=expenses.pdf"},
    )
