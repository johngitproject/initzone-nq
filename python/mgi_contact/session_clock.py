"""MGI Session Clock — mirror of MGIStructureContact.MgiSessionClock / MGIContactStrategy local DST.

Convention (from C# passation-mgi.md #11):
- Hours configured are REFERENCE ETE (DST, e.g. 14:30 Benin = 09:30 ET summer).
- In winter (EST) US open falls 1h later in fixed local time => +1h auto if adjust=True.
- DST US rules 2007+: 2nd Sunday March (inclusive) -> 1st Sunday November (exclusive).
- Exact to the day (transition at 2am Sunday, before any session including Sunday night futures open).
- Charts assumed fixed offset (e.g. Benin UTC+1, no DST): Time[0].TimeOfDay comparable to TimeSpan.

Ported from:
  MGIStructureContact.cs:81  MgiSessionClock.IsUsDst
  MGIStructureContact.cs:96  DstAdjusted
  MGIContactStrategy.cs:34   IsUsDstLocal
  MGIContactStrategy.cs:42   DstAdjustedLocal
"""
from datetime import date, datetime, time, timedelta

def _second_sunday_of_march(year: int) -> date:
    d = date(year, 3, 1)
    # days to next Sunday
    days_ahead = (6 - d.weekday()) % 7  # Monday=0 -> Sunday=6
    first_sunday = d + timedelta(days=days_ahead)
    second_sunday = first_sunday + timedelta(days=7)
    return second_sunday

def _first_sunday_of_november(year: int) -> date:
    d = date(year, 11, 1)
    days_ahead = (6 - d.weekday()) % 7
    first_sunday = d + timedelta(days=days_ahead)
    return first_sunday

def is_us_dst(d: date | datetime) -> bool:
    """Return True if date is in US DST (2nd Sun Mar inclusive -> 1st Sun Nov exclusive)."""
    if isinstance(d, datetime):
        d = d.date()
    year = d.year
    dst_start = _second_sunday_of_march(year)
    dst_end = _first_sunday_of_november(year)
    return d >= dst_start and d < dst_end

def dst_adjusted(base_summer: timedelta, bar_date: date | datetime, adjust: bool) -> timedelta:
    """Effective time: base (summer reference) +1h in winter if adjust=True."""
    if not adjust:
        return base_summer
    # bar_date may be datetime
    check = bar_date.date() if isinstance(bar_date, datetime) else bar_date
    if is_us_dst(check):
        return base_summer
    return base_summer + timedelta(hours=1)

# helpers for TimeSpan-like timedelta usage
def time_to_timedelta(t: time) -> timedelta:
    return timedelta(hours=t.hour, minutes=t.minute, seconds=t.second, microseconds=t.microsecond)

def timedelta_to_time(td: timedelta) -> time:
    total = int(td.total_seconds())
    h = (total // 3600) % 24
    m = (total % 3600) // 60
    s = total % 60
    return time(hour=h, minute=m, second=s)

def eff_rth_open(base_open: timedelta, bar_date, adjust: bool) -> timedelta:
    return dst_adjusted(base_open, bar_date, adjust)

def eff_rth_close(base_close: timedelta, bar_date, adjust: bool) -> timedelta:
    return dst_adjusted(base_close, bar_date, adjust)

def eff_overnight_start(base_close: timedelta, bar_date, adjust: bool) -> timedelta:
    return eff_rth_close(base_close, bar_date, adjust)
