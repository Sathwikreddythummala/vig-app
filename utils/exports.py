"""Helpers for building spreadsheet/PDF exports from the text-based DB records."""
import pandas as pd


def to_numeric_df(records, numeric_cols):
    """Build a DataFrame from records, coercing the given columns to real numbers
    so Excel treats them as amounts (right-aligned, summable) rather than text."""
    df = pd.DataFrame(records)
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
