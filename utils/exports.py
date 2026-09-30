"""Helpers for building spreadsheet/PDF exports from the text-based DB records."""
import re
from calendar import month_name

import pandas as pd


def _slug(v, maxlen=30):
    s = re.sub(r"[^A-Za-z0-9]+", "", str(v or "")).lower()
    return s[:maxlen]


def filtered_filename(base, *, month="", date_from="", date_to="", vehicle="",
                      category="", subcategory="", paid_by="", search="", extra=None):
    """Compose a download filename slug from the applied filters + a base noun.

    The slug names what was filtered and what kind of download it is, e.g.
        filtered_filename('expenses', month='2026-08', vehicle='TG08U3393', subcategory='Diesel')
        -> 'august_3393_diesel_expenses'
    The caller appends the extension (.xlsx / .pdf). Only non-empty filters
    appear; with no filters the slug is 'all_<base>'.
    """
    parts = []
    # Month -> month name (august); otherwise fall back to a date range.
    if month and "-" in month:
        try:
            parts.append(month_name[int(month.split("-")[1])].lower())
        except Exception:
            parts.append(_slug(month))
    elif month:
        parts.append(_slug(month))
    else:
        if date_from:
            parts.append("from" + _slug(date_from))
        if date_to:
            parts.append("to" + _slug(date_to))
    # Vehicle -> last 4 characters of the plate (TG08U3393 -> 3393).
    if vehicle:
        vs = re.sub(r"[^A-Za-z0-9]", "", str(vehicle))
        parts.append((vs[-4:] if len(vs) > 4 else vs).lower())
    if paid_by:
        parts.append(_slug(paid_by))
    # Subcategory implies its category, so prefer the most specific one.
    if subcategory:
        parts.append(_slug(subcategory))
    elif category:
        parts.append(_slug(category))
    if search:
        parts.append("search" + _slug(search))
    for x in (extra or []):
        s = _slug(x)
        if s:
            parts.append(s)
    parts = [p for p in parts if p] or ["all"]
    parts.append(_slug(base))
    return "_".join(parts)


def to_numeric_df(records, numeric_cols):
    """Build a DataFrame from records, coercing the given columns to real numbers
    so Excel treats them as amounts (right-aligned, summable) rather than text."""
    df = pd.DataFrame(records)
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
