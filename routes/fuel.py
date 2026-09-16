from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
import pandas as pd
import io
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, today_str, add_audit_log,
)
from utils.templates import templates
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
_IST = ZoneInfo("Asia/Kolkata")

router = APIRouter(prefix="/fuel", tags=["fuel"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def fuel_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="fuel.html", context={"user": user})


def _is_admin(user) -> bool:
    return bool(user and str(user.get("role", "")) == "admin")


def _km_run_map(all_records: list[dict]) -> dict:
    """FuelID -> distance run since the previous odometer reading for the SAME vehicle
    (this Kilometre minus the previous one, chronologically). Blank when it can't be
    computed (first reading, missing reading, or an odometer that went backwards)."""
    by_vehicle = defaultdict(list)
    for r in all_records:
        try:
            kmv = float(str(r.get("Kilometre", "")).strip())
        except (ValueError, TypeError):
            kmv = None
        if kmv is None or kmv <= 0:
            continue
        by_vehicle[str(r.get("VehicleNumber", "")).strip()].append((r, kmv))
    result = {}
    for _vn, items in by_vehicle.items():
        items.sort(key=lambda t: (str(t[0].get("EntryDate", "")), str(t[0].get("CreatedDate", ""))))
        prev = None
        for r, kmv in items:
            if prev is not None and kmv >= prev:
                result[r.get("FuelID", "")] = round(kmv - prev)
            prev = kmv
    return result


@router.get("/api/list")
async def list_fuel(
    request: Request,
    month: str = "",
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    driver: str = "",
    fuel_type: str = "",
    payment_status: str = "",
    page: int = 1,
    per_page: int = 25,
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    all_records = get_all_records("FuelEntries")
    is_admin = _is_admin(user)
    km_map = _km_run_map(all_records) if is_admin else {}
    records = all_records
    if month:
        records = [r for r in records if str(r.get("EntryDate", ""))[:7] == month]
    if date_from:
        records = [r for r in records if str(r.get("EntryDate", "")) >= date_from]
    if date_to:
        records = [r for r in records if str(r.get("EntryDate", "")) <= date_to]
    if payment_status:
        records = [r for r in records if (str(r.get("PaymentStatus", "")).strip() or "Paid") == payment_status]
    from utils.filters import filter_multi
    records = filter_multi(records, "VehicleNumber", vehicle)
    records = filter_multi(records, "DriverName", driver)
    records = filter_multi(records, "FuelType", fuel_type)
    records.sort(key=lambda x: str(x.get("EntryDate", "")), reverse=True)
    total = len(records)
    total_amount = sum(float(r.get("Amount", 0) or 0) for r in records)
    total_litres = sum(float(r.get("Litres", 0) or 0) for r in records)
    start = (page - 1) * per_page
    paginated = records[start:start + per_page]
    if is_admin:
        paginated = [{**e, "KmRun": km_map.get(e.get("FuelID", ""), "")} for e in paginated]
    return {
        "entries": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 1,
        "total_amount": total_amount,
        "total_litres": total_litres,
        "show_km_run": is_admin,
    }


@router.get("/api/stats")
async def fuel_stats(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("FuelEntries")
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    month_start = datetime.now(_IST).strftime("%Y-%m-01")
    today_records = [r for r in records if str(r.get("EntryDate", "")) == today]
    month_records = [r for r in records if str(r.get("EntryDate", "")) >= month_start]
    total_today = sum(float(r.get("Amount", 0) or 0) for r in today_records)
    total_month = sum(float(r.get("Amount", 0) or 0) for r in month_records)
    month_litres = sum(float(r.get("Litres", 0) or 0) for r in month_records)
    vehicle_wise = defaultdict(float)
    driver_wise = defaultdict(float)
    for r in month_records:
        vehicle_wise[str(r.get("VehicleNumber", "")) or "Unknown"] += float(r.get("Amount", 0) or 0)
        driver_wise[str(r.get("DriverName", "")) or "Unknown"] += float(r.get("Amount", 0) or 0)
    monthly_trend = defaultdict(float)
    for r in records:
        d = str(r.get("EntryDate", ""))
        if len(d) >= 7:
            monthly_trend[d[:7]] += float(r.get("Amount", 0) or 0)
    sorted_months = sorted(monthly_trend.keys())[-12:]
    credit_outstanding = sum(
        float(r.get("Amount", 0) or 0) for r in records
        if str(r.get("PaymentStatus", "")).strip() == "Unpaid"
    )
    return {
        "total_today": total_today,
        "total_month": total_month,
        "month_litres": month_litres,
        "month_entries": len(month_records),
        "credit_outstanding": credit_outstanding,
        "vehicle_wise": {"labels": list(vehicle_wise.keys()), "values": list(vehicle_wise.values())},
        "driver_wise": {"labels": list(driver_wise.keys()), "values": list(driver_wise.values())},
        "monthly_trend": {"labels": sorted_months, "values": [monthly_trend[m] for m in sorted_months]},
    }


def _filtered_fuel(date_from: str, date_to: str, vehicle: str, driver: str, fuel_type: str, month: str = "") -> list[dict]:
    records = get_all_records("FuelEntries")
    if month:
        records = [r for r in records if str(r.get("EntryDate", ""))[:7] == month]
    if date_from:
        records = [r for r in records if str(r.get("EntryDate", "")) >= date_from]
    if date_to:
        records = [r for r in records if str(r.get("EntryDate", "")) <= date_to]
    from utils.filters import filter_multi
    records = filter_multi(records, "VehicleNumber", vehicle)
    records = filter_multi(records, "DriverName", driver)
    records = filter_multi(records, "FuelType", fuel_type)
    records.sort(key=lambda x: str(x.get("EntryDate", "")), reverse=True)
    return records


@router.get("/api/export/excel")
async def export_excel(
    request: Request,
    month: str = "",
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    driver: str = "",
    fuel_type: str = "",
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = _filtered_fuel(date_from, date_to, vehicle, driver, fuel_type, month)
    num_cols = ["Litres", "Amount", "Kilometre"]
    if _is_admin(user):
        km_map = _km_run_map(get_all_records("FuelEntries"))
        records = [{**r, "Km Run": km_map.get(r.get("FuelID", ""), "")} for r in records]
        num_cols.append("Km Run")
    from utils.exports import to_numeric_df
    df = to_numeric_df(records, num_cols)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=fuel_entries.xlsx"},
    )


@router.get("/api/export/pdf")
async def export_pdf(
    request: Request,
    month: str = "",
    date_from: str = "",
    date_to: str = "",
    vehicle: str = "",
    driver: str = "",
    fuel_type: str = "",
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    records = _filtered_fuel(date_from, date_to, vehicle, driver, fuel_type, month)

    def safe(v, limit=30):
        return str(v or "").encode("ascii", "ignore").decode("ascii")[:limit]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph("Vigneshwara Enterprises - Fuel Report", styles["Title"]), Spacer(1, 20)]
    is_admin = _is_admin(user)
    km_map = _km_run_map(get_all_records("FuelEntries")) if is_admin else {}
    header = ["Date", "Vehicle", "Driver", "Fuel Type", "Litres", "Amount", "KM Reading"] \
        + (["Km Run"] if is_admin else []) + ["Station", "Mode", "Status"]
    data = [header]
    total_amount = 0.0
    total_litres = 0.0
    for r in records:
        amt = float(r.get("Amount", 0) or 0)
        litres = float(r.get("Litres", 0) or 0)
        total_amount += amt
        total_litres += litres
        row = [
            safe(r.get("EntryDate")),
            safe(r.get("VehicleNumber")),
            safe(r.get("DriverName")),
            safe(r.get("FuelType")),
            f"{litres:,.2f}" if litres else "",
            f"Rs.{amt:,.0f}",
            safe(r.get("Kilometre")),
        ]
        if is_admin:
            kmr = km_map.get(r.get("FuelID", ""), "")
            row.append(f"{int(kmr):,}" if kmr != "" else "")
        row += [
            safe(r.get("FuelStation")),
            safe(r.get("PaymentMode")),
            str(r.get("PaymentStatus", "")).strip() or "Paid",
        ]
        data.append(row)
    totals_row = ["", "", "", "Total", f"{total_litres:,.2f}", f"Rs.{total_amount:,.0f}", ""]
    if is_admin:
        totals_row.append("")
    totals_row += ["", "", ""]
    data.append(totals_row)
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
        headers={"Content-Disposition": "attachment; filename=fuel_entries.pdf"},
    )


@router.get("/api/credit")
async def fuel_credit(request: Request):
    """Unpaid (credit) fuel grouped by fuel station."""
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    records = get_all_records("FuelEntries")
    unpaid = [r for r in records if str(r.get("PaymentStatus", "")).strip() == "Unpaid"]
    unpaid.sort(key=lambda x: str(x.get("EntryDate", "")), reverse=True)
    stations = defaultdict(lambda: {"entries": [], "total": 0.0, "litres": 0.0, "count": 0})
    for r in unpaid:
        st = str(r.get("FuelStation", "")).strip() or "Unknown Station"
        amt = float(r.get("Amount", 0) or 0)
        grp = stations[st]
        grp["entries"].append(r)
        grp["total"] += amt
        grp["litres"] += float(r.get("Litres", 0) or 0)
        grp["count"] += 1
    station_list = [
        {"station": st, "total": d["total"], "litres": d["litres"],
         "count": d["count"], "entries": d["entries"]}
        for st, d in stations.items()
    ]
    station_list.sort(key=lambda x: x["total"], reverse=True)
    return {
        "stations": station_list,
        "total_outstanding": sum(s["total"] for s in station_list),
        "total_entries": len(unpaid),
    }


@router.post("/api/settle")
async def settle_fuel(request: Request):
    """Mark fuel entries Paid. Accepts {fuel_ids: [...]} or {station: name}."""
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    fuel_ids = data.get("fuel_ids") or []
    station = str(data.get("station", "")).strip()
    pay_date = data.get("PaidDate", "") or today_str()
    from services.sheets_service import build_row
    if station and not fuel_ids:
        records = get_all_records("FuelEntries")
        fuel_ids = [
            r.get("FuelID") for r in records
            if str(r.get("PaymentStatus", "")).strip() == "Unpaid"
            and (str(r.get("FuelStation", "")).strip() or "Unknown Station") == station
        ]
    settled = 0
    for fid in fuel_ids:
        result = find_row_by_id("FuelEntries", fid)
        if not result:
            continue
        row_num, existing = result
        if str(existing.get("PaymentStatus", "")).strip() != "Unpaid":
            continue
        vals = {**existing, "PaymentStatus": "Paid", "PaidDate": pay_date}
        update_row("FuelEntries", row_num, build_row("FuelEntries", vals))
        settled += 1
    add_audit_log("UPDATE", "FuelEntries", station or ",".join(fuel_ids[:3]),
                  f"Settled {settled} credit fuel entries", user["email"])
    return {"success": True, "settled": settled}


@router.post("/api/add")
async def add_fuel(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    from utils.duplicate_check import is_duplicate
    if is_duplicate("FuelEntries", {
        "EntryDate": data.get("EntryDate", ""),
        "VehicleNumber": data.get("VehicleNumber", ""),
        "DriverName": data.get("DriverName", ""),
        "FuelType": data.get("FuelType", "Diesel"),
        "Litres": data.get("Litres", ""),
        "Amount": data.get("Amount", 0),
        "Kilometre": data.get("Kilometre", ""),
    }):
        return JSONResponse({"error": "Duplicate entry already exists"}, 400)
    fid = gen_id("FUEL")
    from services.sheets_service import build_row
    status = (data.get("PaymentStatus") or "Paid").strip() or "Paid"
    paid_date = data.get("PaidDate", "") if status == "Paid" else ""
    vals = {**data, "FuelID": fid, "FuelType": data.get("FuelType", "Diesel"),
            "PaymentMode": data.get("PaymentMode", "Cash"),
            "PaymentStatus": status, "PaidDate": paid_date, "CreatedDate": now_str()}
    row = build_row("FuelEntries", vals)
    append_row("FuelEntries", row)
    add_audit_log("CREATE", "FuelEntries", fid, f"Fuel ₹{data.get('Amount',0)} for {data.get('VehicleNumber','')}", user["email"])
    return {"success": True, "fuel_id": fid}


@router.put("/api/{fuel_id}")
async def update_fuel(request: Request, fuel_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("FuelEntries", fuel_id)
    if not result:
        return JSONResponse({"error": "Entry not found"}, 404)
    row_num, existing = result
    from services.sheets_service import build_row
    status = (data.get("PaymentStatus") or existing.get("PaymentStatus") or "Paid").strip() or "Paid"
    if status == "Paid":
        paid_date = data.get("PaidDate", "") or existing.get("PaidDate", "") or today_str()
    else:
        paid_date = ""
    vals = {**existing, **data, "FuelID": fuel_id, "FuelType": data.get("FuelType", "Diesel"),
            "PaymentMode": data.get("PaymentMode", "Cash"),
            "PaymentStatus": status, "PaidDate": paid_date,
            "CreatedDate": existing.get("CreatedDate", now_str())}
    row = build_row("FuelEntries", vals)
    update_row("FuelEntries", row_num, row)
    add_audit_log("UPDATE", "FuelEntries", fuel_id, f"Fuel entry updated ₹{data.get('Amount',0)}", user["email"])
    return {"success": True}



@router.delete("/api/{fuel_id}")
async def delete_fuel(request: Request, fuel_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("FuelEntries", fuel_id)
    if not result:
        return JSONResponse({"error": "Entry not found"}, 404)
    row_num, record = result
    delete_row("FuelEntries", row_num)
    add_audit_log("DELETE", "FuelEntries", fuel_id, f"Fuel ₹{record.get('Amount',0)} deleted", user["email"])
    return {"success": True}
