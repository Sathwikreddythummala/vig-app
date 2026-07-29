from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates
import io

router = APIRouter(prefix="/purse", tags=["purse"])
HOLDERS = ["TSR", "MSR"]


def _purse_entries(holder: str = "", type_filter: str = ""):
    """All purse ledger rows (manual entries + expenses paid by a holder), filtered."""
    from utils.filters import filter_multi
    records = filter_multi(get_all_records("Purse"), "Holder", holder)
    holder_vals = {v.strip() for v in (holder or "").split(",") if v.strip()}
    entries = []
    for r in records:
        entries.append({
            "Date": r.get("Date", ""),
            "Holder": r.get("Holder", ""),
            "Type": r.get("Type", ""),
            "Amount": float(r.get("Amount", 0) or 0),
            "Category": r.get("Category", ""),
            "VehicleNumber": r.get("VehicleNumber", ""),
            "Description": r.get("Description", ""),
        })
    for e in get_all_records("Expenses"):
        paid_by = str(e.get("PaidBy", ""))
        if paid_by in HOLDERS and (not holder_vals or paid_by in holder_vals):
            entries.append({
                "Date": e.get("ExpenseDate", ""),
                "Holder": paid_by,
                "Type": "Expense",
                "Amount": float(e.get("Amount", 0) or 0),
                "Category": e.get("Category", ""),
                "VehicleNumber": e.get("VehicleNumber", ""),
                "Description": str(e.get("Category", "")) + " - " + str(e.get("SubCategory", "") or e.get("Description", "")),
            })
    if type_filter:
        types = {t.strip() for t in type_filter.split(",") if t.strip()}
        entries = [e for e in entries if e["Type"] in types]
    entries.sort(key=lambda x: (str(x["Holder"]), str(x["Date"])))
    return entries


def _holder_totals(entries):
    """Given / spent / balance per holder from a list of entries."""
    totals = {}
    for e in entries:
        t = totals.setdefault(e["Holder"], {"given": 0.0, "spent": 0.0})
        if e["Type"] == "Credit":
            t["given"] += e["Amount"]
        else:  # Debit or Expense
            t["spent"] += e["Amount"]
    for t in totals.values():
        t["balance"] = t["given"] - t["spent"]
    return totals


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
    from utils.filters import filter_multi
    records = filter_multi(records, "Holder", holder)
    records.sort(key=lambda x: str(x.get("Date", "")), reverse=True)
    expenses = get_all_records("Expenses")
    holder_vals = {v.strip() for v in (holder or "").split(",") if v.strip()}
    exp_entries = []
    for e in expenses:
        paid_by = str(e.get("PaidBy", ""))
        if paid_by in HOLDERS and (not holder_vals or paid_by in holder_vals):
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


@router.get("/api/export/excel")
async def export_excel(request: Request, holder: str = "", type: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    import pandas as pd
    entries = _purse_entries(holder, type)
    totals = _holder_totals(entries)
    cols = ["Date", "Holder", "Type", "Amount", "Category", "VehicleNumber", "Description"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Summary sheet — person-wise given / spent / balance
        summary_rows = [
            {"Holder": h, "Given": t["given"], "Spent": t["spent"], "Balance": t["balance"]}
            for h, t in sorted(totals.items())
        ]
        pd.DataFrame(summary_rows or [{"Holder": "", "Given": 0, "Spent": 0, "Balance": 0}]).to_excel(
            writer, sheet_name="Summary", index=False
        )
        # One sheet per holder
        holders = sorted({e["Holder"] for e in entries}) or [""]
        for h in holders:
            rows = [{k: e[k] for k in cols} for e in entries if e["Holder"] == h]
            sheet = (h or "All")[:31]
            pd.DataFrame(rows or [{c: "" for c in cols}])[cols].to_excel(writer, sheet_name=sheet, index=False)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=purse_report.xlsx"},
    )


@router.get("/api/export/pdf")
async def export_pdf(request: Request, holder: str = "", type: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    entries = _purse_entries(holder, type)
    totals = _holder_totals(entries)

    def safe(v, limit=40):
        return str(v or "").encode("ascii", "ignore").decode("ascii")[:limit]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Vigneshwara Enterprises - Purse Report", styles["Title"]), Spacer(1, 16)]
    header = ["Date", "Type", "Amount", "Category", "Vehicle", "Description"]
    for h in sorted({e["Holder"] for e in entries}):
        t = totals.get(h, {"given": 0, "spent": 0, "balance": 0})
        elements.append(Paragraph(
            f"{safe(h)} &nbsp;&nbsp; Given: Rs.{t['given']:,.0f} | Spent: Rs.{t['spent']:,.0f} | Balance: Rs.{t['balance']:,.0f}",
            styles["Heading3"],
        ))
        data = [header]
        for e in [x for x in entries if x["Holder"] == h]:
            data.append([
                safe(e["Date"], 12), safe(e["Type"], 10), f"Rs.{e['Amount']:,.0f}",
                safe(e["Category"], 20), safe(e["VehicleNumber"], 14), safe(e["Description"], 40),
            ])
        table = Table(data, repeatRows=1, colWidths=[62, 55, 65, 95, 75, 160])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFD54F")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FFFDE7")]),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 18))
    if len(elements) <= 2:
        elements.append(Paragraph("No purse entries for the selected filters.", styles["Normal"]))
    doc.build(elements)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=purse_report.pdf"},
    )


@router.post("/api/add")
async def add_purse(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    pid = gen_id("PRS")
    from services.sheets_service import build_row
    vals = {**data, "PurseID": pid, "Type": data.get("Type", "Credit"), "ReferenceID": "", "CreatedDate": now_str()}
    row = build_row("Purse", vals)
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
