"""Payslip + Diesel-mileage slip generation (editable preview on the client, PDF here).

Matches the Vigneshwara letterhead template: full company header, red theme,
detail grid, earnings/deductions (Meals is an allowance/earning), a diesel-mileage
incentive computed from Approved KM vs allowed mileage, and amount in words w/ paise.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
import io
from services.sheets_service import get_all_records, append_row, update_row, build_row, now_str, add_audit_log, find_row_by_id, gen_id
from services.db import execute
from routes.drivers import vehicle_salaries_api

router = APIRouter(prefix="/payslip", tags=["payslip"])


def get_user(request: Request):
    return request.session.get("user")


# ---------------------------------------------------------------------------
# Company header (stored in Settings; editable on the slip, saved on generate).
# Defaults are Vigneshwara's real details; change per-company for white-label.
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "CompanyName": "Vigneshwara Enterprises",
    "CompanyAddress": "4-56, Kranthi Colony, Sri Sai Nagar, Medipally, Medchal, Telangana - 500098",
    "CompanyGST": "36AAWFV6672K1Z3",
    "CompanyPhone": "9908697249",
    "CompanyPhone2": "8179800846",
    "CompanyEmail": "vigneshwara202223@gmail.com",
}
_ALLOWED_MILEAGE_DEFAULT = 2.25
_DIESEL_RATE_DEFAULT = 60.0


def _num(x):
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def _company_info() -> dict:
    vals = {s.get("Key", ""): str(s.get("Value", "")) for s in get_all_records("Settings")}
    return {
        "name": vals.get("CompanyName") or _DEFAULTS["CompanyName"],
        "address": vals.get("CompanyAddress") or _DEFAULTS["CompanyAddress"],
        "gst": vals.get("CompanyGST") or _DEFAULTS["CompanyGST"],
        "phone": vals.get("CompanyPhone") or _DEFAULTS["CompanyPhone"],
        "phone2": vals.get("CompanyPhone2") or _DEFAULTS["CompanyPhone2"],
        "email": vals.get("CompanyEmail") or _DEFAULTS["CompanyEmail"],
    }


def _save_company_info(company: dict):
    mapping = {
        "CompanyName": str(company.get("name", "")).strip(),
        "CompanyAddress": str(company.get("address", "")).strip(),
        "CompanyGST": str(company.get("gst", "")).strip(),
        "CompanyPhone": str(company.get("phone", "")).strip(),
        "CompanyPhone2": str(company.get("phone2", "")).strip(),
        "CompanyEmail": str(company.get("email", "")).strip(),
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
            return bytes(rows[0]["file_data"])
    except Exception:
        pass
    return None


_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _month_label(month: str) -> str:
    try:
        y, m = int(month[:4]), int(month[5:7])
        return f"{_MONTHS[m]} {y}"
    except (ValueError, IndexError):
        return month or ""


def _words_int(n: int) -> str:
    if n == 0:
        return ""
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two(x):
        return ones[x] if x < 20 else (tens[x // 10] + (" " + ones[x % 10] if x % 10 else "")).strip()

    def three(x):
        h, rest = x // 100, x % 100
        out = (ones[h] + " Hundred") if h else ""
        if rest:
            out += (" " if h else "") + two(rest)
        return out

    parts = []
    crore = n // 10000000; n %= 10000000
    lakh = n // 100000; n %= 100000
    thousand = n // 1000; n %= 1000
    if crore:
        parts.append(two(crore) + " Crore")
    if lakh:
        parts.append(two(lakh) + " Lakh")
    if thousand:
        parts.append(two(thousand) + " Thousand")
    if n:
        parts.append(three(n))
    return " ".join(parts).strip()


def _amount_in_words(amount) -> str:
    amt = float(amount or 0)
    neg = amt < 0
    amt = abs(amt)
    rupees = int(amt)
    paise = int(round((amt - rupees) * 100))
    if paise == 100:
        rupees += 1
        paise = 0
    out = "Rupees " + (_words_int(rupees) or "Zero")
    if paise:
        out += " and Paise " + _words_int(paise)
    out += " Only"
    return ("Minus " if neg else "") + out


# ---------------------------------------------------------------------------
# Diesel-mileage incentive: pay the driver for diesel saved vs the vehicle's
# allowed mileage.  allowed_litres = ApprovedKm / allowed_mileage;
# saved = allowed_litres - consumed;  amount = saved * diesel_rate.
# ---------------------------------------------------------------------------
def _vehicle(vnum: str):
    vnum = str(vnum or "").strip()
    for v in get_all_records("Vehicles"):
        if str(v.get("VehicleNumber", "")).strip() == vnum:
            return v
    return None


def _diesel_mileage(vnum: str, month: str) -> dict:
    v = _vehicle(vnum)
    allowed_mileage = (_num(v.get("AllowedMileage")) if v else 0) or _ALLOWED_MILEAGE_DEFAULT
    diesel_rate = (_num(v.get("DieselRate")) if v else 0) or _DIESEL_RATE_DEFAULT
    approved_km = consumed = 0.0
    for r in get_all_records("MileageReports"):
        if str(r.get("VehicleNumber", "")).strip() == str(vnum).strip() and str(r.get("Month", "")) == month:
            approved_km = _num(r.get("ApprovedKm"))
            consumed = _num(r.get("Litres"))
            break
    mileage = (approved_km / consumed) if consumed else 0.0
    allowed_litres = (approved_km / allowed_mileage) if allowed_mileage else 0.0
    saved = allowed_litres - consumed
    amount = saved * diesel_rate
    return {
        "vehicle": vnum,
        "approved_km": round(approved_km, 2), "consumed": round(consumed, 2),
        "mileage": round(mileage, 2), "allowed_mileage": allowed_mileage, "diesel_rate": diesel_rate,
        "allowed_litres": round(allowed_litres, 2), "saved": round(saved, 2),
        "amount": round(amount, 2),
    }


def _set_vehicle_monthly_salary(vehicle_id: str, amount: float):
    from services.sheets_service import SHEET_HEADERS
    res = find_row_by_id("Vehicles", vehicle_id)
    if not res:
        return
    rn, v = res
    headers = SHEET_HEADERS["Vehicles"]
    row = [v.get(h, "") for h in headers]
    row[headers.index("MonthlySalary")] = str(amount)
    row[headers.index("UpdatedDate")] = now_str()
    update_row("Vehicles", rn, row)


def _set_incentive(driver_id: str, driver_name: str, month: str, amount: float):
    from services.sheets_service import SHEET_HEADERS
    headers = SHEET_HEADERS["Incentives"]
    for inc in get_all_records("Incentives"):
        if str(inc.get("DriverID", "")) == str(driver_id) and str(inc.get("ForMonth", "")) == month:
            res = find_row_by_id("Incentives", inc.get("IncentiveID", ""))
            if res:
                rn, ex = res
                row = [ex.get(h, "") for h in headers]
                row[headers.index("Amount")] = str(amount)
                row[headers.index("UpdatedDate")] = now_str()
                update_row("Incentives", rn, row)
            return
    vals = {"IncentiveID": gen_id("INC"), "DriverID": driver_id, "DriverName": driver_name,
            "ForMonth": month, "Amount": str(amount), "Description": "Payslip",
            "EnteredBy": "payslip", "CreatedDate": now_str(), "UpdatedDate": now_str()}
    append_row("Incentives", build_row("Incentives", vals))


def _driver_and_primary_vehicle(driver_id: str, rows: list):
    driver = None
    for d in get_all_records("Drivers"):
        if str(d.get("DriverID", "")) == str(driver_id):
            driver = d
            break
    vehicles = []
    for r in rows:
        vn = str(r.get("VehicleNumber", "")).strip()
        if vn and vn not in vehicles:
            vehicles.append(vn)
    primary = ""
    if driver and str(driver.get("AssignedVehicle", "")).strip():
        primary = str(driver.get("AssignedVehicle", "")).strip()
    elif vehicles:
        primary = vehicles[0]
    return driver, primary, vehicles


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
    month = month or body.get("month", "")
    days_in_month = body.get("days_in_month", 0)
    rows = [r for r in body.get("rows", []) if str(r.get("DriverID", "")) == str(driver_id)]
    driver, primary, vehicles = _driver_and_primary_vehicle(driver_id, rows)
    if not driver:
        return JSONResponse({"error": "Driver not found"}, 404)

    agg = {k: 0.0 for k in ("Gross", "Incentive", "SalaryPaid", "Advance", "Meals", "Deductions", "Other",
                            "EffectiveDays", "HalfDays", "AbsentDays")}
    for r in rows:
        for k in agg:
            agg[k] += _num(r.get(k))

    # monthly salary / per-day from the primary vehicle
    prow = next((r for r in rows if str(r.get("VehicleNumber", "")).strip() == primary), None)
    if prow:
        monthly_salary = _num(prow.get("VehicleSalary"))
        per_day = _num(prow.get("PerDay"))
    else:
        pv = _vehicle(primary)
        monthly_salary = _num(pv.get("MonthlySalary")) if pv else 0.0
        per_day = round(monthly_salary / days_in_month, 2) if days_in_month else 0.0

    dm = _diesel_mileage(primary, month) if primary else {"amount": 0.0}
    meals = round(agg["Meals"], 2)
    dm_amt = round(dm.get("amount", 0.0), 2)
    salary_paid = round(agg["SalaryPaid"], 2)
    advance = round(agg["Advance"], 2)
    deductions = round(agg["Deductions"], 2)
    other = round(agg["Other"], 2)

    # Per-vehicle salary breakdown: a driver on 2 vehicles earns from EACH vehicle,
    # pro-rated by the days driven on it (vehicle salary / days-in-month x days).
    veh_rows = []
    for r in rows:
        veh_rows.append({
            "vehicle_id": r.get("VehicleID", ""),
            "vehicle_number": r.get("VehicleNumber", ""),
            "monthly_salary": _num(r.get("VehicleSalary")),
            "per_day": _num(r.get("PerDay")),
            "days": _num(r.get("Days")),
            "effective_days": _num(r.get("EffectiveDays")),
            "gross": round(_num(r.get("Gross")), 2),
        })
    if not veh_rows and primary:
        pv = _vehicle(primary)
        if pv:
            ms0 = _num(pv.get("MonthlySalary"))
            veh_rows.append({
                "vehicle_id": pv.get("VehicleID", ""), "vehicle_number": primary,
                "monthly_salary": ms0, "per_day": round(ms0 / days_in_month, 2) if days_in_month else 0.0,
                "days": 0, "effective_days": 0, "gross": 0.0,
            })
    total_gross = round(sum(v["gross"] for v in veh_rows), 2)
    net = (total_gross + dm_amt + meals) - (salary_paid + advance + deductions + other)

    # itemized payment/deduction history from the DB (this driver's Driver Expense entries this month)
    dname = str(driver.get("DriverName", "")).strip()
    history = []
    for e in get_all_records("Expenses"):
        if str(e.get("DriverName", "")).strip() != dname:
            continue
        if str(e.get("Category", "")).strip() != "Driver Expense":
            continue
        if (str(e.get("ForMonth", "")).strip() or str(e.get("ExpenseDate", ""))[:7]) != month:
            continue
        history.append({
            "date": str(e.get("ExpenseDate", ""))[:10],
            "particular": e.get("SubCategory", "") or "Other",
            "amount": round(_num(e.get("Amount")), 2),
        })
    history.sort(key=lambda x: str(x["date"]))

    return {
        "company": _company_info(),
        "month": month, "month_label": _month_label(month),
        "driver": {
            "id": driver.get("DriverID", ""), "name": driver.get("DriverName", ""),
            "designation": driver.get("EmployeeType", "") or "Driver",
            "vehicle": primary or (", ".join(vehicles) or "-"),
            "mobile": driver.get("MobileNumber", ""),
            "bank": driver.get("BankName", ""), "account": driver.get("AccountNumber", ""), "ifsc": driver.get("IFSCCode", ""),
        },
        "meta": {
            "days_in_month": days_in_month,
            "effective_days": round(agg["EffectiveDays"], 1),
            "half_days": round(agg["HalfDays"]), "absent_days": round(agg["AbsentDays"]),
        },
        "vehicles": veh_rows,
        "other_earnings": [
            {"label": "Diesel Mileage", "amount": dm_amt},
            {"label": "Meals Allowance", "amount": meals},
        ],
        "deductions": [
            {"label": "Salary Paid", "amount": salary_paid},
            {"label": "Advance", "amount": advance},
            {"label": "Meals Deduction", "amount": 0.0},
            {"label": "Deductions", "amount": deductions},
            {"label": "Other", "amount": other},
        ],
        "history": history,
        "net": round(net, 2),
    }


# ---- shared PDF header ----
def _letterhead(company, W, mm):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    styles = getSampleStyleSheet()
    GOLD = colors.HexColor("#FFC107")
    INK = colors.HexColor("#3E2723")

    def safe(v):
        return str(v or "").encode("ascii", "ignore").decode("ascii")

    name_st = ParagraphStyle("coName", parent=styles["Title"], fontSize=19, textColor=INK, leading=22, spaceAfter=1)
    sub_st = ParagraphStyle("coSub", parent=styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#333333"),
                            leading=10, alignment=1)
    ph_st = ParagraphStyle("coPh", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#111111"),
                           leading=11, alignment=2)
    center = [Paragraph(safe(company.get("name", "")), ParagraphStyle("cn", parent=name_st, alignment=1))]
    if company.get("address"):
        center.append(Paragraph(safe(company["address"]), sub_st))
    line2 = []
    if company.get("gst"):
        line2.append("GSTIN: " + safe(company["gst"]))
    if company.get("email"):
        line2.append("Email: " + safe(company["email"]))
    if line2:
        center.append(Paragraph(" &nbsp;|&nbsp; ".join(line2), sub_st))
    phones = []
    if company.get("phone"):
        phones.append("Cell: " + safe(company["phone"]))
    if company.get("phone2"):
        phones.append(safe(company["phone2"]))
    right = [Paragraph("<br/>".join(phones), ph_st)] if phones else [Paragraph("", ph_st)]

    logo = _company_logo_bytes()
    left = ""
    if logo:
        try:
            left = Image(io.BytesIO(logo), width=18 * mm, height=18 * mm, kind="proportional")
        except Exception:
            left = ""
    head = Table([[left, center, right]], colWidths=[20 * mm, W - 56 * mm, 36 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (0, 0), 0),
                              ("RIGHTPADDING", (-1, 0), (-1, 0), 0)]))
    bar = Table([[""]], colWidths=[W], rowHeights=[3.5])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GOLD)]))
    return [head, Spacer(1, 5), bar, Spacer(1, 9)]


def _title_bar(text, W):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    GOLD = colors.HexColor("#FFC107")
    INK = colors.HexColor("#3E2723")
    t = Table([[text]], colWidths=[W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GOLD), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 13),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


@router.post("/api/payslip-pdf")
async def payslip_pdf(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    company = data.get("company", {})
    _save_company_info(company)
    driver = data.get("driver", {})
    meta = data.get("meta", {})
    days_in_month = _num(meta.get("days_in_month")) or 30
    # Per-vehicle salary. Recompute each gross server-side from the (possibly edited)
    # monthly salary, and WRITE BACK the monthly salary to that vehicle.
    veh = []
    for v in data.get("vehicles", []):
        ms = _num(v.get("monthly_salary"))
        eff = _num(v.get("effective_days"))
        pd = round(ms / days_in_month, 2) if days_in_month else 0.0
        veh.append({"num": str(v.get("vehicle_number", "")), "ms": ms, "per_day": pd,
                    "days": _num(v.get("days")), "eff": eff, "gross": round(pd * eff, 2)})
        if str(v.get("vehicle_id", "")).strip() and str(v.get("monthly_salary")).strip() not in ("", "None"):
            _set_vehicle_monthly_salary(str(v.get("vehicle_id")).strip(), ms)
    total_salary = round(sum(x["gross"] for x in veh), 2)
    other_earnings = [(str(e.get("label", "")), _num(e.get("amount"))) for e in data.get("other_earnings", []) if str(e.get("label", "")).strip()]
    earnings = [("Salary (Total)", total_salary)] + other_earnings
    deductions = [(str(d.get("label", "")), _num(d.get("amount"))) for d in data.get("deductions", []) if str(d.get("label", "")).strip()]
    total_earn = sum(a for _l, a in earnings)
    total_ded = sum(a for _l, a in deductions)
    net = total_earn - total_ded
    month_label = str(data.get("month_label", ""))
    remarks = str(data.get("remarks", "")).strip()

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    def safe(v):
        return str(v or "").encode("ascii", "ignore").decode("ascii")

    def rs(v):
        return "Rs. " + f"{_num(v):,.2f}"

    styles = getSampleStyleSheet()
    GOLD = colors.HexColor("#FFC107")
    INK = colors.HexColor("#3E2723")
    GOLD_LT = colors.HexColor("#FFF9C4")
    AMBER = colors.HexColor("#B26A00")
    DARK = colors.HexColor("#222222")
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=13)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=16 * mm, leftMargin=15 * mm, rightMargin=15 * mm)
    W = doc.width
    el = _letterhead(company, W, mm)
    el.append(_title_bar("PAYSLIP FOR THE MONTH OF " + safe(month_label).upper(), W))
    el.append(Spacer(1, 8))

    grid = [
        ["Driver Name", safe(driver.get("name", "")), "Designation", safe(driver.get("designation", "Driver"))],
        ["Pay Period", safe(month_label), "Days in Month", str(int(days_in_month))],
        ["Days Worked", f"{sum(x['eff'] for x in veh):g}", "Half / Absent",
         f"{meta.get('half_days', 0)} / {meta.get('absent_days', 0)}"],
    ]
    gt = Table(grid, colWidths=[W * 0.18, W * 0.32, W * 0.18, W * 0.32])
    gt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (0, -1), GOLD_LT), ("BACKGROUND", (2, 0), (2, -1), GOLD_LT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0C060")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    el.append(gt)
    el.append(Spacer(1, 10))

    # Salary by vehicle (a driver on 2 vehicles earns from each, by days on it)
    el.append(Paragraph("<b>Salary by Vehicle</b>", small))
    el.append(Spacer(1, 3))
    vt = [["VEHICLE", "DAYS (eff/total)", "MONTHLY SALARY", "PER DAY", "SALARY"]]
    for x in veh:
        vt.append([safe(x["num"]), f"{x['eff']:g} / {x['days']:g}", rs(x["ms"]), rs(x["per_day"]), rs(x["gross"])])
    vt.append(["", "", "", "Total Salary", rs(total_salary)])
    vtab = Table(vt, colWidths=[W * 0.22, W * 0.18, W * 0.24, W * 0.16, W * 0.20])
    vtab.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), GOLD), ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (2, 0), (4, -1), "RIGHT"), ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), GOLD_LT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0C060")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    el.append(vtab)
    el.append(Spacer(1, 12))

    n = max(len(earnings), len(deductions))
    earnings += [("", None)] * (n - len(earnings))
    deductions += [("", None)] * (n - len(deductions))
    tbl = [["EARNINGS", "AMOUNT", "DEDUCTIONS", "AMOUNT"]]
    for (el2, ea), (dl, da) in zip(earnings, deductions):
        tbl.append([safe(el2), rs(ea) if ea is not None else "", safe(dl), rs(da) if da is not None else ""])
    tbl.append(["Total Earnings", rs(total_earn), "Total Deductions", rs(total_ded)])
    ed = Table(tbl, colWidths=[W * 0.30, W * 0.20, W * 0.30, W * 0.20])
    ed.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), GOLD), ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), GOLD_LT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0C060")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    el.append(ed)
    el.append(Spacer(1, 10))

    net_box = Table([["NET PAYABLE", rs(net)],
                     [Paragraph("<b>In words:</b> " + safe(_amount_in_words(net)),
                                ParagraphStyle("w", parent=small, textColor=DARK)), ""]],
                    colWidths=[W * 0.60, W * 0.40])
    net_box.setStyle(TableStyle([
        ("SPAN", (0, 1), (-1, 1)),
        ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (1, 0), 13),
        ("TEXTCOLOR", (0, 0), (1, 0), AMBER), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BACKGROUND", (0, 0), (1, 0), GOLD_LT),
        ("BOX", (0, 0), (-1, -1), 1.2, GOLD),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 1), (-1, 1), 6), ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    el.append(net_box)

    history = data.get("history", [])
    if history:
        el.append(Spacer(1, 12))
        el.append(Paragraph("<b>Payments &amp; Deductions this month</b> (from records)", small))
        el.append(Spacer(1, 4))
        ht = [["Date", "Particular", "Amount"]]
        htot = 0.0
        for h in history:
            amt = _num(h.get("amount")); htot += amt
            ht.append([safe(h.get("date")), safe(h.get("particular")), rs(amt)])
        ht.append(["", "Total", rs(htot)])
        htbl = Table(ht, colWidths=[W * 0.22, W * 0.48, W * 0.30])
        htbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, 0), GOLD), ("TEXTCOLOR", (0, 0), (-1, 0), INK),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), GOLD_LT),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E0C060")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#FFFDF2")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ]))
        el.append(htbl)

    if remarks:
        el.append(Spacer(1, 8))
        el.append(Paragraph("<b>Remarks:</b> " + safe(remarks), small))

    el.append(Spacer(1, 40))
    el.append(_signatures(W, "Driver's Signature", company.get("name", "")))
    el.append(Spacer(1, 10))
    el.append(Paragraph("This is a computer-generated payslip.", ParagraphStyle(
        "foot", parent=styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#999999"), alignment=1)))

    doc.build(el)
    buf.seek(0)
    add_audit_log("CREATE", "Payslip", str(driver.get("id", "")),
                  f"Payslip {month_label} for {driver.get('name', '')} (net Rs.{net:,.0f})", user.get("email", ""))
    fname = "Payslip_" + safe(driver.get("name", "driver")).replace(" ", "_") + "_" + str(data.get("month", "")) + ".pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


def _signatures(W, left_label, company_name):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    def safe(v):
        return str(v or "").encode("ascii", "ignore").decode("ascii")
    sig = Table([
        ["______________________", "______________________"],
        [left_label, "For " + safe(company_name)],
        ["", "Authorised Signatory"],
    ], colWidths=[W / 2, W / 2])
    sig.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "LEFT"), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 1), (-1, 2), "Helvetica-Bold"),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
    ]))
    return sig


# ---------------------------------------------------------------------------
# DIESEL MILEAGE SLIP
# ---------------------------------------------------------------------------
@router.get("/api/diesel-data")
async def diesel_data(request: Request, driver_id: str = "", month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    body = await vehicle_salaries_api(request, month)
    month = month or (body.get("month", "") if not isinstance(body, JSONResponse) else month)
    rows = [] if isinstance(body, JSONResponse) else [r for r in body.get("rows", []) if str(r.get("DriverID", "")) == str(driver_id)]
    driver, primary, vehicles = _driver_and_primary_vehicle(driver_id, rows)
    if not driver:
        return JSONResponse({"error": "Driver not found"}, 404)
    dm = _diesel_mileage(primary, month) if primary else {
        "vehicle": "", "approved_km": 0, "consumed": 0, "mileage": 0,
        "allowed_mileage": _ALLOWED_MILEAGE_DEFAULT, "diesel_rate": _DIESEL_RATE_DEFAULT,
        "allowed_litres": 0, "saved": 0, "amount": 0}
    return {
        "company": _company_info(),
        "month": month, "month_label": _month_label(month),
        "driver": {"id": driver.get("DriverID", ""), "name": driver.get("DriverName", ""),
                   "designation": driver.get("EmployeeType", "") or "Driver",
                   "vehicle": primary or (", ".join(vehicles) or "-")},
        "mileage": dm,
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
    m = data.get("mileage", {})
    month_label = str(data.get("month_label", ""))
    remarks = str(data.get("remarks", "")).strip()
    # recompute from the (possibly edited) inputs for integrity
    approved_km = _num(m.get("approved_km"))
    consumed = _num(m.get("consumed"))
    allowed_mileage = _num(m.get("allowed_mileage")) or _ALLOWED_MILEAGE_DEFAULT
    diesel_rate = _num(m.get("diesel_rate")) or _DIESEL_RATE_DEFAULT
    mileage = (approved_km / consumed) if consumed else 0.0
    allowed_litres = (approved_km / allowed_mileage) if allowed_mileage else 0.0
    saved = allowed_litres - consumed
    amount = saved * diesel_rate

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    def safe(v):
        return str(v or "").encode("ascii", "ignore").decode("ascii")

    styles = getSampleStyleSheet()
    GOLD = colors.HexColor("#FFC107")
    INK = colors.HexColor("#3E2723")
    GOLD_LT = colors.HexColor("#FFF9C4")
    AMBER = colors.HexColor("#B26A00")
    DARK = colors.HexColor("#222222")
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=13)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=16 * mm, leftMargin=15 * mm, rightMargin=15 * mm)
    W = doc.width
    el = _letterhead(company, W, mm)
    el.append(_title_bar("DIESEL MILEAGE SLIP - " + safe(month_label).upper(), W))
    el.append(Spacer(1, 8))

    grid = [
        ["Driver Name", safe(driver.get("name", "")), "Vehicle Number", safe(driver.get("vehicle", ""))],
        ["Designation", safe(driver.get("designation", "Driver")), "Period", safe(month_label)],
    ]
    gt = Table(grid, colWidths=[W * 0.18, W * 0.32, W * 0.18, W * 0.32])
    gt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (0, -1), GOLD_LT), ("BACKGROUND", (2, 0), (2, -1), GOLD_LT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0C060")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    el.append(gt)
    el.append(Spacer(1, 12))

    part = [
        ["PARTICULARS", "VALUE"],
        ["Approved KM", f"{approved_km:,.0f} km"],
        ["Diesel Consumed", f"{consumed:,.2f} L"],
        ["Mileage Achieved", f"{mileage:,.2f} km/L"],
        [f"Diesel Allowed (@ {allowed_mileage:g} km/L)", f"{allowed_litres:,.2f} L"],
        ["Diesel Saved (Diesel Left)", f"{saved:,.2f} L"],
        [f"Diesel Mileage Amount (@ Rs.{diesel_rate:g}/L)", "Rs. " + f"{amount:,.2f}"],
    ]
    pt = Table(part, colWidths=[W * 0.62, W * 0.38])
    pt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("BACKGROUND", (0, 0), (-1, 0), GOLD), ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -2), (-1, -1), GOLD_LT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0C060")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    el.append(pt)
    el.append(Spacer(1, 10))

    box = Table([["DIESEL MILEAGE AMOUNT PAYABLE", "Rs. " + f"{amount:,.2f}"],
                 [Paragraph("<b>In words:</b> " + safe(_amount_in_words(amount)),
                            ParagraphStyle("w", parent=small, textColor=DARK)), ""]],
                colWidths=[W * 0.62, W * 0.38])
    box.setStyle(TableStyle([
        ("SPAN", (0, 1), (-1, 1)),
        ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (1, 0), 12.5),
        ("TEXTCOLOR", (0, 0), (1, 0), AMBER), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BACKGROUND", (0, 0), (1, 0), GOLD_LT),
        ("BOX", (0, 0), (-1, -1), 1.2, GOLD),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 1), (-1, 1), 6), ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    el.append(box)
    if remarks:
        el.append(Spacer(1, 8))
        el.append(Paragraph("<b>Remarks:</b> " + safe(remarks), small))
    el.append(Spacer(1, 40))
    el.append(_signatures(W, "Driver's Signature", company.get("name", "")))
    el.append(Spacer(1, 10))
    el.append(Paragraph("This is a computer-generated slip.", ParagraphStyle(
        "foot", parent=styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#999999"), alignment=1)))

    doc.build(el)
    buf.seek(0)
    add_audit_log("CREATE", "DieselSlip", str(driver.get("id", "")),
                  f"Diesel mileage slip {month_label} for {driver.get('name', '')} (Rs.{amount:,.0f})", user.get("email", ""))
    fname = "Diesel_Slip_" + safe(driver.get("name", "driver")).replace(" ", "_") + "_" + str(data.get("month", "")) + ".pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})
