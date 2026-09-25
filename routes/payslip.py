"""Payslip + Diesel statement generation (editable preview on the client, PDF here)."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
import io
from services.sheets_service import get_all_records, find_row_by_id, append_row, update_row, build_row, now_str, add_audit_log
from services.db import execute
from routes.drivers import vehicle_salaries_api

router = APIRouter(prefix="/payslip", tags=["payslip"])


def get_user(request: Request):
    return request.session.get("user")


# ---------------------------------------------------------------------------
# Company header (name/address/phone/GST) is stored in Settings, defaulting to
# the app's own name. Editable on the slip; saved back on generate.
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "CompanyName": "Vigneshwara Enterprises",
    "CompanyAddress": "",
    "CompanyPhone": "",
    "CompanyGST": "",
}


def _company_info() -> dict:
    vals = {s.get("Key", ""): str(s.get("Value", "")) for s in get_all_records("Settings")}
    return {
        "name": vals.get("CompanyName") or _DEFAULTS["CompanyName"],
        "address": vals.get("CompanyAddress", ""),
        "phone": vals.get("CompanyPhone", ""),
        "gst": vals.get("CompanyGST", ""),
    }


def _save_company_info(company: dict):
    mapping = {
        "CompanyName": str(company.get("name", "")).strip(),
        "CompanyAddress": str(company.get("address", "")).strip(),
        "CompanyPhone": str(company.get("phone", "")).strip(),
        "CompanyGST": str(company.get("gst", "")).strip(),
    }
    existing = {s.get("Key", ""): s for s in get_all_records("Settings")}
    for k, v in mapping.items():
        if k == "CompanyName" and not v:
            continue
        row = build_row("Settings", {"Key": k, "Value": v, "UpdatedDate": now_str()})
        if k in existing:
            update_row("Settings", k, row)
        else:
            append_row("Settings", row)


def _company_logo_bytes():
    try:
        rows = execute(
            "SELECT file_data, mime_type FROM document_files "
            "WHERE entity_type = 'Company' AND entity_id = 'VIGNESHWARA' AND doc_type = 'Logo' "
            "ORDER BY uploaded_date DESC LIMIT 1",
            fetch=True,
        )
        if rows:
            return bytes(rows[0]["file_data"]), (rows[0].get("mime_type") or "image/png")
    except Exception:
        pass
    return None, None


_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _month_label(month: str) -> str:
    try:
        y, m = int(month[:4]), int(month[5:7])
        return f"{_MONTHS[m]} {y}"
    except (ValueError, IndexError):
        return month


def _amount_in_words(amount) -> str:
    n = int(round(float(amount or 0)))
    if n == 0:
        return "Zero Rupees Only"
    neg = n < 0
    n = abs(n)
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two(x):
        if x < 20:
            return ones[x]
        return (tens[x // 10] + (" " + ones[x % 10] if x % 10 else "")).strip()

    def three(x):
        h, rest = x // 100, x % 100
        out = ""
        if h:
            out = ones[h] + " Hundred"
            if rest:
                out += " "
        if rest:
            out += two(rest)
        return out

    parts = []
    crore = n // 10000000; n %= 10000000
    lakh = n // 100000; n %= 100000
    thousand = n // 1000; n %= 1000
    hundred = n
    if crore:
        parts.append(two(crore) + " Crore")
    if lakh:
        parts.append(two(lakh) + " Lakh")
    if thousand:
        parts.append(two(thousand) + " Thousand")
    if hundred:
        parts.append(three(hundred))
    words = " ".join(parts).strip()
    return ("Minus " if neg else "") + words + " Rupees Only"


def _num(x):
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# PAYSLIP
# ---------------------------------------------------------------------------
@router.get("/api/payslip-data")
async def payslip_data(request: Request, driver_id: str = "", month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    body = await vehicle_salaries_api(request, month)
    if isinstance(body, JSONResponse):
        return body
    rows = [r for r in body.get("rows", []) if str(r.get("DriverID", "")) == str(driver_id)]

    driver = None
    for d in get_all_records("Drivers"):
        if str(d.get("DriverID", "")) == str(driver_id):
            driver = d
            break
    if not driver:
        return JSONResponse({"error": "Driver not found"}, 404)

    agg = {k: 0.0 for k in ("Gross", "Incentive", "SalaryPaid", "Advance", "Meals", "Deductions", "Other",
                            "Days", "EffectiveDays", "HalfDays", "AbsentDays", "VehicleSalary")}
    vehicles = []
    for r in rows:
        for k in agg:
            agg[k] += _num(r.get(k))
        if r.get("VehicleNumber") and r["VehicleNumber"] not in vehicles:
            vehicles.append(r["VehicleNumber"])
    net = agg["Gross"] + agg["Incentive"] - agg["SalaryPaid"] - agg["Advance"] - agg["Meals"] - agg["Deductions"] - agg["Other"]

    return {
        "company": _company_info(),
        "has_logo": _company_logo_bytes()[0] is not None,
        "month": month or body.get("month", ""),
        "month_label": _month_label(month or body.get("month", "")),
        "driver": {
            "id": driver.get("DriverID", ""),
            "name": driver.get("DriverName", ""),
            "mobile": driver.get("MobileNumber", ""),
            "bank": driver.get("BankName", ""),
            "account": driver.get("AccountNumber", ""),
            "ifsc": driver.get("IFSCCode", ""),
            "vehicle": ", ".join(vehicles) or (driver.get("AssignedVehicle", "") or "-"),
        },
        "days": {
            "worked": round(agg["Days"]), "paid": round(agg["EffectiveDays"], 1),
            "half": round(agg["HalfDays"]), "absent": round(agg["AbsentDays"]),
        },
        "earnings": [
            {"label": "Gross Salary", "amount": round(agg["Gross"], 2)},
            {"label": "Incentive", "amount": round(agg["Incentive"], 2)},
        ],
        "deductions": [
            {"label": "Salary Already Paid", "amount": round(agg["SalaryPaid"], 2)},
            {"label": "Advance", "amount": round(agg["Advance"], 2)},
            {"label": "Meals", "amount": round(agg["Meals"], 2)},
            {"label": "Deductions", "amount": round(agg["Deductions"], 2)},
            {"label": "Other", "amount": round(agg["Other"], 2)},
        ],
        "net": round(net, 2),
    }


@router.post("/api/payslip-pdf")
async def payslip_pdf(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    company = data.get("company", {})
    _save_company_info(company)
    driver = data.get("driver", {})
    earnings = [(str(e.get("label", "")), _num(e.get("amount"))) for e in data.get("earnings", []) if str(e.get("label", "")).strip()]
    deductions = [(str(d.get("label", "")), _num(d.get("amount"))) for d in data.get("deductions", []) if str(d.get("label", "")).strip()]
    total_earn = sum(a for _l, a in earnings)
    total_ded = sum(a for _l, a in deductions)
    net = total_earn - total_ded
    month_label = str(data.get("month_label", ""))
    days = data.get("days", {})
    remarks = str(data.get("remarks", "")).strip()

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT

    def safe(v):
        return str(v or "").encode("ascii", "ignore").decode("ascii")

    def rs(v):
        return "Rs. " + f"{_num(v):,.2f}"

    styles = getSampleStyleSheet()
    GOLD = colors.HexColor("#FFC107")
    GOLD_LT = colors.HexColor("#FFF9C4")
    DARK = colors.HexColor("#3E2723")
    h_name = ParagraphStyle("h_name", parent=styles["Title"], fontSize=17, textColor=DARK, spaceAfter=0, leading=20)
    h_sub = ParagraphStyle("h_sub", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#6D4C41"), leading=11)
    title_st = ParagraphStyle("title_st", parent=styles["Normal"], fontSize=12, textColor=DARK, alignment=TA_CENTER, spaceBefore=4)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=13)
    words_st = ParagraphStyle("words", parent=styles["Normal"], fontSize=9, textColor=DARK)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=16 * mm, rightMargin=16 * mm)
    W = doc.width
    elements = []

    # ---- Header: logo + company ----
    logo_bytes, _mime = _company_logo_bytes()
    comp_block = [Paragraph(safe(company.get("name", "")), h_name)]
    for line in (company.get("address", ""), company.get("phone", ""), company.get("gst", "")):
        if str(line).strip():
            lbl = "GSTIN: " if line == company.get("gst", "") and str(line).strip() else ""
            comp_block.append(Paragraph(safe(lbl + str(line)), h_sub))
    if logo_bytes:
        try:
            img = Image(io.BytesIO(logo_bytes), width=20 * mm, height=20 * mm, kind="proportional")
            head = Table([[img, comp_block]], colWidths=[24 * mm, W - 24 * mm])
        except Exception:
            head = Table([[comp_block]], colWidths=[W])
    else:
        head = Table([[comp_block]], colWidths=[W])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(head)
    elements.append(Spacer(1, 6))
    bar = Table([[""]], colWidths=[W], rowHeights=[3])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GOLD)]))
    elements.append(bar)
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"<b>SALARY SLIP</b> &nbsp;&nbsp; {safe(month_label)}", title_st))
    elements.append(Spacer(1, 10))

    # ---- Employee details ----
    emp = [
        ["Employee", safe(driver.get("name", "")), "Vehicle", safe(driver.get("vehicle", ""))],
        ["Mobile", safe(driver.get("mobile", "")), "Days Paid",
         f"{days.get('paid', '')} (of {days.get('worked', '')})  half {days.get('half', 0)} · abs {days.get('absent', 0)}"],
        ["Bank A/c", safe((driver.get("account", "") or "-")), "IFSC", safe(driver.get("ifsc", "") or "-")],
    ]
    emp_t = Table(emp, colWidths=[W * 0.16, W * 0.34, W * 0.16, W * 0.34])
    emp_t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6D4C41")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#6D4C41")),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("FONTNAME", (3, 0), (3, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 0), (-1, -1), GOLD_LT),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0C060")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#EAD9A0")),
    ]))
    elements.append(emp_t)
    elements.append(Spacer(1, 12))

    # ---- Earnings / Deductions side by side ----
    n = max(len(earnings), len(deductions))
    earnings += [("", None)] * (n - len(earnings))
    deductions += [("", None)] * (n - len(deductions))
    tbl = [["EARNINGS", "AMOUNT", "DEDUCTIONS", "AMOUNT"]]
    for (el, ea), (dl, da) in zip(earnings, deductions):
        tbl.append([safe(el), rs(ea) if ea is not None else "", safe(dl), rs(da) if da is not None else ""])
    tbl.append(["Total Earnings", rs(total_earn), "Total Deductions", rs(total_ded)])
    ed = Table(tbl, colWidths=[W * 0.30, W * 0.20, W * 0.30, W * 0.20])
    ed.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, 0), DARK),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GOLD_LT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0C060")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(ed)
    elements.append(Spacer(1, 4))

    # ---- Net payable ----
    net_t = Table([["NET PAYABLE", rs(net)]], colWidths=[W * 0.60, W * 0.40])
    net_t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 12),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(net_t)
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>In words:</b> " + safe(_amount_in_words(net)), words_st))

    if remarks:
        elements.append(Spacer(1, 8))
        elements.append(Paragraph("<b>Remarks:</b> " + safe(remarks), small))

    # ---- Signatures ----
    elements.append(Spacer(1, 36))
    sig = Table([["_______________________", "_______________________"],
                 ["Employee Signature", "Authorised Signatory"]], colWidths=[W / 2, W / 2])
    sig.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#6D4C41")),
    ]))
    elements.append(sig)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph("This is a computer-generated salary slip.", ParagraphStyle(
        "foot", parent=styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#9C7B63"), alignment=TA_CENTER)))

    doc.build(elements)
    buf.seek(0)
    add_audit_log("CREATE", "Payslip", str(driver.get("id", "")),
                  f"Payslip {month_label} for {driver.get('name', '')} (net Rs.{net:,.0f})", user.get("email", ""))
    fname = "payslip_" + safe(driver.get("name", "driver")).replace(" ", "_") + "_" + str(data.get("month", "")) + ".pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


# ---------------------------------------------------------------------------
# DIESEL STATEMENT
# ---------------------------------------------------------------------------
@router.get("/api/diesel-data")
async def diesel_data(request: Request, driver_id: str = "", month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    driver = None
    for d in get_all_records("Drivers"):
        if str(d.get("DriverID", "")) == str(driver_id):
            driver = d
            break
    if not driver:
        return JSONResponse({"error": "Driver not found"}, 404)
    dname = driver.get("DriverName", "")
    entries = [f for f in get_all_records("FuelEntries")
               if str(f.get("DriverName", "")).strip() == str(dname).strip()
               and (not month or str(f.get("EntryDate", ""))[:7] == month)]
    entries.sort(key=lambda x: str(x.get("EntryDate", "")))
    rows = [{
        "date": e.get("EntryDate", ""), "vehicle": e.get("VehicleNumber", ""),
        "fuel_type": e.get("FuelType", "Diesel"), "litres": _num(e.get("Litres")),
        "amount": _num(e.get("Amount")), "station": e.get("FuelStation", ""),
        "status": str(e.get("PaymentStatus", "")).strip() or "Paid",
    } for e in entries]
    return {
        "company": _company_info(),
        "month": month, "month_label": _month_label(month),
        "driver": {"id": driver.get("DriverID", ""), "name": dname,
                   "vehicle": driver.get("AssignedVehicle", "") or "-"},
        "rows": rows,
        "total_litres": round(sum(r["litres"] for r in rows), 2),
        "total_amount": round(sum(r["amount"] for r in rows), 2),
    }


@router.post("/api/diesel-pdf")
async def diesel_pdf(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    company = data.get("company", {})
    _save_company_info(company)
    driver = data.get("driver", {})
    rows = data.get("rows", [])
    month_label = str(data.get("month_label", ""))
    remarks = str(data.get("remarks", "")).strip()
    total_litres = sum(_num(r.get("litres")) for r in rows)
    total_amount = sum(_num(r.get("amount")) for r in rows)

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    def safe(v):
        return str(v or "").encode("ascii", "ignore").decode("ascii")

    styles = getSampleStyleSheet()
    GOLD = colors.HexColor("#FFC107")
    GOLD_LT = colors.HexColor("#FFF9C4")
    DARK = colors.HexColor("#3E2723")
    h_name = ParagraphStyle("h_name", parent=styles["Title"], fontSize=17, textColor=DARK, leading=20)
    h_sub = ParagraphStyle("h_sub", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#6D4C41"), leading=11)
    title_st = ParagraphStyle("title_st", parent=styles["Normal"], fontSize=12, textColor=DARK, alignment=TA_CENTER)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=13)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=16 * mm, rightMargin=16 * mm)
    W = doc.width
    elements = []
    logo_bytes, _m = _company_logo_bytes()
    comp_block = [Paragraph(safe(company.get("name", "")), h_name)]
    for line in (company.get("address", ""), company.get("phone", "")):
        if str(line).strip():
            comp_block.append(Paragraph(safe(str(line)), h_sub))
    if logo_bytes:
        try:
            img = Image(io.BytesIO(logo_bytes), width=20 * mm, height=20 * mm, kind="proportional")
            head = Table([[img, comp_block]], colWidths=[24 * mm, W - 24 * mm])
        except Exception:
            head = Table([[comp_block]], colWidths=[W])
    else:
        head = Table([[comp_block]], colWidths=[W])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(head)
    elements.append(Spacer(1, 6))
    bar = Table([[""]], colWidths=[W], rowHeights=[3])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GOLD)]))
    elements.append(bar)
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"<b>DIESEL / FUEL STATEMENT</b> &nbsp;&nbsp; {safe(month_label)}", title_st))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"<b>Driver:</b> {safe(driver.get('name',''))} &nbsp;&nbsp; <b>Vehicle:</b> {safe(driver.get('vehicle',''))}", small))
    elements.append(Spacer(1, 8))

    tbl = [["Date", "Vehicle", "Type", "Litres", "Amount (Rs.)", "Station", "Status"]]
    for r in rows:
        tbl.append([
            safe(r.get("date")), safe(r.get("vehicle")), safe(r.get("fuel_type")),
            f"{_num(r.get('litres')):,.2f}" if _num(r.get("litres")) else "",
            f"{_num(r.get('amount')):,.0f}", safe(r.get("station")), safe(r.get("status", "")),
        ])
    tbl.append(["", "", "TOTAL", f"{total_litres:,.2f}", f"{total_amount:,.0f}", "", ""])
    t = Table(tbl, colWidths=[W * 0.13, W * 0.16, W * 0.11, W * 0.12, W * 0.16, W * 0.20, W * 0.12], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, 0), DARK),
        ("ALIGN", (3, 0), (4, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GOLD_LT),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E0C060")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#FFFDE7")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)
    if remarks:
        elements.append(Spacer(1, 8))
        elements.append(Paragraph("<b>Remarks:</b> " + safe(remarks), small))
    elements.append(Spacer(1, 36))
    sig = Table([["_______________________", "_______________________"],
                 ["Driver Signature", "Authorised Signatory"]], colWidths=[W / 2, W / 2])
    sig.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                             ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#6D4C41"))]))
    elements.append(sig)
    doc.build(elements)
    buf.seek(0)
    add_audit_log("CREATE", "DieselSlip", str(driver.get("id", "")),
                  f"Diesel statement {month_label} for {driver.get('name', '')}", user.get("email", ""))
    fname = "diesel_" + safe(driver.get("name", "driver")).replace(" ", "_") + "_" + str(data.get("month", "")) + ".pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})
