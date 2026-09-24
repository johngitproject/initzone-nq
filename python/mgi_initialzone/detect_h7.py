"""Detection H7-VWAPx c30-tp1.0 adaptee au moteur DCA — variante H7-DCA-OR5.

Reprend EXACTEMENT les primitives du fige (`scripts/backtest_ssrn_v2.py` :
`load_market`, `rth_bars`, `vwap_rth`, `OR_N=5`, `MIN_RTH_BARS=300`, rollover J-8)
pour que les signaux soient identiques au backtest fige
(`donnees/backtest/ssrn-v2-3y/STRATEGIE_H7_VWAPX_FIGEE.md`).

Mapping H7 -> ZoneSignal (decisions validees) :
- 1 zone max par jour RTH valide, uniquement si reclaim close vs VWAP
  apres la 30e barre (`i>=30`, cf `run_market` H7-VWAPx).
- VAL = OR5_L, VAH = OR5_H du jour (OR_N=5 premieres barres RTH).
- atr_ref = ORR = OR5_H - OR5_L (TP = avg +/- ORR, pas des renforts 1xORR,
  sizing 1 %/ORR).
- bullish = sens du reclaim (+1 long / -1 short).
- trend_dt = datetime du reclaim, entry_px = close du reclaim (entree market,
  comme `paper_h7.py`) — consomme par `dca_atr --entry-mode h7`.
- Reclaim sur la DERNIERE barre RTH : has_zone=False (aucune gestion possible
  avant le flat EOD) ; jour comptabilise dans `skipped_last_bar`.

Le fige n'est ni modifie ni recalcule ici : cette variante est en OBSERVATION
(meme statut que `c40-tp0.5`).

Usage (depuis la racine projet, MARKET relatif exige) :
  venv\\Scripts\\python -m python.mgi_initialzone.dca_atr --detector h7 --inst NQ ...
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Tuple

# Rend `scripts/backtest_ssrn_v2` importable quel que soit le cwd d'appel.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import backtest_ssrn_v2 as B  # noqa: E402

from .detector import ZoneSignal  # noqa: E402

RTH_CLOSE_SEC = 15 * 3600  # fin RTH fichier (coherent avec B.rth_bars : t <= 15h)
RECLAIM_MIN_I = 30  # premier cross close vs VWAP apres la 30e barre (fige c30)


def rth_close_time() -> time:
    """Heure file du flat EOD H7 (15:00:00)."""
    return time(15, 0, 0)


def detect_h7(inst: str = "NQ") -> Tuple[List[ZoneSignal], List[Tuple], dict]:
    """Detecte les zones H7-DCA-OR5 pour `inst` (NQ ou ES).

    Retourne (signaux, barres_1min, meta) ou barres_1min = barres RTH
    converties au format moteur `(dt, o, h, l, c, v)` (dt naif, meme
    timeline que `trend_dt`/`entry_px`). meta = diagnostics par jour.
    """
    assert inst in ("NQ", "ES"), inst
    daily, log = B.load_market(inst)
    days = sorted(d for d, (ct, b) in daily.items() if len(B.rth_bars(b)) >= B.MIN_RTH_BARS)
    rth: Dict[date, list] = {d: B.rth_bars(daily[d][1]) for d in days}

    signals: List[ZoneSignal] = []
    bars_1min: List[Tuple] = []
    n_days = len(days)
    n_norr = 0
    n_noreclaim = 0
    n_lastbar = 0
    for d in days:
        bars = rth[d]
        for (t, o, h, l, c, v) in bars:
            bars_1min.append((datetime.combine(d, time.min) + timedelta(seconds=t),
                              o, h, l, c, v))
        if len(bars) < B.OR_N + 1:
            continue
        seg = bars[:B.OR_N]
        oh = max(x[2] for x in seg)
        ol = min(x[3] for x in seg)
        orr = oh - ol
        if orr <= 0:
            n_norr += 1
            continue
        vw = B.vwap_rth(bars)
        found = False
        for i in range(RECLAIM_MIN_I, len(bars)):
            pc, c = bars[i - 1][4], bars[i][4]
            pv, vv = vw[i - 1], vw[i]
            drx = 0
            if pc <= pv and c > vv:
                drx = +1
            elif pc >= pv and c < vv:
                drx = -1
            if drx:
                found = True
                t = bars[i][0]
                rdt = datetime.combine(d, time.min) + timedelta(seconds=t)
                last_bar = t >= RTH_CLOSE_SEC
                if last_bar:
                    n_lastbar += 1
                signals.append(ZoneSignal(
                    trend_date=d, init_date=d,
                    bullish=(drx == 1),
                    vah=oh, poc=float("nan"), val=ol,
                    has_zone=not last_bar,
                    swing=ol if drx == 1 else oh,
                    atr_ref=orr,
                    body=abs(c - bars[i][1]),
                    trend_o=bars[i][1], trend_c=c,
                    trend_dt=rdt, init_dt=None, timeframe="H1",
                    entry_px=c,
                ))
                break
        if not found:
            n_noreclaim += 1
    bars_1min.sort(key=lambda b: b[0])
    meta = {"inst": inst, "n_days": n_days,
            "n_signals": len(signals),
            "n_valid": sum(1 for z in signals if z.has_zone),
            "n_norr": n_norr, "n_noreclaim": n_noreclaim,
            "n_lastbar": n_lastbar,
            "first": str(days[0]) if days else "-",
            "last": str(days[-1]) if days else "-",
            "fallbacks": log}
    return signals, bars_1min, meta
