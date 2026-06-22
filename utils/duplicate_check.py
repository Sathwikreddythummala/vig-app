from services.sheets_service import get_all_records


def is_duplicate(sheet_name: str, check_fields: dict) -> bool:
    records = get_all_records(sheet_name)
    for record in records:
        match = True
        for field, value in check_fields.items():
            if str(record.get(field, "")).strip().lower() != str(value).strip().lower():
                match = False
                break
        if match:
            return True
    return False
