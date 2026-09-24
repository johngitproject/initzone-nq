"""Detection AcceptZone — methode S2 §2 seule (3 profils, composite J1+J2).

Regles figees (miroir exact de scripts/backtest_swing_s2.py:53-75) :
- Triplets J1/J2/J3 consecutifs parmi les jours gardes (>= MIN_ETH_BARS, defaut 800).
- Balance : VAL1 <= POC2 <= VAH1 (profils 70 % tick-level, volume uniforme).
- Composite : barres 1-min J1+J2 fusionnees -> profile_70 70 % (addition tick-a-tick).
- Acceptation haussiere : POC3 > POC1 ET POC3 > POC2 ET POC3 > VAHc + BUF*tick
  -> zone rebid (bullish=True).
- Acceptation baissiere : POC3 < POC1 ET POC3 < POC2 ET POC3 < VALc - BUF*tick
  -> zone reoffer (bullish=False).
- Zone : vah/val/poc = composite, creation = J3.

Seule difference vs S2 : atr_ref = Wilder-DCA evalue AVANT J3
(detector.wilder_atr_before, warmup index >= 23), pour TP/renforts/sizing
du moteur DCA. Aucune logique d'execution S2 reprise (pas de fade/accept
intraday, pas de good-side/freshest/HOLD ici — c'est run_side qui gere
l'expiry 10j).

Conventions date : jour calendaire du fichier (== date S2 load_market ;
les exports sont ETH ~1380 barres/j, cf. LISEZMOI heures Chicago).
Apres clean_bars DCA (samedis/dimanches ecartes), le groupement par date
coincide avec eth_days S2 sur les jours gardes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from .detector import ZoneSignal, build_period, wilder_atr_before

MIN_ETH_BARS = 800
BUF_TICKS = 2


def _profile70(
    day_bars: List[Tuple[datetime, float, float, float, float, float]],
    tick: float,
) -> Optional[Dict[str, float]]:
    """profile_70 tick-level, miroir exact de scripts/backtest_vp02.py:115-143.

    day_bars : [(dt, o, h, l, c, v)] (dt ignore, seuls h/l/v servent).
    Retourne {"poc","vah","val","tot"} ou None si pas de volume.
    """
    vol: Dict[int, float] = defaultdict(float)
    for _dt, _o, h, l, _c, v in day_bars:
        lo = int(round(l / tick))
        hi = int(round(h / tick))
        n = max(1, hi - lo + 1)
        q = v / n
        for k in range(lo, hi + 1):
            vol[k] += q
    if not vol:
        return None
    tot = sum(vol.values())
    poc = max(vol, key=lambda k: (vol[k], -abs(k)))
    lo_k = hi_k = poc
    acc = vol[poc]
    keys = sorted(vol)
    while acc < 0.70 * tot:
        up = vol[hi_k + 1] if hi_k + 1 <= keys[-1] else -1
        dn = vol[lo_k - 1] if lo_k - 1 >= keys[0] else -1
        if up < 0 and dn < 0:
            break
        if up >= dn:
            hi_k += 1
            acc += vol.get(hi_k, 0.0)
        else:
            lo_k -= 1
            acc += vol.get(lo_k, 0.0)
    return {"poc": poc * tick, "vah": hi_k * tick, "val": lo_k * tick, "tot": tot}


def _base_zones(
    bars_1min: List[Tuple[datetime, float, float, float, float, float]],
    tick_size: float = 0.25,
    buf_ticks: int = BUF_TICKS,
    min_eth_bars: int = MIN_ETH_BARS,
) -> Tuple[List[dict], dict]:
    """Socle commun : frames + profils + triplets §2. Retourne (bases, ctx).

    base = {k1,k2,k3,p1,p2,p3,pc,bullish} avec bullish True=rebid / False=reoffer.
    ctx = {closes,ohlc,per,trs,idx_of,kept,prof} pour les variantes (reverse...).
    Comportement §2 strict, sans filtre ATR (fait par l'appelant).
    """
    closes, ohlc, per = build_period(bars_1min, 1440)
    # TR series sur tous les jours (convention DCA, comme detect_tf)
    trs: List[float] = []
    prev_c: Optional[float] = None
    for k in closes:
        o, h, l, c = ohlc[k]
        trs.append((h - l) if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = c
    idx_of = {k: j for j, k in enumerate(closes)}

    # Jours gardes (>= min barres), tries — equivaut a eth_days S2
    kept = sorted(k for k in closes if len(per[k]) >= min_eth_bars)
    prof: Dict[datetime, Optional[Dict[str, float]]] = {}
    for k in kept:
        prof[k] = _profile70(sorted(per[k]), tick_size)

    buf = buf_ticks * tick_size
    bases: List[dict] = []
    for i in range(2, len(kept)):
        k1, k2, k3 = kept[i - 2], kept[i - 1], kept[i]
        p1, p2, p3 = prof[k1], prof[k2], prof[k3]
        if not p1 or not p2 or not p3:
            continue
        # Balance J1/J2 : POC2 dans VA1 (S2 :62)
        if not (p1["val"] <= p2["poc"] <= p1["vah"]):
            continue
        # Composite J1+J2 recalculé 70 % (S2 :65-66)
        pc = _profile70(sorted(per[k1] + per[k2]), tick_size)
        if not pc:
            continue
        if pc["val"] >= pc["vah"]:
            continue
        bullish: Optional[bool] = None
        if p3["poc"] > p1["poc"] and p3["poc"] > p2["poc"] and p3["poc"] > pc["vah"] + buf:
            bullish = True  # rebid
        elif p3["poc"] < p1["poc"] and p3["poc"] < p2["poc"] and p3["poc"] < pc["val"] - buf:
            bullish = False  # reoffer
        else:
            continue
        bases.append({"k1": k1, "k2": k2, "k3": k3, "p1": p1, "p2": p2,
                      "p3": p3, "pc": pc, "bullish": bullish})
    ctx = {"closes": closes, "ohlc": ohlc, "per": per, "trs": trs,
           "idx_of": idx_of, "kept": kept, "prof": prof}
    return bases, ctx


def detect_accept(
    bars_1min: List[Tuple[datetime, float, float, float, float, float]],
    tick_size: float = 0.25,
    atr_period: int = 20,
    buf_ticks: int = BUF_TICKS,
    min_eth_bars: int = MIN_ETH_BARS,
) -> Tuple[List[ZoneSignal], List[date]]:
    """Detecte les AcceptZone daily. Retourne (signaux, jours calendaires)."""
    bases, ctx = _base_zones(bars_1min, tick_size, buf_ticks, min_eth_bars)
    ohlc, trs, idx_of = ctx["ohlc"], ctx["trs"], ctx["idx_of"]

    signals: List[ZoneSignal] = []
    for b in bases:
        k2, k3, pc, bullish = b["k2"], b["k3"], b["pc"], b["bullish"]
        # atr_ref Wilder-DCA avant J3 (decision verrouillee)
        j3 = idx_of[k3]
        atr = wilder_atr_before(trs, atr_period, j3)
        if atr is None or atr <= 0:
            continue
        o3, h3, l3, c3 = ohlc[k3]
        body = abs(c3 - o3)
        swing = l3 if bullish else h3
        signals.append(
            ZoneSignal(
                trend_date=k3.date(),
                init_date=k2.date(),
                bullish=bullish,
                vah=pc["vah"],
                poc=pc["poc"],
                val=pc["val"],
                has_zone=True,
                swing=swing,
                atr_ref=atr,
                body=body,
                trend_o=o3,
                trend_c=c3,
                trend_dt=k3,
                init_dt=k2,
                timeframe="D1",
            )
        )
    days = [k.date() for k in ctx["closes"]]
    signals.sort(key=lambda z: z.trend_date)
    return signals, days


def detect_reverse(
    bars_1min: List[Tuple[datetime, float, float, float, float, float]],
    tick_size: float = 0.25,
    atr_period: int = 20,
    buf_ticks: int = BUF_TICKS,
    min_eth_bars: int = MIN_ETH_BARS,
    flip_window: int = 10,
) -> Tuple[List[ZoneSignal], List[date]]:
    """Zones reverse (fake acceptance), symetrique, decision verrouillee.

    Pour chaque zone §2 (creation J3, sens S, composite pc) : premier jour F
    (jours gardes, J3 < F <= J3 + flip_window calendaires) ou le POC daily
    (meme profil §2) revient STRICTEMENT dans [VALc, VAHc] (bornes incluses,
    sans buffer) -> la zone flippe de sens (reoffer->acheteuse,
    rebid->vendeuse). Un seul flip par zone (le premier).
    Signal : vah/val/poc = pc inchange, trend_date = F, init_date = J3
    (delai flip = trend - init en jours), atr_ref = Wilder-DCA avant F,
    swing/body/o/c du jour F. Expiry DCA standard 10j depuis F.
    """
    bases, ctx = _base_zones(bars_1min, tick_size, buf_ticks, min_eth_bars)
    ohlc, trs, idx_of = ctx["ohlc"], ctx["trs"], ctx["idx_of"]
    kept, prof = ctx["kept"], ctx["prof"]

    signals: List[ZoneSignal] = []
    for b in bases:
        k3, pc = b["k3"], b["pc"]
        flipped: Optional[bool] = None
        kf = None
        for kc in kept:
            if kc <= k3:
                continue
            if (kc - k3).days > flip_window:
                break
            pf = prof.get(kc)
            if not pf:
                continue
            if pc["val"] <= pf["poc"] <= pc["vah"]:
                flipped = not b["bullish"]
                kf = kc
                break  # un seul flip : le premier
        if flipped is None or kf is None:
            continue
        jf = idx_of[kf]
        atr = wilder_atr_before(trs, atr_period, jf)
        if atr is None or atr <= 0:
            continue
        of, hf, lf, cf = ohlc[kf]
        signals.append(
            ZoneSignal(
                trend_date=kf.date(),
                init_date=k3.date(),
                bullish=flipped,
                vah=pc["vah"],
                poc=pc["poc"],
                val=pc["val"],
                has_zone=True,
                swing=lf if flipped else hf,
                atr_ref=atr,
                body=abs(cf - of),
                trend_o=of,
                trend_c=cf,
                trend_dt=kf,
                init_dt=k3,
                timeframe="D1",
            )
        )
    days = [k.date() for k in ctx["closes"]]
    signals.sort(key=lambda z: z.trend_date)
    return signals, days


def detect_fade(
    bars_1min: List[Tuple[datetime, float, float, float, float, float]],
    tick_size: float = 0.25,
    atr_period: int = 20,
    buf_ticks: int = BUF_TICKS,
    min_eth_bars: int = MIN_ETH_BARS,
    hold_days: int = 10,
) -> Tuple[List[ZoneSignal], List[date]]:
    """Fade short des retours en zone acheteuse, decision verrouillee.

    Pour chaque zone §2 **rebid** : premier jour F (jours calendaires,
    J3 < F <= J3 + hold_days) avec close_F STRICTEMENT dans [VALc, VAHc]
    (meme definition que l'etude retest) -> la zone est emise **bearish**
    (fade du retour), trend_date = F, init_date = J3, composite inchange,
    atr_ref = Wilder-DCA avant F, swing/body/o/c du jour F. Expiry DCA
    standard 10j depuis F. Reoffer ignorees (fade des zones acheteuses seul).
    """
    bases, ctx = _base_zones(bars_1min, tick_size, buf_ticks, min_eth_bars)
    ohlc, trs, idx_of = ctx["ohlc"], ctx["trs"], ctx["idx_of"]
    closes = ctx["closes"]

    signals: List[ZoneSignal] = []
    for b in bases:
        if not b["bullish"]:
            continue  # rebid seules
        k3, pc = b["k3"], b["pc"]
        vah, val = pc["vah"], pc["val"]
        kf = None
        for kc in closes:
            if kc <= k3 or (kc - k3).days > hold_days:
                continue
            if val <= ohlc[kc][3] <= vah:
                kf = kc
                break  # premier retour : le seul
        if kf is None:
            continue
        jf = idx_of[kf]
        atr = wilder_atr_before(trs, atr_period, jf)
        if atr is None or atr <= 0:
            continue
        of, hf, lf, cf = ohlc[kf]
        signals.append(
            ZoneSignal(
                trend_date=kf.date(),
                init_date=k3.date(),
                bullish=False,
                vah=vah,
                poc=pc["poc"],
                val=val,
                has_zone=True,
                swing=hf,
                atr_ref=atr,
                body=abs(cf - of),
                trend_o=of,
                trend_c=cf,
                trend_dt=kf,
                init_dt=k3,
                timeframe="D1",
            )
        )
    days = [k.date() for k in closes]
    signals.sort(key=lambda z: z.trend_date)
    return signals, days
