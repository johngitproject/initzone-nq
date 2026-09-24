"""Data loader — parse NinjaTrader 8 1-min Last exports and build resampled 5-min.

Input format (per LISEZMOI.md):
  20250101 230100;21269;21282.75;21253.5;21261.25;393
  fields: datetime;open;high;low;close;volume  (no header, ; separator)
  Times are in CME Chicago time per spec (but strategy uses fixed Benin UTC+1 w/ DST shift).
  For Python we keep timestamps as naive and apply same DstAdjusted logic as C#.

Rollover:
  By default concatenates all provided files sorted by datetime, with single-contract-active
  rule: if overlapping dates, latest file wins per timestamp (dedup).
  Rollover helper `roll_at_rth_open_minus_8d` can be used externally; for simple backtest
  we just dedup by timestamp.

Resampling 5-min:
  Resamples to 5-min bars aligned to clock (00,05,...). Aggregates OHLCV: open=first, high=max,
  low=min, close=last, volume=sum.
  Also provides 1-min bars for profile accumulation (same as primary in NT8 OnBarUpdate).

DayKey logic:
  Mirrors MGIStructureContact: barOpenTime = Time[0] - period, dayKey = barOpenDate + (1 if TOD >= EffOvernightStart else 0)
  Used for profile resets and contact day counting.

"""
from __future__ import annotations
import pathlib
from datetime import datetime, timedelta, date, time
from typing import List, Tuple, Dict
import math

# We try to use pandas if available, fallback to pure python
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

DT_FMT = "%Y%m%d %H%M%S"

def parse_nt8_file(path: str | pathlib.Path) -> List[Tuple[datetime, float, float, float, float, float]]:
    """Parse one NT8 export file -> list of (dt, o,h,l,c,v). dt is naive."""
    p = pathlib.Path(path)
    out = []
    with p.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line=line.strip()
            if not line:
                continue
            try:
                dt_s, o_s, h_s, l_s, c_s, v_s = line.split(";")
                dt = datetime.strptime(dt_s, DT_FMT)
                o = float(o_s); h = float(h_s); l = float(l_s); c = float(c_s); v = float(v_s)
                out.append((dt, o, h, l, c, v))
            except Exception:
                continue
    return out

def load_and_merge(paths: List[str | pathlib.Path]) -> List[Tuple[datetime, float, float, float, float, float]]:
    """Load multiple files, sort by dt, dedup (last wins)."""
    all_bars = []
    for p in paths:
        all_bars.extend(parse_nt8_file(p))
    # sort
    all_bars.sort(key=lambda x: x[0])
    # dedup by dt: keep last occurrence
    dedup: Dict[datetime, Tuple[float,float,float,float,float]] = {}
    for dt,o,h,l,c,v in all_bars:
        dedup[dt] = (o,h,l,c,v)
    merged = [(dt, *dedup[dt]) for dt in sorted(dedup.keys())]
    return merged

def to_dataframe(bars: List[Tuple[datetime, float,float,float,float,float]]):
    if not HAS_PANDAS:
        raise ImportError("pandas required for dataframe conversion")
    import pandas as pd
    df = pd.DataFrame(bars, columns=["dt","open","high","low","close","volume"])
    df = df.set_index("dt").sort_index()
    return df

def resample_5min(df):
    """Resample 1-min df to 5-min OHLCV aligned to 00:00."""
    if not HAS_PANDAS:
        raise ImportError("pandas required")
    # pandas resample: label right? NT8 bars: open = Time - period. Resample close at e.g. 14:35
    # Using 5T with label right and closed right would align but easier: use 5min with origin start_day
    # We'll resample with rule '5T' and agg.
    # Ensure df index is datetime
    agg = {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    df5 = df.resample("5min", label="right", closed="right").agg(agg).dropna()
    # Drop bars where no original bars? already dropna covers
    return df5

def get_bar_open_time(dt_close: datetime, period_minutes: int = 1) -> datetime:
    """Mirror GetBarOpenTime(): for OnBarClose, Time[0] is close, open = close - period."""
    return dt_close - timedelta(minutes=period_minutes)

def get_bar_open_timedelta(dt_close: datetime, period_minutes: int = 1) -> timedelta:
    ot = get_bar_open_time(dt_close, period_minutes)
    return timedelta(hours=ot.hour, minutes=ot.minute, seconds=ot.second)

# Rollover helpers for NQ 2025 (from LISEZMOI: Fri before expiry minus 8 calendar days at RTH open)
# Expiries 2025: 21/03, 20/06, 19/09, 19/12
ROLLOVER_DATES_2025 = [
    (date(2025,3,21) - timedelta(days=8), "H5->M5"),  # ~2025-03-13
    (date(2025,6,20) - timedelta(days=8), "M5->U5"),  # ~2025-06-12
    (date(2025,9,19) - timedelta(days=8), "U5->Z5"),  # ~2025-09-11
    (date(2025,12,19) - timedelta(days=8), "Z5->H6"),
]

def filter_by_date(bars: List[Tuple[datetime,...]], start: datetime | date | None, end: datetime | date | None):
    if start is not None:
        if isinstance(start, date) and not isinstance(start, datetime):
            start = datetime.combine(start, time.min)
        bars = [b for b in bars if b[0] >= start]
    if end is not None:
        if isinstance(end, date) and not isinstance(end, datetime):
            end = datetime.combine(end, time.max)
        bars = [b for b in bars if b[0] <= end]
    return bars

def load_nq_2025_1min(data_dir: str | pathlib.Path = "donnees/market", suffix: str = "Last.txt") -> List[Tuple[datetime,float,float,float,float,float]]:
    """Convenience: load NQ 2025 1-min continuous for backtest."""
    p = pathlib.Path(data_dir)
    files = sorted(p.glob(f"NQ *.{suffix}")) + sorted(p.glob(f"NQ *{suffix}"))
    # also handle space names
    # explicit 2025 files
    wanted = ["NQ 03-25.Last.txt","NQ 06-25.Last.txt","NQ 09-25.Last.txt","NQ 12-25.Last.txt"]
    paths = []
    for w in wanted:
        fp = p / w
        if fp.exists():
            paths.append(fp)
    if not paths:
        # fallback glob
        paths = list(p.glob("NQ *.Last.txt"))
    return load_and_merge(paths)
