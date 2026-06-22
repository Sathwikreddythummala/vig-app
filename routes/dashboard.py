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
async def dashboard_stats(request: Request):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    expenses = get_all_records("Expenses")
    vehicles = get_all_records("Vehicles")
    drivers = get_all_records("Drivers")
    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().strftime("%Y-%m-01")
    total_today = sum(float(e.get("Amount", 0) or 0) for e in expenses if str(e.get("ExpenseDate", "")) == today)
    total_month = sum(float(e.get("Amount", 0) or 0) for e in expenses if str(e.get("ExpenseDate", "")) >= month_start)
    return {
        "total_today": total_today,
        "total_month": total_month,
        "total_vehicles": len(vehicles),
        "total_drivers": len([d for d in drivers if str(d.get("Status", "")).lower() == "active" and str(d.get("EmployeeType", "Driver")) == "Driver"]),
    }


@router.get("/api/dashboard/charts")
async def dashboard_charts(request: Request):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    expenses = get_all_records("Expenses")
    month_start = datetime.now().strftime("%Y-%m-01")
    monthly_expenses = [e for e in expenses if str(e.get("ExpenseDate", "")) >= month_start]
    vehicle_wise = defaultdict(float)
    category_wise = defaultdict(float)
    for e in monthly_expenses:
        vn = str(e.get("VehicleNumber", "")) or "Company"
        vehicle_wise[vn] += float(e.get("Amount", 0) or 0)
        category_wise[str(e.get("Category", "Other"))] += float(e.get("Amount", 0) or 0)
    monthly_trend = defaultdict(float)
    for e in expenses:
        d = str(e.get("ExpenseDate", ""))
        if len(d) >= 7:
            monthly_trend[d[:7]] += float(e.get("Amount", 0) or 0)
    sorted_months = sorted(monthly_trend.keys())[-12:]
    return {
        "vehicle_wise": {"labels": list(vehicle_wise.keys()), "values": list(vehicle_wise.values())},
        "category_wise": {"labels": list(category_wise.keys()), "values": list(category_wise.values())},
        "monthly_trend": {"labels": sorted_months, "values": [monthly_trend[m] for m in sorted_months]},
    }


@router.get("/api/dashboard/recent-expenses")
async def recent_expenses(request: Request):
    user = get_user(request)
    if not user:
        return {"error": "Unauthorized"}
    expenses = get_all_records("Expenses")
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
