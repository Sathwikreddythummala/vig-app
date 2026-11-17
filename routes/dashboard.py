from fastapi import APIRouter, Request
from services.sheets_service import get_all_records
from utils.templates import templates
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

IST = ZoneInfo("Asia/Kolkata")

def now_ist():
    return datetime.now(IST)

router = APIRouter(tags=["dashboard"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("/")
async def dashboard(request: Request):
    user = get_user(request)
    if not user:
        return templates.TemplateResponse(request=request, name="login.html")
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"user": user})


@router.get("/test")
async def test_page(request: Request):
    return templates.TemplateResponse(request=request, name="test.html")


@router.get("/api/search")
async def global_search(request: Request, q: str = ""):
    """Top-bar global search across vehicles, drivers, vendors and invoices."""
    user = get_user(request)
    if not user:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Unauthorized"}, 401)
    term = str(q).strip().lower()
    results = []
    if len(term) < 2:
        return {"results": results}

    def match(*vals):
        return any(term in str(v or "").lower() for v in vals)

    # Vehicles
    for v in get_all_records("Vehicles"):
        if match(v.get("VehicleNumber"), v.get("DefaultDriver"), v.get("DefaultVendor")):
            results.append({"type": "Vehicle", "icon": "bi-truck",
                            "label": v.get("VehicleNumber", ""),
                            "sub": v.get("DefaultDriver", "") or v.get("VehicleType", ""),
                            "url": f"/vehicles/details/{v.get('VehicleID','')}"})
    # Drivers / employees
    for d in get_all_records("Drivers"):
        if match(d.get("DriverName"), d.get("MobileNumber"), d.get("AssignedVehicle")):
            results.append({"type": "Employee", "icon": "bi-person",
                            "label": d.get("DriverName", ""),
                            "sub": (d.get("AssignedVehicle", "") or d.get("EmployeeType", "")),
                            "url": "/drivers"})
    # Vendors
    for ve in get_all_records("Vendors"):
        if match(ve.get("VendorName"), ve.get("ContactPerson"), ve.get("GSTNumber")):
            results.append({"type": "Vendor", "icon": "bi-building",
                            "label": ve.get("VendorName", ""),
                            "sub": ve.get("ContactPerson", ""),
                            "url": "/vendors"})
    # Invoices (distinct invoice numbers)
    seen_inv = set()
    for b in get_all_records("Billing"):
        inv = str(b.get("InvoiceNumber", "")).strip()
        key = inv.upper()
        if inv and key not in seen_inv and (term in key.lower() or match(b.get("VendorName"))):
            seen_inv.add(key)
            results.append({"type": "Invoice", "icon": "bi-receipt",
                            "label": inv,
                            "sub": b.get("VendorName", ""),
                            "url": "/billing"})
    # Cap and prioritise exact-ish matches first
    results.sort(key=lambda r: (0 if str(r["label"]).lower().startswith(term) else 1, str(r["label"]).lower()))
    return {"results": results[:25]}


@router.get("/api/dashboard/stats")
async def dashboard_stats(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    if not month:
        month = now_ist().strftime("%Y-%m")
    expenses = get_all_records("Expenses")
    vehicles = get_all_records("Vehicles")
    drivers = get_all_records("Drivers")
    billing = get_all_records("Billing")
    fuel = get_all_records("FuelEntries")
    purse = get_all_records("Purse")
    today = now_ist().strftime("%Y-%m-%d")
    month_expenses = [e for e in expenses if str(e.get("ExpenseDate", ""))[:7] == month]
    total_expense = sum(float(e.get("Amount", 0) or 0) for e in month_expenses)
    total_today = sum(float(e.get("Amount", 0) or 0) for e in expenses if str(e.get("ExpenseDate", "")) == today)
    # Billing: filter by PaymentMonth — amounts based on bills attributed to this month
    month_billing = [b for b in billing if (str(b.get("PaymentMonth", "")) or str(b.get("InvoiceDate", ""))[:7]) == month]
    total_billed = sum(float(b.get("TotalAmount", 0) or 0) for b in month_billing)
    total_received = sum(float(b.get("PaidAmount", 0) or 0) for b in month_billing)
    total_outstanding = sum(float(b.get("BalanceAmount", 0) or 0) for b in month_billing)
    month_fuel = [f for f in fuel if str(f.get("EntryDate", ""))[:7] == month]
    total_fuel_litres = sum(float(f.get("Litres", 0) or 0) for f in month_fuel)
    total_fuel_amount = sum(float(f.get("Amount", 0) or 0) for f in month_fuel)
    active_drivers = len([d for d in drivers if str(d.get("Status", "")).lower() == "active" and str(d.get("EmployeeType", "Driver")) == "Driver"])
    active_vehicles = len([v for v in vehicles if str(v.get("VehicleStatus", "")).lower() == "active"])
    # Purse: upcoming income and expenses from today onwards
    upcoming_purse = [p for p in purse if str(p.get("Date", "")) >= today]
    purse_income = sum(float(p.get("Amount", 0) or 0) for p in upcoming_purse if str(p.get("Type", "")).lower() == "income")
    purse_expense = sum(float(p.get("Amount", 0) or 0) for p in upcoming_purse if str(p.get("Type", "")).lower() == "expense")
    return {
        "month": month,
        "total_today": total_today,
        "total_expense": total_expense,
        "total_billed": total_billed,
        "total_received": total_received,
        "total_outstanding": total_outstanding,
        "total_fuel_litres": total_fuel_litres,
        "total_fuel_amount": total_fuel_amount,
        "total_vehicles": len(vehicles),
        "active_vehicles": active_vehicles,
        "total_drivers": active_drivers,
        "purse_income": purse_income,
        "purse_expense": purse_expense,
        "purse_upcoming_count": len(upcoming_purse),
    }


@router.get("/api/dashboard/charts")
async def dashboard_charts(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    if not month:
        month = now_ist().strftime("%Y-%m")
    expenses = get_all_records("Expenses")
    monthly_expenses = [e for e in expenses if str(e.get("ExpenseDate", ""))[:7] == month]
    vehicle_wise = defaultdict(float)
    category_wise = defaultdict(float)
    for e in monthly_expenses:
        vn = str(e.get("VehicleNumber", "")) or "Company"
        vehicle_wise[vn] += float(e.get("Amount", 0) or 0)
        category_wise[str(e.get("Category", "Other"))] += float(e.get("Amount", 0) or 0)
    return {
        "vehicle_wise": {"labels": list(vehicle_wise.keys()), "values": list(vehicle_wise.values())},
        "category_wise": {"labels": list(category_wise.keys()), "values": list(category_wise.values())},
    }


@router.get("/api/dashboard/recent-expenses")
async def recent_expenses(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    expenses = get_all_records("Expenses")
    if month:
        expenses = [e for e in expenses if str(e.get("ExpenseDate", ""))[:7] == month]
    expenses.sort(key=lambda x: str(x.get("ExpenseDate", "")), reverse=True)
    return {"expenses": expenses[:20]}


@router.get("/api/dashboard/alerts")
async def dashboard_alerts(request: Request):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    alerts = []
    today = now_ist().date()
    threshold = today + timedelta(days=30)
    vehicles = get_all_records("Vehicles")
    drivers = get_all_records("Drivers")
    date_fields = [
        ("RCExpiry", "RC"),
        ("InsuranceExpiryDate", "Insurance"),
        ("PermitExpiryDate", "Permit"),
        ("FitnessExpiryDate", "Fitness"),
        ("PUCExpiryDate", "PUC"),
    ]
    for v in vehicles:
        vnum = v.get("VehicleNumber", "")
        for field, label in date_fields:
            val = str(v.get(field, "")).strip()
            if val:
                try:
                    exp = datetime.strptime(val, "%Y-%m-%d").date()
                    if exp <= threshold:
                        status = "expired" if exp < today else "expiring"
                        days = (exp - today).days
                        alerts.append({
                            "type": "danger" if exp < today else "warning",
                            "message": f"{vnum} - {label} {'expired' if exp < today else f'expiring in {days} days'} ({val})",
                            "entity": "Vehicle",
                            "entity_id": vnum,
                        })
                except ValueError:
                    pass
        if str(v.get("LoanAvailable", "")).lower() == "yes":
            emi_date = str(v.get("EMIDate", "")).strip()
            if emi_date:
                try:
                    day = int(emi_date)
                    emi_d = today.replace(day=day)
                    diff = (emi_d - today).days
                    if 0 <= diff <= 7:
                        alerts.append({
                            "type": "info",
                            "message": f"{vnum} - EMI due in {diff} days (Day {day})",
                            "entity": "Vehicle",
                            "entity_id": vnum,
                        })
                except (ValueError, TypeError):
                    pass
    for d in drivers:
        dname = d.get("DriverName", "")
        lic_exp = str(d.get("LicenseExpiryDate", "")).strip()
        if lic_exp:
            try:
                exp = datetime.strptime(lic_exp, "%Y-%m-%d").date()
                if exp <= threshold:
                    days = (exp - today).days
                    alerts.append({
                        "type": "danger" if exp < today else "warning",
                        "message": f"{dname} - License {'expired' if exp < today else f'expiring in {days} days'} ({lic_exp})",
                        "entity": "Driver",
                        "entity_id": dname,
                    })
            except ValueError:
                pass
    # Strictly no vehicle should be without a driver — flag every active vehicle with none.
    # A vehicle "has a driver" if DefaultDriver is set OR it has an open assignment period.
    has_open_assignment = set()
    for a in get_all_records("VehicleAssignments"):
        if not str(a.get("EndDate", "")).strip() and str(a.get("DriverName", "")).strip():
            has_open_assignment.add(str(a.get("VehicleNumber", "")).strip())
    no_driver = []
    for v in vehicles:
        if str(v.get("VehicleStatus", "Active")).strip().lower() in ("inactive", "sold", "scrapped"):
            continue
        vn = str(v.get("VehicleNumber", "")).strip()
        if not str(v.get("DefaultDriver", "")).strip() and vn not in has_open_assignment:
            no_driver.append({"vehicle": vn, "id": v.get("VehicleID", "")})
            alerts.append({"type": "danger", "message": f"{vn} - NO DRIVER assigned",
                           "entity": "Vehicle", "entity_id": vn})
    # Driver rules:
    #  - every driver needs an ENTRY (joining) date,
    #  - an INACTIVE (left) driver needs an EXIT date,
    #  - an ACTIVE driver with NO vehicle must be made inactive (or given a vehicle).
    from services.auth_service import is_driver_record
    driven = set()
    for a in get_all_records("VehicleAssignments"):
        if not str(a.get("EndDate", "")).strip() and str(a.get("DriverName", "")).strip():
            driven.add(str(a.get("DriverName", "")).strip())
    for v in vehicles:
        dd = str(v.get("DefaultDriver", "")).strip()
        if dd:
            driven.add(dd)
    driver_issues = []
    for d in drivers:
        if not is_driver_record(d):
            continue
        name = str(d.get("DriverName", "")).strip()
        if not name:
            continue
        inactive = str(d.get("Status", "Active")).strip().lower() == "inactive"
        has_vehicle = bool(str(d.get("AssignedVehicle", "")).strip()) or name in driven
        issues = []
        if not str(d.get("JoiningDate", "")).strip():
            issues.append("no entry date")
        if inactive and not str(d.get("ExitDate", "")).strip():
            issues.append("no exit date")
        if not inactive and not has_vehicle:
            issues.append("active but no vehicle - assign one or mark inactive")
        if issues:
            driver_issues.append({"name": name, "id": d.get("DriverID", ""), "issue": ", ".join(issues)})
            alerts.append({"type": "danger", "message": f"{name} - {', '.join(issues)}",
                           "entity": "Driver", "entity_id": name})
    alerts.sort(key=lambda x: 0 if x["type"] == "danger" else 1 if x["type"] == "warning" else 2)
    return {"alerts": alerts, "vehicles_without_driver": no_driver,
            "driver_issues": driver_issues}
