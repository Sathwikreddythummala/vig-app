from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates

router = APIRouter(prefix="/purse", tags=["purse"])
HOLDERS = ["TSR", "MSR"]


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def purse_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="purse.html", context={"user": user})


@router.get("/api/summary")
async def purse_summary(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Purse")
    expenses = get_all_records("Expenses")
    summary = {}
    for h in HOLDERS:
        given = sum(float(r.get("Amount", 0) or 0) for r in records if r.get("Holder") == h and r.get("Type") == "Credit")
        spent_purse = sum(float(r.get("Amount", 0) or 0) for r in records if r.get("Holder") == h and r.get("Type") == "Debit")
        spent_expenses = sum(float(e.get("Amount", 0) or 0) for e in expenses if str(e.get("PaidBy", "")) == h)
        total_spent = spent_purse + spent_expenses
        balance = given - total_spent
        summary[h] = {
            "given": given,
            "spent_purse": spent_purse,
            "spent_expenses": spent_expenses,
            "total_spent": total_spent,
            "balance": balance,
        }
    return {"summary": summary}


@router.get("/api/list")
async def purse_list(request: Request, holder: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("Purse")
    if holder:
        records = [r for r in records if r.get("Holder") == holder]
    records.sort(key=lambda x: str(x.get("Date", "")), reverse=True)
    expenses = get_all_records("Expenses")
    exp_entries = []
    for e in expenses:
        paid_by = str(e.get("PaidBy", ""))
        if paid_by in HOLDERS and (not holder or paid_by == holder):
            exp_entries.append({
                "Date": e.get("ExpenseDate", ""),
                "Holder": paid_by,
                "Type": "Expense",
                "Amount": float(e.get("Amount", 0) or 0),
                "Description": str(e.get("Category", "")) + " - " + str(e.get("SubCategory", "") or e.get("Description", "")),
                "VehicleNumber": e.get("VehicleNumber", ""),
                "Category": e.get("Category", ""),
            })
    all_entries = []
    for r in records:
        all_entries.append({
            "PurseID": r.get("PurseID", ""),
            "Date": r.get("Date", ""),
            "Holder": r.get("Holder", ""),
            "Type": r.get("Type", ""),
            "Amount": float(r.get("Amount", 0) or 0),
            "Description": r.get("Description", ""),
            "VehicleNumber": r.get("VehicleNumber", ""),
            "Category": r.get("Category", ""),
            "Source": "purse",
        })
    for e in exp_entries:
        all_entries.append({
            "PurseID": "",
            "Date": e["Date"],
            "Holder": e["Holder"],
            "Type": "Expense",
            "Amount": e["Amount"],
            "Description": e["Description"],
            "VehicleNumber": e["VehicleNumber"],
            "Category": e["Category"],
            "Source": "expense",
        })
    all_entries.sort(key=lambda x: str(x.get("Date", "")), reverse=True)
    return {"entries": all_entries}


@router.post("/api/add")
async def add_purse(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    pid = gen_id("PRS")
    row = [
        pid,
        data.get("Date", ""),
        data.get("Holder", ""),
        data.get("Type", "Credit"),
        data.get("Amount", 0),
        data.get("Description", ""),
        data.get("VehicleNumber", ""),
        data.get("Category", ""),
        "",
        now_str(),
    ]
    append_row("Purse", row)
    add_audit_log("CREATE", "Purse", pid, f"{data.get('Type','')} ₹{data.get('Amount',0)} to {data.get('Holder','')}", user["email"])
    return {"success": True}


@router.delete("/api/{purse_id}")
async def delete_purse(request: Request, purse_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Purse", purse_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, record = result
    delete_row("Purse", row_num)
    add_audit_log("DELETE", "Purse", purse_id, "Purse entry deleted", user["email"])
    return {"success": True}
