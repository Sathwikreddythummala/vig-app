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
    "Driver Expense": ["Salary", "Advance", "Meals", "Phone Recharge", "Travel", "Accommodation", "Deductions"],
    "Tyres": ["Tyre Purchase", "Tyre Puncture", "Tube", "Alignment", "Balancing"],
    "Maintenance": ["Servicing", "Greasing", "Mechanic Charges", "Electrical", "Welding", "Battery", "Clutch Plate", "Gear Box", "Oil Change", "Engine Repair"],
    "EMI": [],
    "Fastag": [],
    "Insurance": [],
    "Permit": [],
    "Quarterly Tax/Green Tax": ["Quarterly Tax", "Green Tax"],
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
    user = get_user(request)
    if user and user.get("role") == "driver":
        return {"categories": {"Driver Expense": CATEGORIES["Driver Expense"]}}
    return {"categories": CATEGORIES}


@router.get("/api/list")
async def list_expenses(
    request: Request,
    month: str = "",
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    category: str = "",
    subcategory: str = "",
    paid_by: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = 25,
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    expenses = get_all_records("Expenses")
    if month:
        expenses = [e for e in expenses if (str(e.get("ForMonth", "")) or str(e.get("ExpenseDate", ""))[:7]) == month]
    # Drivers only see their own Driver Expense and Deductions entries
    if user.get("role") == "driver":
        driver_name = user.get("driver_name", "")
        expenses = [e for e in expenses if str(e.get("Category", "")) == "Driver Expense" and str(e.get("DriverName", "")) == driver_name]
    if date_from:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) >= date_from]
    if date_to:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) <= date_to]
    from utils.filters import filter_multi
    expenses = filter_multi(expenses, "VehicleNumber", vehicle)
    expenses = filter_multi(expenses, "Category", category)
    expenses = filter_multi(expenses, "SubCategory", subcategory)
    expenses = filter_multi(expenses, "PaidBy", paid_by)
    if search:
        s = search.lower().strip()
        _fields = ("ExpenseID", "Description", "VehicleNumber", "DriverName", "Category",
                   "SubCategory", "PaidBy", "PaymentMode", "Amount", "ExpenseFor", "ForMonth", "ExpenseDate")
        expenses = [e for e in expenses if any(s in str(e.get(f, "")).lower() for f in _fields)]
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
    # Warn on a possible duplicate: same date + expense-for (Vehicle/Company) + vehicle + amount.
    # Category, sub-category and description are intentionally ignored.
    # Unless the user chose to continue (_force).
    if not data.get("_force"):
        def _n(x):
            return str(x or "").strip().lower()
        def _amt(x):
            try:
                return round(float(x or 0), 2)
            except (ValueError, TypeError):
                return 0.0
        for e in get_all_records("Expenses"):
            if (_n(e.get("ExpenseDate")) == _n(data.get("ExpenseDate"))
                    and _n(e.get("ExpenseFor")) == _n(data.get("ExpenseFor", "Vehicle Expense"))
                    and _n(e.get("VehicleNumber")) == _n(data.get("VehicleNumber"))
                    and _amt(e.get("Amount")) == _amt(data.get("Amount"))):
                return {"duplicate": True,
                        "message": "A matching expense already exists (same date, type, vehicle and amount).",
                        "existing": {
                            "ExpenseDate": e.get("ExpenseDate", ""),
                            "ExpenseFor": e.get("ExpenseFor", ""),
                            "VehicleNumber": e.get("VehicleNumber", ""),
                            "DriverName": e.get("DriverName", ""),
                            "Category": e.get("Category", ""),
                            "SubCategory": e.get("SubCategory", ""),
                            "Amount": e.get("Amount", ""),
                            "Description": e.get("Description", ""),
                            "PaidBy": e.get("PaidBy", ""),
                            "PaymentMode": e.get("PaymentMode", ""),
                        }}
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
    vals = {**existing, **data, "ExpenseID": expense_id, "ForMonth": for_month, "ExpenseFor": data.get("ExpenseFor", "Vehicle Expense"), "PaymentMode": data.get("PaymentMode", "Cash"), "CreatedDate": existing.get("CreatedDate", now_str()), "UpdatedDate": now_str()}
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
    month: str = "",
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    category: str = "",
    paid_by: str = "",
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    expenses = get_all_records("Expenses")
    if month:
        expenses = [e for e in expenses if (str(e.get("ForMonth", "")) or str(e.get("ExpenseDate", ""))[:7]) == month]
    if date_from:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) >= date_from]
    if date_to:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) <= date_to]
    from utils.filters import filter_multi
    expenses = filter_multi(expenses, "VehicleNumber", vehicle)
    expenses = filter_multi(expenses, "Category", category)
    expenses = filter_multi(expenses, "PaidBy", paid_by)
    from utils.exports import to_numeric_df
    df = to_numeric_df(expenses, ["Amount"])
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
    month: str = "",
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    category: str = "",
    paid_by: str = "",
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    expenses = get_all_records("Expenses")
    if month:
        expenses = [e for e in expenses if (str(e.get("ForMonth", "")) or str(e.get("ExpenseDate", ""))[:7]) == month]
    if date_from:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) >= date_from]
    if date_to:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) <= date_to]
    from utils.filters import filter_multi
    expenses = filter_multi(expenses, "VehicleNumber", vehicle)
    expenses = filter_multi(expenses, "Category", category)
    expenses = filter_multi(expenses, "PaidBy", paid_by)
    def safe(v, limit=30):
        return str(v or "").encode("ascii", "ignore").decode("ascii")[:limit]

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
            safe(e.get("ExpenseDate")),
            safe(e.get("VehicleNumber")),
            safe(e.get("DriverName")),
            safe(e.get("Category")),
            safe(e.get("SubCategory")),
            safe(e.get("Description")),
            f"Rs.{amt:,.0f}",
            safe(e.get("PaymentMode")),
        ])
    data.append(["", "", "", "", "", "Total", f"Rs.{total:,.0f}", ""])
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
