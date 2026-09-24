"""Detection InitialZone Daily — miroir exact de MGIHtfInitialZone.cs (OnDailyClose).

Conventions (declarees) :
- Bougies Daily = jour calendaire UTC (timestamps fichier = UTC, prouve par
  halt 21h00 UTC et RTH 13h30-20h00 UTC ; drift declare vs Daily ETH CME NT8).
- Timestamps 1-min du fichier = OPEN de barre (standard NT8).
- ATR Wilder(20) non-biaise : seed SMA des 20 plus vieux TR puis lissage Wilder,
  evalue AVANT la trend-candle (endBarsAgo=1), warmup index daily >= 23.
- Value Area 70 %, rows 4 ticks, volume uniforme sur [Low..High], convention
  C# exacte : VAH = (hiK+1)*rowSize (bord sup), VAL = loK*rowSize (base),
  POC tie-break = rangee la plus proche du mid (pHigh+pLow)/2.

Sortie : liste de ZoneSignal (trend_date, bullish, vah, poc, val, swing, atr_ref).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple


@dataclass
class ZoneSignal:
    trend_date: date          # J0 (trend-candle) : jour calendaire (daily) ou jour de l'heure (H1)
    init_date: date           # J-1 (initial candle)
    bullish: bool
    vah: float
    poc: float
    val: float
    has_zone: bool
    swing: float              # PB (low J0) si bullish
    atr_ref: float            # ATR Wilder avant J0 (sert aussi de TP)
    body: float
    trend_o: float
    trend_c: float
    trend_dt: Optional[datetime] = None  # close de la trend-candle (renseigne en H1)
    init_dt: Optional[datetime] = None   # close de l'initiale (renseigne en H1)
    timeframe: str = "D1"     # "D1" ou "H1"
    entry_px: Optional[float] = None  # prix d'entree impose (detector H7 : close du reclaim)


def clean_bars(bars_1min: List[Tuple[datetime, float, float, float, float, float]],
               max_range_pct: float = 5.0) -> Tuple[List[Tuple], int, int, int]:
    """Ecarte les barres manifestement corrompues (exports NT8 week-end/rollover).

    Timestamps fichier = UTC (prouve : halt quotidien heure 21 UTC = 16h-17h CT,
    RTH 13h30-20h UTC = 8h30-15h CT). Daily = calendaire UTC (drift declare
    vs Daily ETH NT8, borne 17h CT).

    - Samedis : marche ferme (prints isoles fantomes).
    - Dimanches 00:00-21:59 UTC : ferme (reopen 17h00 CT = 22h00 UTC ete /
      23h00 UTC hiver). Dimanche 22:00-22:59 : drop si plat + vol<=2.
    - Criteres : range (high-low) > max_range_pct % du close precedent, ou prix
    (high/low/close) hors bande [0.5x, 2x] du close precedent. Une vraie barre
    1-min NQ ne fait jamais 5 % de range (meme FOMC < 1 %).
    Retourne (propre, n_samedis, n_dimanches, n_corrompues).
    """
    out = []
    n_sat = n_sun = dropped = 0
    prev_c: Optional[float] = None
    for b in bars_1min:
        dt, o, h, l, c, v = b
        if dt.weekday() == 5:
            n_sat += 1
            continue
        if dt.weekday() == 6 and (dt.hour < 22 or (dt.hour == 22 and h == l and v <= 2)):
            n_sun += 1
            continue
        bad = False
        if prev_c is not None and prev_c > 0:
            if (h - l) > (max_range_pct / 100.0) * prev_c:
                bad = True
            elif h < 0.5 * prev_c or l > 2.0 * prev_c or c < 0.5 * prev_c or c > 2.0 * prev_c:
                bad = True
        if bad:
            dropped += 1
        else:
            out.append(b)
        if c > 0 and not (c < 0.5 * (prev_c or c) or c > 2.0 * (prev_c or c)):
            prev_c = c
    return out, n_sat, n_sun, dropped


def build_period(bars_1min: List[Tuple[datetime, float, float, float, float, float]],
                 step_min: int) -> Tuple[List[datetime],
                                         Dict[datetime, Tuple[float, float, float, float]],
                                         Dict[datetime, List[Tuple[datetime, float, float, float, float, float]]]]:
    """Agrege 1-min -> bougies de step_min (floor, convention open).
    Retourne (closes tries, ohlc, barres 1-min par bougie). step 1440 = daily UTC."""
    per: Dict[datetime, List] = {}
    for b in bars_1min:
        dt = b[0]
        base = dt.replace(second=0, microsecond=0)
        mins = base.hour * 60 + base.minute
        if step_min >= 1440:
            key = datetime.combine(base.date(), datetime.min.time())
        else:
            bucket = (mins // step_min) * step_min
            key = datetime.combine(base.date(), datetime.min.time()) + timedelta(minutes=bucket + step_min)
            if bucket + step_min >= 1440:  # close chevauchant minuit -> rattache au jour suivant
                key = datetime.combine(base.date() + timedelta(days=1), datetime.min.time())
        per.setdefault(key, []).append(b)
    closes = sorted(per.keys())
    ohlc: Dict[datetime, Tuple[float, float, float, float]] = {}
    for k in closes:
        lst = sorted(per[k])
        ohlc[k] = (lst[0][1], max(b[2] for b in lst), min(b[3] for b in lst), lst[-1][4])
    return closes, ohlc, per


def detect_tf(bars_1min: List[Tuple[datetime, float, float, float, float, float]],
              step_min: int = 1440, atr_period: int = 20,
              atr_mult: float = 1.0, tick_size: float = 0.25,
              row_ticks: int = 4, va_pct: float = 70.0,
              tf_label: str = "D1") -> Tuple[List[ZoneSignal], List[datetime]]:
    """Detection generique (miroir MGIHtfInitialZone : ATR Wilder non-biaise + VA initiale).
    step_min=1440 -> Daily (equivaut a detect_daily), 60 -> H1, 10080 -> Weekly."""
    closes, ohlc, per = build_period(bars_1min, step_min)
    keys = closes
    trs = []
    prev_c: Optional[float] = None
    for k in keys:
        o, h, l, c = ohlc[k]
        trs.append((h - l) if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = c
    signals: List[ZoneSignal] = []
    for j in range(len(keys)):
        if j < atr_period + 3:
            continue
        k = keys[j]
        o, h, l, c = ohlc[k]
        atr = wilder_atr_before(trs, atr_period, j)
        if atr is None or atr <= 0:
            continue
        body = abs(c - o)
        if body <= atr * atr_mult:
            continue
        bull = c > o
        swing = l if bull else h
        init_k = keys[j - 1]
        va = value_area_70(sorted(per[init_k]), tick_size, row_ticks, va_pct)
        if va is None:
            signals.append(ZoneSignal(k.date(), init_k.date(), bull, float("nan"), float("nan"),
                                      float("nan"), False, swing, atr, body, o, c,
                                      trend_dt=k, init_dt=init_k, timeframe=tf_label))
        else:
            vah, poc, val = va
            signals.append(ZoneSignal(k.date(), init_k.date(), bull, vah, poc, val,
                                      True, swing, atr, body, o, c,
                                      trend_dt=k, init_dt=init_k, timeframe=tf_label))
    return signals, keys


def true_ranges(days: List[date], ohlc: Dict[date, Tuple[float, float, float, float]]) -> List[float]:
    trs = []
    prev_c: Optional[float] = None
    for d in days:
        o, h, l, c = ohlc[d]
        tr = (h - l) if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
        prev_c = c
    return trs


def wilder_atr_before(trs: List[float], period: int, trend_idx: int) -> Optional[float]:
    """ATR Wilder evalue sur trs[0..trend_idx-1] (trend exclue). None si historique insuffisant.

    Miroir de WilderAtr(bip, period, endBarsAgo=1) : seed SMA des `period` plus
    vieux TR puis lissage (atr*(p-1)+tr)/p jusqu'a la barre precedant la trend.
    Exige trend_idx >= period + 3 (== CurrentBars[1] >= AtrDailyPeriod + 3).
    """
    if trend_idx < period + 3:
        return None
    hist = trs[:trend_idx]  # sans la trend
    seed = sum(hist[:period]) / period
    atr = seed
    for tr in hist[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def accumulate_row_vol(vol: Dict[int, float], h: float, l: float, v: float,
                       row_size: float) -> bool:
    """Ajoute une barre au dict rangee->volume (uniforme sur [Low..High]). False si ignoree."""
    if math.isnan(h) or math.isnan(l) or math.isnan(v) or v <= 0:
        return False
    lo = math.floor(l / row_size)
    hi = math.floor(h / row_size)
    n = max(1, hi - lo + 1)
    q = v / n
    for k in range(lo, hi + 1):
        vol[k] = vol.get(k, 0.0) + q
    return True


def va_from_rows(vol: Dict[int, float], row_size: float, va_pct: float,
                 p_high: float, p_low: float) -> Optional[Tuple[float, float, float]]:
    """Expansion VA depuis POC. Retourne (vah, poc, val) ou None si vide."""
    if not vol:
        return None
    mid = (p_high + p_low) * 0.5
    min_k = min(vol.keys())
    max_k = max(vol.keys())
    # POC : max volume, ex-aequo -> rangee la plus proche du mid (miroir C#)
    poc = None
    pv = float("-inf")
    first = True
    for k, vv in vol.items():
        row_mid = (k + 0.5) * row_size
        if first or vv > pv or (vv == pv and abs(row_mid - mid) < abs((poc + 0.5) * row_size - mid)):
            poc = k
            pv = vv
            first = False
    total = sum(vol.values())
    target = total * va_pct / 100.0
    lo_k = hi_k = poc
    acc = vol[poc]
    guard = 0
    while acc < target and guard < 100000:
        guard += 1
        up = vol.get(hi_k + 1, -1.0) if hi_k + 1 <= max_k else -1.0
        dn = vol.get(lo_k - 1, -1.0) if lo_k - 1 >= min_k else -1.0
        if up < 0 and dn < 0:
            break
        if up >= dn:
            hi_k += 1
            if hi_k in vol:
                acc += vol[hi_k]
        else:
            lo_k -= 1
            if lo_k in vol:
                acc += vol[lo_k]
    vah = (hi_k + 1) * row_size
    val = lo_k * row_size
    poc_px = (poc + 0.5) * row_size
    return (vah, poc_px, val)


def value_area_70(day_bars: List[Tuple[datetime, float, float, float, float, float]],
                  tick_size: float, row_ticks: int = 4,
                  va_pct: float = 70.0) -> Optional[Tuple[float, float, float]]:
    """VA C#-exacte. Retourne (vah, poc, val) ou None si pas de volume."""
    row_size = tick_size * max(1, row_ticks)
    vol: Dict[int, float] = {}
    p_high = float("-inf")
    p_low = float("inf")
    any_bar = False
    for _, _o, h, l, _c, v in day_bars:
        if accumulate_row_vol(vol, h, l, v, row_size):
            any_bar = True
            if h > p_high:
                p_high = h
            if l < p_low:
                p_low = l
    if not any_bar or not vol:
        return None
    return va_from_rows(vol, row_size, va_pct, p_high, p_low)


def monthly_va(bars_1min: List[Tuple[datetime, float, float, float, float, float]],
               tick_size: float = 0.25, row_ticks: int = 4,
               va_pct: float = 70.0) -> Dict[Tuple[int, int], Tuple[float, float, float]]:
    """VA complete par mois calendaire (heure fichier). Retourne {(y,m): (vah, poc, val)}."""
    row_size = tick_size * max(1, row_ticks)
    per_m: Dict[Tuple[int, int], list] = {}
    for b in bars_1min:
        per_m.setdefault((b[0].year, b[0].month), []).append(b)
    out: Dict[Tuple[int, int], Tuple[float, float, float]] = {}
    for k, lst in per_m.items():
        vol: Dict[int, float] = {}
        p_high = float("-inf")
        p_low = float("inf")
        for _, _o, h, l, _c, v in sorted(lst):
            if accumulate_row_vol(vol, h, l, v, row_size):
                if h > p_high:
                    p_high = h
                if l < p_low:
                    p_low = l
        va = va_from_rows(vol, row_size, va_pct, p_high, p_low)
        if va is not None:
            out[k] = va
    return out


def prev_month_key(y: int, m: int) -> Tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


def daily_va(bars_1min: List[Tuple[datetime, float, float, float, float, float]],
             tick_size: float = 0.25, row_ticks: int = 4,
             va_pct: float = 70.0) -> Dict[date, Tuple[float, float, float]]:
    """VA complete par jour calendaire UTC. Retourne {date: (vah, poc, val)}."""
    row_size = tick_size * max(1, row_ticks)
    per_d: Dict[date, list] = {}
    for b in bars_1min:
        per_d.setdefault(b[0].date(), []).append(b)
    out: Dict[date, Tuple[float, float, float]] = {}
    for k, lst in per_d.items():
        vol: Dict[int, float] = {}
        p_high = float("-inf")
        p_low = float("inf")
        for _, _o, h, l, _c, v in sorted(lst):
            if accumulate_row_vol(vol, h, l, v, row_size):
                if h > p_high:
                    p_high = h
                if l < p_low:
                    p_low = l
        va = va_from_rows(vol, row_size, va_pct, p_high, p_low)
        if va is not None:
            out[k] = va
    return out


def monthly_regime(poc_now: Optional[float],
                   prev_va: Optional[Tuple[float, float, float]]) -> str:
    """Regime mensuel : 'buy' (POC > prevVAH), 'sell' (POC < prevVAL), sinon 'both'."""
    if poc_now is None or prev_va is None:
        return "both"
    prev_vah, _pp, prev_val = prev_va
    if poc_now > prev_vah:
        return "buy"
    if poc_now < prev_val:
        return "sell"
    return "both"


def detect_daily(bars_1min: List[Tuple[datetime, float, float, float, float, float]],
                 tick_size: float = 0.25, atr_period: int = 20,
                 atr_mult: float = 1.0, row_ticks: int = 4,
                 va_pct: float = 70.0) -> Tuple[List[ZoneSignal], List[date]]:
    """Detecte les InitialZone Daily bullish + bearish. Retourne (signaux, jours).
    Delegue a detect_tf(1440) : resultats identiques a l'ancien moteur."""
    signals, keys = detect_tf(bars_1min, 1440, atr_period, atr_mult,
                              tick_size, row_ticks, va_pct, "D1")
    return signals, [k.date() for k in keys]
