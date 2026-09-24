"""Vendored subset of MGI Contact needed by mgi_initialzone.

Only ships what the InitialZone backtests import:
- data_loader (NT8 1-min Last parsing + merge/dedup)
- session_clock (US DST rules + dst_adjusted)

Full contact engine (volume_profile, structure_contact, contact_strategy,
backtest) is intentionally NOT included in this repo.
"""
from .session_clock import is_us_dst, dst_adjusted

__all__ = [
    "is_us_dst", "dst_adjusted",
]
