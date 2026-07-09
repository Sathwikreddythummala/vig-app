def filter_multi(records, field, param):
    """Filter records where `field` matches any of the comma-separated values in `param`.

    An empty/blank param means no filtering (all records pass).
    """
    vals = {v.strip() for v in (param or "").split(",") if v.strip()}
    if not vals:
        return records
    return [r for r in records if str(r.get(field, "")) in vals]
