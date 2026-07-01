from fastapi import APIRouter, Request
from services.sheets_service import get_all_records
from utils.templates import templates
from datetime import datetime, timedelta
from collections import defaultdict

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


@router.get("/api/dashboard/stats")
async def dashboard_stats(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    if not month:
        month = datetime.now().strftime("%Y-%m")
    expenses = get_all_records("Expenses")
    vehicles = get_all_records("Vehicles")
    drivers = get_all_records("Drivers")
    billing = get_all_records("Billing")
    receivables = get_all_records("Receivables")
    fuel = get_all_records("FuelEntries")
    purse = get_all_records("Purse")
    today = datetime.now().strftime("%Y-%m-%d")
    month_expenses = [e for e in expenses if str(e.get("ExpenseDate", ""))[:7] == month]
    total_expense = sum(float(e.get("Amount", 0) or 0) for e in month_expenses)
    total_today = sum(float(e.get("Amount", 0) or 0) for e in expenses if str(e.get("ExpenseDate", "")) == today)
    # Billing: filter by PaymentMonth (month when payment is expected/received)
    month_billing = [b for b in billing if (str(b.get("PaymentMonth", "")) or str(b.get("InvoiceDate", ""))[:7]) == month]
    total_billed = sum(float(b.get("TotalAmount", 0) or 0) for b in month_billing)
    # Received: sum receivables whose PaymentMonth matches selected month
    month_received = [r for r in receivables if (str(r.get("PaymentMonth", "")) or str(r.get("ReceiveDate", ""))[:7]) == month]
    total_received = sum(float(r.get("Amount", 0) or 0) for r in month_received)
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
        month = datetime.now().strftime("%Y-%m")
    expenses = get_all_records("Expenses")
    billing = get_all_records("Billing")
    monthly_expenses = [e for e in expenses if str(e.get("ExpenseDate", ""))[:7] == month]
    vehicle_wise = defaultdict(float)
    category_wise = defaultdict(float)
    for e in monthly_expenses:
        vn = str(e.get("VehicleNumber", "")) or "Company"
        vehicle_wise[vn] += float(e.get("Amount", 0) or 0)
        category_wise[str(e.get("Category", "Other"))] += float(e.get("Amount", 0) or 0)
    monthly_trend = defaultdict(float)
    billing_trend = defaultdict(float)
    for e in expenses:
        d = str(e.get("ExpenseDate", ""))
        if len(d) >= 7:
            monthly_trend[d[:7]] += float(e.get("Amount", 0) or 0)
    for b in billing:
        d = str(b.get("PaymentMonth", "")) or str(b.get("InvoiceDate", ""))[:7]
        if len(d) >= 7:
            billing_trend[d[:7]] += float(b.get("TotalAmount", 0) or 0)
    all_months = sorted(set(list(monthly_trend.keys()) + list(billing_trend.keys())))[-12:]
    return {
        "vehicle_wise": {"labels": list(vehicle_wise.keys()), "values": list(vehicle_wise.values())},
        "category_wise": {"labels": list(category_wise.keys()), "values": list(category_wise.values())},
        "monthly_trend": {
            "labels": all_months,
            "expense": [monthly_trend.get(m, 0) for m in all_months],
            "billing": [billing_trend.get(m, 0) for m in all_months],
        },
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
    today = datetime.now().date()
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
    alerts.sort(key=lambda x: 0 if x["type"] == "danger" else 1 if x["type"] == "warning" else 2)
    return {"alerts": alerts}
