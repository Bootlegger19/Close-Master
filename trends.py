"""Trend data for the admin Trends tab.

build_trend() turns closing history into one value per item per period, ready to chart:
  level  - the item's count at the last closing in the period
  change - net change in count across the closings in the period
"""
from datetime import date, timedelta

from constants import business_date
from models import db, Count, InventorySession

GROUPS = {"day": "Day", "week": "Week", "month": "Month", "year": "Year"}
METRICS = {"level": "Stock level", "change": "Change since previous closing"}
DEFAULT_SPAN_DAYS = {"day": 29, "week": 83, "month": 364, "year": 1825}
MAX_PERIODS = 400    # most periods drawn on one chart


def bucket_start(day, group):
    """First day of the period that contains `day`."""
    if group == "week":
        return day - timedelta(days=day.weekday())   # weeks start on Monday
    if group == "month":
        return day.replace(day=1)
    if group == "year":
        return day.replace(month=1, day=1)
    return day


def next_bucket(start, group):
    if group == "week":
        return start + timedelta(days=7)
    if group == "month":
        return date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    if group == "year":
        return date(start.year + 1, 1, 1)
    return start + timedelta(days=1)


def bucket_label(start, group, with_year):
    if group == "year":
        return f"{start:%Y}"
    if group == "month":
        return f"{start:%b %Y}"
    text = f"{start:%b %d}" + (f" {start:%Y}" if with_year else "")
    return f"Wk of {text}" if group == "week" else text


def build_trend(bar_id, items, group, metric, start, end):
    """Return labels, one dataset per item, and the non-empty table rows."""
    item_ids = [item.id for item in items]
    rows = (db.session.query(InventorySession.completed_at, Count.item_id, Count.quantity)
            .join(Count, Count.session_id == InventorySession.id)
            .filter(InventorySession.bar_id == bar_id,
                    InventorySession.status == "completed",
                    Count.item_id.in_(item_ids))
            .order_by(InventorySession.completed_at, InventorySession.id)
            .all())

    # Walk the closings in time order. `last_seen` remembers each item's previous count
    # (even from before the range) so the first period's change is still correct.
    last_seen = {}
    cells = {}   # (item_id, period start) -> {"level": ..., "change": ...}
    for completed_at, item_id, quantity in rows:
        day = business_date(completed_at)
        before = last_seen.get(item_id)
        last_seen[item_id] = quantity
        if not start <= day <= end:
            continue
        cell = cells.setdefault((item_id, bucket_start(day, group)),
                                {"level": None, "change": None})
        cell["level"] = quantity                       # later closings overwrite earlier ones
        if before is not None:
            cell["change"] = (cell["change"] or 0) + (quantity - before)

    # One slot for every period in the range, so empty periods show as gaps.
    periods = []
    cursor, last = bucket_start(start, group), bucket_start(end, group)
    while cursor <= last:
        periods.append(cursor)
        cursor = next_bucket(cursor, group)
    trimmed = len(periods) > MAX_PERIODS
    if trimmed:
        periods = periods[-MAX_PERIODS:]

    datasets = [
        {"label": item.name,
         "data": [cells.get((item.id, period), {}).get(metric) for period in periods]}
        for item in items
    ]

    with_year = start.year != end.year
    labels = [bucket_label(period, group, with_year) for period in periods]
    table_rows = []
    for index, label in enumerate(labels):
        values = [dataset["data"][index] for dataset in datasets]
        if any(value is not None for value in values):
            table_rows.append({"label": label, "values": values})

    return {"labels": labels, "datasets": datasets, "rows": table_rows,
            "trimmed": trimmed, "has_data": bool(table_rows)}