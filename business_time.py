"""The business clock. Ogera works in India, so "today" and date ranges are IST calendar days (UTC+5:30).

The server may run in UTC (for example on Render), so never ask the machine for its local date.
"""
import datetime

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)


def ist_now() -> datetime.datetime:
    """The current IST wall-clock time as a naive datetime, the same form follow-up due times are stored in."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + IST_OFFSET


def today_ist() -> datetime.date:
    return ist_now().date()


def ist_day_start_utc(day: datetime.date) -> datetime.datetime:
    """Midnight IST of `day`, as the naive UTC value that created_at and changed_at store."""
    return datetime.datetime.combine(day, datetime.time.min) - IST_OFFSET


def ist_day_bounds(day: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """Return the UTC start and exclusive end for one IST calendar day."""
    start = ist_day_start_utc(day)
    return start, start + datetime.timedelta(days=1)
