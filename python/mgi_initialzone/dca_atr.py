"""Backtest DCA/averaging sur InitialZone Daily — long et short separes.

Spec verrouillee (option b) :
- Capital 1M. Unite/qty fige par cycle : floor(10 000 $ / (atr_ref_pts x 20 $/pt NQ)), min 1.
- Long : zones bullish, VAH+VAL. Ordre limite acheteuse au niveau brut L :
  fill si low1 <= L, prix = min(open1, L). Cycles independants par (zone, niveau).
- Short : miroir (zones bearish, fill si high1 >= L, prix = max(open1, L)).
- Renforts (market, meme qty) : close 1-min au-dela du rung (dernier fill -/+ 1xATR).
  Cap 3 renforts (4 unites max).
- Entrees (--entry-mode) : immediate (limite 1er touch, defaut) | confirm (touche
  puis fill au 1er close 5-min du bon cote, avec rattrapage TP intra-lag) |
  second-touch (2e touche du jour) | delay (market N min apres 1re touche).
- TP : touche 1-min de avg +/- ATR -> cloture TOUT le cycle. TP gagne les egalites.
- Urgence cycle : latent (au close 1-min) <= -emerg_dollars (defaut 40 000 $) -> tout aplati @close. Reprise J+1.
- Re-trigger : meme niveau rejouable sauf le jour calendaire de l'entree du cycle precedent.
- Fills autorises : trend_date < date <= trend_date + 10j. Expiry trade : entree + 10j.
- MAE/MFE cycles : excursions extremes (high/low 1-min) vs PREMIERE entree, en ticks.

Usage :
  venv\\Scripts\\python -m python.mgi_initialzone.dca_atr --side both --out-dir backtest_out\\initzone_dca
  venv\\Scripts\\python -m python.mgi_initialzone.dca_atr --side long --start 2025-01-01 --end 2025-01-31 --out-dir backtest_out\\initzone_dca_smoke
  Variante H7-DCA-OR5 (entree reclaim, flat EOD, couts Lifetime, base 100k) :
  venv\\Scripts\\python -m python.mgi_initialzone.dca_atr --detector h7 --entry-mode h7 --inst NQ --emerg-dollars 4000 --unit-risk 1000 --pt-value 20 --cost-rt 4.36 --out-dir backtest_out\\h7_dca\\NQ
"""
from __future__ import annotations

import argparse
import csv
import math
import pathlib
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Tuple

from ..mgi_contact.data_loader import load_and_merge
from ..mgi_contact.session_clock import is_us_dst
from .detector import (ZoneSignal, accumulate_row_vol, clean_bars, daily_va, detect_daily,
                       monthly_regime, monthly_va, prev_month_key, va_from_rows)
from .detect_accept import detect_accept, detect_fade, detect_reverse
from .reject2_atr import FILE_RANGES, parse_dt, resample_floor

PT_VALUE = 20.0      # $/point NQ
UNIT_RISK = 10_000.0  # 1 % de 1M par ATR adverse (option b)
EMERG = -40_000.0     # urgence cycle : -4 % du capital
MAX_ADDS = 3
EXPIRY_D = 10


def unit_qty(atr_pts: float, unit_risk: float = UNIT_RISK,
             pt_value: float = PT_VALUE) -> int:
    return max(1, int(unit_risk // (atr_pts * pt_value)))


def monthly_opens(bars_1min) -> Dict[Tuple[int, int], float]:
    """Open mensuel = open de la 1re barre 1-min du mois calendaire (heure fichier)."""
    mo: Dict[Tuple[int, int], float] = {}
    for dt, o, _h, _l, _c, _v in bars_1min:
        mo.setdefault((dt.year, dt.month), o)
    return mo


def daily_opens(bars_1min) -> Dict[date, float]:
    """Open journalier = open de la 1re barre 1-min du jour calendaire UTC."""
    do: Dict[date, float] = {}
    for dt, o, _h, _l, _c, _v in bars_1min:
        do.setdefault(dt.date(), o)
    return do


def rth_opens(bars_1min, rth_start_hour: int = 14) -> Dict[date, float]:
    """Open RTH = open de la 1re barre 1-min du jour a heure >= rth_start_hour (UTC fixe).
    Jour sans barre RTH (ferie) : absent du dict -> gate inactif ce jour-la."""
    ro: Dict[date, float] = {}
    for dt, o, _h, _l, _c, _v in bars_1min:
        if dt.hour >= rth_start_hour:
            ro.setdefault(dt.date(), o)
    return ro


def or5_ranges(bars_1min, open_hour: int = 14, open_min: int = 30, n_bars: int = 5) -> Dict[date, float]:
    """ORR par jour = high-low des n_bars premieres barres 1-min a partir de
    open_hour:open_min UTC (ex. 14h30-14h35, OR complete a 14h35).
    Jour incomplet ou plat : absent (aucun fill ce jour-la en mode or5)."""
    per: Dict[date, list] = {}
    for b in bars_1min:
        dt = b[0]
        if (dt.hour, dt.minute) >= (open_hour, open_min):
            per.setdefault(dt.date(), []).append(b)
    out: Dict[date, float] = {}
    for d, lst in per.items():
        seg = sorted(lst)[:n_bars]
        if len(seg) < n_bars:
            continue
        orr = max(b[2] for b in seg) - min(b[3] for b in seg)
        if orr > 0:
            out[d] = orr
    return out


OR_COMPLETE_HM = (14, 35)  # fills interdits avant 14h35 UTC en mode or5 (OR du jour requise)


def run_side(bars_1min, signals: List[ZoneSignal], side: str, out_dir: pathlib.Path,
             tick: float, start_dt, mo: Dict[Tuple[int, int], float],
             monthly_filter: str = "none",
             mva: Dict[Tuple[int, int], Tuple[float, float, float]] | None = None,
             regime_filter: str = "none", emerg_dollars: float = 40000.0,
             emerg_atr_mult: float = 0.0,
             entry_mode: str = "immediate", entry_delay_min: int = 15,
             min_zone_age: int = 0, session_filter: str = "all",
             hourly: bool = False, expiry_hours: float = 10.0,
             dailyopen_filter: str = "none",
             do: Dict[date, float] | None = None,
             dva: Dict[date, Tuple[float, float, float]] | None = None,
             dayregime_filter: str = "none", no_expiry: bool = False,
             rthopen_filter: str = "none",
             ro: Dict[date, float] | None = None,
             vwap_filter: str = "none", vwap_anchor: str = "daily",
             pt_value: float = 20.0, cost_rt: float = 0.0,
             unit_risk: float = 10_000.0, eod_time=None,
             range_mode: str = "atr",
             orr: Dict[date, float] | None = None,
             tp_mult: float = 1.0, max_open_cycles: int = 0) -> dict:
    assert side in ("long", "short")
    sgn = 1 if side == "long" else -1
    want_bull = side == "long"
    need_regime = regime_filter in (side, "both")
    need_dregime = dayregime_filter in (side, "both")
    need_vwap = vwap_filter in (side, "both")
    # VWAP developing : cumul prix-typique*volume (O(1)/barre)
    vw_pv, vw_v = 0.0, 0.0
    vw_key = None
    vw_start = None
    refused_vwap = 0

    def eth_session_key(dt):
        """Cle de session ETH : jour CT de l'open 17h00 (DST-aware).
        Ex. dimanche 22h UTC (ete) -> session du dimanche ; lundi 01h UTC -> session du dimanche."""
        try:
            dst = is_us_dst(dt.date())
        except Exception:
            dst = True
        ct = dt - timedelta(hours=5 if dst else 6)
        if ct.time() >= time(17, 0):
            return (ct.date(), dst)
        return (ct.date() - timedelta(days=1), dst)

    def roll_vwap(dt, h, l, c, v):
        nonlocal vw_pv, vw_v, vw_key, vw_start, refused_vwap
        if not need_vwap:
            return
        if vwap_anchor == "monthly":
            key = (dt.year, dt.month)
        elif vwap_anchor == "eth":
            key = eth_session_key(dt)
        else:
            key = dt.date()
        if key != vw_key:
            vw_key, vw_pv, vw_v = key, 0.0, 0.0
            vw_start = dt
        if v is not None and not math.isnan(v) and v > 0:
            vw_pv += ((h + l + c) / 3.0) * v
            vw_v += v

    def vwap_now():
        return (vw_pv / vw_v) if vw_v > 0 else None
    bars_5 = resample_floor(bars_1min, 5) if entry_mode == "confirm" else []
    i5 = 0  # pointeur closes 5-min traitees
    zones = [z for z in signals if z.bullish == want_bull and z.has_zone and z.val < z.vah]
    zones.sort(key=lambda z: z.trend_date)
    # Variante H7 : index des reclaims par datetime (entree market, pas de
    # touche limite sur VAL/VAH). Cle exacte = meme timeline que bars_1min.
    h7_by_dt = {}
    if entry_mode == "h7":
        for zi, z in enumerate(zones):
            if z.trend_dt is not None:
                h7_by_dt.setdefault(z.trend_dt, (zi, z))

    cycles, legs = [], []
    cid = 0
    # cycles ouverts : dict key=(zi, lvl) -> etat
    open_cyc: Dict[Tuple[int, str], dict] = {}
    last_entry_day: Dict[Tuple[int, str], date] = {}
    refused_warmup = 0
    refused_regime = 0
    refused_session = 0
    refused_age = 0
    refused_or = 0
    refused_concur = 0
    fills_by_regime = defaultdict(int)
    armed: Dict[Tuple[int, str], datetime] = {}      # confirm : (zi,lvl) -> heure touche
    touch_count: Dict[Tuple[int, str, date], int] = {}  # second-touch : touches par jour
    pending: Dict[Tuple[int, str], datetime] = {}    # delay : (zi,lvl) -> heure de fire
    pending_reg: Dict[Tuple[int, str], str] = {}  # regime fige a la programmation

    # developing mensuel : dict rangee->vol + extremes, recalcule 1x/jour
    row_size = tick * 4
    dev_vol: Dict[int, float] = {}
    dev_hi, dev_lo = float("-inf"), float("inf")
    dev_month: Tuple[int, int] | None = None
    dev_day: date | None = None
    day_regime = "both"
    # developing journalier : POC du jour en cours vs VA de la veille (filtre dayregime)
    dd_day: date | None = None
    dd_hour: int | None = None
    dd_vol: Dict[int, float] = {}
    dd_hi, dd_lo = float("-inf"), float("inf")
    day_regime_d = "both"

    def roll_developing(dt, h, l, v):
        nonlocal dev_month, dev_day, dev_vol, dev_hi, dev_lo, day_regime
        mk = (dt.year, dt.month)
        if mk != dev_month:
            dev_month, dev_vol, dev_hi, dev_lo = mk, {}, float("-inf"), float("inf")
            dev_day = None
        if dt.date() != dev_day:
            dev_day = dt.date()
            if need_regime and mva is not None:
                va = va_from_rows(dev_vol, row_size, 70.0, dev_hi, dev_lo) if dev_vol else None
                day_regime = monthly_regime(va[1] if va else None, mva.get(prev_month_key(*mk)))
        if accumulate_row_vol(dev_vol, h, l, v, row_size):
            if h > dev_hi:
                dev_hi = h
            if l < dev_lo:
                dev_lo = l
        # developing journalier (recalcule au plus 1x/heure)
        nonlocal dd_day, dd_hour, dd_vol, dd_hi, dd_lo, day_regime_d
        if dt.date() != dd_day:
            dd_day, dd_vol, dd_hi, dd_lo = dt.date(), {}, float("-inf"), float("inf")
            dd_hour = None
        if dt.hour != dd_hour:
            dd_hour = dt.hour
            if need_dregime and dva is not None:
                va = va_from_rows(dd_vol, row_size, 70.0, dd_hi, dd_lo) if dd_vol else None
                day_regime_d = monthly_regime(va[1] if va else None, dva.get(dd_day - timedelta(days=1)))
        if accumulate_row_vol(dd_vol, h, l, v, row_size):
            if h > dd_hi:
                dd_hi = h
            if l < dd_lo:
                dd_lo = l
    def gate_ok(dt, d, c, trend_date=None, hour=None, z_trend_dt=None):
        """Filtres monthly/daily-open + regime + age zone + session. Retourne reg ou None.
        min_zone_age = jours (daily) ou heures (hourly)."""
        nonlocal refused_warmup, refused_regime, refused_session, refused_age, refused_vwap
        if monthly_filter in (side, "both"):
            mop = mo.get((dt.year, dt.month))
            if mop is not None:
                if side == "long" and not (c > mop):
                    return None
                if side == "short" and not (c < mop):
                    return None
        if dailyopen_filter in (side, "both"):
            dop = do.get(d) if do else None
            if dop is not None:
                if side == "long" and not (c > dop):
                    return None
                if side == "short" and not (c < dop):
                    return None
        if rthopen_filter in (side, "both"):
            rop = ro.get(d) if ro else None
            if rop is not None:
                if side == "long" and not (c > rop):
                    return None
                if side == "short" and not (c < rop):
                    return None
        if need_vwap:
            if vw_start is None or (dt - vw_start).total_seconds() < 3600:
                refused_vwap += 1
                return None  # warmup : 60 min de donnees avant un VWAP fiable
            vw = vwap_now()
            if vw is not None:
                if side == "long" and not (c > vw):
                    refused_vwap += 1
                    return None
                if side == "short" and not (c < vw):
                    refused_vwap += 1
                    return None
        reg = day_regime if need_regime else "both"
        if need_regime:
            if d.day <= 3:
                refused_warmup += 1
                return None
            if (side == "long" and reg != "buy") or (side == "short" and reg != "sell"):
                refused_regime += 1
                return None
        if need_dregime:
            if dt.hour < 3:
                refused_warmup += 1
                return None  # warmup 00h-02h59 UTC : POC du jour non fiable
            dreg = day_regime_d
            if (side == "long" and dreg != "buy") or (side == "short" and dreg != "sell"):
                refused_regime += 1
                return None
            reg = dreg
        if min_zone_age > 0 and trend_date is not None:
            if hourly and z_trend_dt is not None:
                if (dt - z_trend_dt).total_seconds() / 3600 < min_zone_age:
                    refused_age += 1
                    return None
            elif (d - trend_date).days < min_zone_age:
                refused_age += 1
                return None
        if session_filter != "all" and hour is not None:
            is_rth = 14 <= hour <= 20  # RTH 8h30-15h CT ~= 14h-20h UTC
            if session_filter == "on" and is_rth:
                refused_session += 1
                return None
            if session_filter == "rth" and not is_rth:
                refused_session += 1
                return None
        return reg

    def do_fill(zi, z, lvl_name, lvl, fdt, fpx, reg):
        """Cree le cycle. Retourne la cle, ou None si bloque (OR du jour manquante
        ou fill avant 14h35 UTC en mode or5)."""
        nonlocal cid, refused_or
        key = (zi, lvl_name)
        ref = z.atr_ref
        if range_mode == "or5":
            if (fdt.hour, fdt.minute) < OR_COMPLETE_HM:
                refused_or += 1
                return None
            ref = (orr or {}).get(fdt.date())
            if ref is None or ref <= 0:
                refused_or += 1
                return None
        qty = unit_qty(ref, unit_risk, pt_value)
        cid += 1
        if eod_time is not None:
            # Flat EOD (variante H7) : sortie forcee au plus tard sur la
            # derniere barre du jour d'entree (-1s pour declencher dessus).
            exp_dt = datetime.combine(fdt.date(), eod_time) - timedelta(seconds=1)
        else:
            exp_dt = fdt + timedelta(hours=expiry_hours)
        open_cyc[key] = {
            "id": cid, "zone_trend": z.trend_date, "level": lvl_name, "level_px": lvl,
            "qty": qty, "atr": ref, "fills": [fpx], "avg": fpx, "last_fill": fpx,
            "entry_t": fdt, "exp_day": fdt.date() + timedelta(days=EXPIRY_D),
            "exp_dt": exp_dt,
            "mfe": 0.0, "mae": 0.0, "regime": reg, "min_latent": 0.0,
        }
        fills_by_regime[reg] += 1
        last_entry_day[key] = fdt.date()
        legs.append((cid, 1, fdt, fpx, qty, "ENTRY"))
        return key

    def handle_touch(zi, z, lvl_name, lvl):
        """Logique d'entree apres touche averee (partagee boucle classique / indexee).
        Anti-refill (mode no-expiry uniquement) : si le prix est installe de
        l'autre cote de TOUTE la zone (ni reclaim intrabarre), la touche est
        ignoree. En mode expiry, l'expiry borne deja les refills : regle inactive
        pour preserver les baselines verifiees a l'octet."""
        nonlocal refused_concur
        key = (zi, lvl_name)
        if key in open_cyc or last_entry_day.get(key) == d:
            return
        if max_open_cycles > 0 and len(open_cyc) >= max_open_cycles:
            refused_concur += 1
            return
        if no_expiry and c_prev is not None:
            if side == "long" and c_prev < z.val and c < z.val:
                return
            if side == "short" and c_prev > z.vah and c > z.vah:
                return
        if entry_mode == "immediate":
            reg = gate_ok(dt, d, c, z.trend_date, dt.hour, z.trend_dt)
            if reg is None:
                return
            fill = min(o, lvl) if side == "long" else max(o, lvl)
            do_fill(zi, z, lvl_name, lvl, dt, fill, reg)
        elif entry_mode == "confirm":
            reg = gate_ok(dt, d, c, z.trend_date, dt.hour, z.trend_dt)
            if reg is None:
                return
            armed[key] = reg  # arme : fill au prochain close 5-min du bon cote
        elif entry_mode == "second-touch":
            ck = (zi, lvl_name, d)
            touch_count[ck] = touch_count.get(ck, 0) + 1
            if touch_count[ck] < 2:
                return
            reg = gate_ok(dt, d, c, z.trend_date, dt.hour, z.trend_dt)
            if reg is None:
                return
            fill = min(o, lvl) if side == "long" else max(o, lvl)
            do_fill(zi, z, lvl_name, lvl, dt, fill, reg)
        elif entry_mode == "delay":
            if key in pending:
                return  # premiere touche fait foi
            reg = gate_ok(dt, d, c, z.trend_date, dt.hour, z.trend_dt)
            if reg is None:
                return
            pending[key] = dt + timedelta(minutes=entry_delay_min)
            pending_reg[key] = reg

    def tp_safeguard(key, tc, now_bi):
        """Apres un fill antidate (confirm), verifie le TP touche entre tc et now."""
        cy = open_cyc.get(key)
        if cy is None:
            return
        tp = cy["avg"] + sgn * tp_mult * cy["atr"]
        j = now_bi
        while j >= 0 and bars_1min[j][0] > tc:
            _dt, _o, h, l, _c, _v = bars_1min[j]
            if (h >= tp) if side == "long" else (l <= tp):
                cy.update(exit_t=_dt, exit_px=tp, reason="TP")
                return
            j -= 1

    zi_start = 0
    nz = len(zones)
    active: Dict[int, ZoneSignal] = {}
    c_prev = None  # close 1-min precedent (filtre anti-refill)
    exp_delta = timedelta(days=EXPIRY_D)
    exp_h = timedelta(hours=expiry_hours)
    import bisect as _bidx
    lvl_index: list = []  # (prix, zi, lvl) trie — utilise en mode no-expiry uniquement

    def zone_active(z, dt, d):
        if hourly and z.trend_dt is not None:
            return z.trend_dt < dt <= z.trend_dt + exp_h
        return z.trend_date < d <= z.trend_date + exp_delta

    def zone_started(z, dt, d):
        if hourly and z.trend_dt is not None:
            return z.trend_dt < dt
        return z.trend_date < d

    def zone_expired(z, dt, d):
        if hourly and z.trend_dt is not None:
            return dt > z.trend_dt + exp_h
        return d > z.trend_date + exp_delta
    for bi, (dt, o, h, l, c, v) in enumerate(bars_1min):
        d = dt.date()
        roll_developing(dt, h, l, v)
        roll_vwap(dt, h, l, c, v)
        while zi_start < nz and zone_started(zones[zi_start], dt, d):
            znew = zones[zi_start]
            active[zi_start] = znew
            if no_expiry and znew.has_zone and znew.val < znew.vah:
                _bidx.insort(lvl_index, (znew.val, zi_start, "VAL"))
                _bidx.insort(lvl_index, (znew.vah, zi_start, "VAH"))
            zi_start += 1
        if not no_expiry:
            for k in [k for k, z in active.items() if zone_expired(z, dt, d)]:
                del active[k]
        # --- 1+2+4) boucle unique positions ouvertes : TP touche, renforts,
        # latent/urgence/expiry, MAE/MFE (fusion exacte des 3 anciennes boucles) ---
        done_keys = []
        _ptv = pt_value
        _em = abs(emerg_dollars)
        _eam = emerg_atr_mult
        _is_long = side == "long"
        for key, cy in open_cyc.items():
            _avg = cy["avg"]
            _atr = cy["atr"]
            _fills = cy["fills"]
            _qty = cy["qty"]
            tp = _avg + sgn * tp_mult * _atr
            if (h >= tp) if _is_long else (l <= tp):
                cy.update(exit_t=dt, exit_px=tp, reason="TP")
                done_keys.append(key)
                continue
            rung = cy["last_fill"] - sgn * _atr
            if ((c <= rung) if _is_long else (c >= rung)) and len(_fills) < 1 + MAX_ADDS:
                _fills.append(c)
                legs.append((cy["id"], len(_fills), dt, c, _qty, "ADD"))
                n = len(_fills)
                _avg = sum(_fills) / n
                cy["avg"] = _avg
                cy["last_fill"] = c
                tp = _avg + sgn * tp_mult * _atr
                if (h >= tp) if _is_long else (l <= tp):
                    cy.update(exit_t=dt, exit_px=tp, reason="TP")
                    done_keys.append(key)
                    continue
            # latent au close (+ tracking du pire latent intra-cycle)
            latent = 0.0
            for f in _fills:
                latent += sgn * (c - f)
            latent *= _ptv * _qty
            if latent < cy["min_latent"]:
                cy["min_latent"] = latent
            _thr = (_eam * _atr * _qty * _ptv) if _eam > 0 else _em
            if latent <= -_thr:
                cy.update(exit_t=dt, exit_px=c, reason="Emergency")
                done_keys.append(key)
                continue
            if not no_expiry and ((dt > cy["exp_dt"]) if hourly else (dt.date() > cy["exp_day"])):
                cy.update(exit_t=dt, exit_px=c, reason="Expired")
                done_keys.append(key)
                continue
            f0 = _fills[0]
            if _is_long:
                _mfe = (h - f0) / tick
                if _mfe > cy["mfe"]:
                    cy["mfe"] = _mfe
                _mae = (f0 - l) / tick
                if _mae > cy["mae"]:
                    cy["mae"] = _mae
            else:
                _mfe = (f0 - l) / tick
                if _mfe > cy["mfe"]:
                    cy["mfe"] = _mfe
                _mae = (h - f0) / tick
                if _mae > cy["mae"]:
                    cy["mae"] = _mae
        for key in done_keys:
            cy = open_cyc.pop(key)
            n = len(cy["fills"])
            cost = n * cy["qty"] * cost_rt
            net = sum((sgn * (cy["exit_px"] - f)) * pt_value * cy["qty"] for f in cy["fills"]) - cost
            cy["mfe_t"], cy["mae_t"] = round(cy.pop("mfe"), 1), round(cy.pop("mae"), 1)
            cy["cost_dollar"] = round(cost, 2)
            cycles.append({**cy, "n_units": n, "net_dollar": round(net, 2)})
        # --- 3a) confirm : traite les closes 5-min (fills au close confirme) ---
        pre_keys = set(open_cyc)
        if entry_mode == "confirm":
            while i5 < len(bars_5) and bars_5[i5][0] <= dt:
                tc, _o5, _h5, _l5, c5, _v5 = bars_5[i5]
                for akey in [k for k in armed if k not in open_cyc]:
                    zi, lvl_name = akey
                    z = active.get(zi)
                    if z is None:
                        armed.pop(akey, None)
                        continue
                    lvl = z.val if lvl_name == "VAL" else z.vah
                    if last_entry_day.get(akey) == tc.date():
                        continue
                    ok = (c5 > lvl) if side == "long" else (c5 < lvl)
                    if ok:
                        if min_zone_age > 0:
                            too_young = ((tc - z.trend_dt).total_seconds() / 3600 < min_zone_age
                                         if (hourly and z.trend_dt is not None)
                                         else (tc.date() - z.trend_date).days < min_zone_age)
                            if too_young:
                                refused_age += 1
                                armed.pop(akey, None)
                                continue
                        if session_filter != "all":
                            is_rth = 14 <= tc.hour <= 20
                            if (session_filter == "on" and is_rth) or (session_filter == "rth" and not is_rth):
                                refused_session += 1
                                armed.pop(akey, None)
                                continue
                        reg = armed.pop(akey)
                        key = do_fill(zi, z, lvl_name, lvl, tc, c5, reg)
                        tp_safeguard(key, tc, bi)
                i5 += 1
        # --- 3b) delay : declenche les fills programmes ---
        if entry_mode == "delay":
            for pkey in [k for k, ft in pending.items() if ft <= dt]:
                zi, lvl_name = pkey
                z = active.get(zi)
                pending.pop(pkey, None)
                if z is None or pkey in open_cyc or last_entry_day.get(pkey) == d:
                    continue
                lvl = z.val if lvl_name == "VAL" else z.vah
                do_fill(zi, z, lvl_name, lvl, dt, c, pending_reg.pop(pkey, "both"))
        # --- 3c) nouveaux fills / armements sur touches ---
        if start_dt is not None and dt < start_dt:
            c_prev = c
            continue
        if entry_mode == "h7":
            # Variante H7-DCA-OR5 : entree unique market au reclaim
            # (trend_dt / entry_px du signal). Pas de limite sur VAL/VAH ;
            # renforts + TP + urgence + flat EOD standard ensuite.
            hz = h7_by_dt.get(dt)
            if hz is not None:
                zi, z = hz
                key = (zi, "H7")
                if key not in open_cyc and last_entry_day.get(key) != d:
                    reg = gate_ok(dt, d, c, z.trend_date, dt.hour, z.trend_dt)
                    if reg is not None:
                        fpx = z.entry_px
                        if fpx is None or (isinstance(fpx, float) and math.isnan(fpx)):
                            fpx = c
                        do_fill(zi, z, "H7", fpx, dt, fpx, reg)
        if no_expiry:
            # touches = niveaux actifs du bon cote de la barre (exact, indexe)
            if side == "long":
                pos = _bidx.bisect_left(lvl_index, (l, -1, ""))
                cands = lvl_index[pos:]
            else:
                pos = _bidx.bisect_right(lvl_index, (h, 10 ** 12, ""))
                cands = lvl_index[:pos]
            for (_lv, zi, lvl_name) in cands:
                z = active[zi]
                handle_touch(zi, z, lvl_name, z.val if lvl_name == "VAL" else z.vah)
        else:
            for zi, z in active.items():
                for lvl_name, lvl in (("VAL", z.val), ("VAH", z.vah)):
                    touched = (l <= lvl) if side == "long" else (h >= lvl)
                    if not touched:
                        continue
                    handle_touch(zi, z, lvl_name, lvl)
        # --- 4) MAE/MFE des cycles NES ce tour (l'ancienne boucle couvrait aussi
        # les survivants, desormais traites dans la boucle unique 1+2+4) ---
        for key, cy in open_cyc.items():
            if key in pre_keys:
                continue
            f0 = cy["fills"][0]
            cy["mfe"] = max(cy["mfe"], sgn * (h - f0) / tick)
            cy["mae"] = max(cy["mae"], sgn * (f0 - l) / tick)
        c_prev = c

    # fin de donnees : cloture au dernier close
    if bars_1min:
        dt, _o, _h, _l, c, _v = bars_1min[-1]
        for key, cy in list(open_cyc.items()):
            cy.update(exit_t=dt, exit_px=c, reason="EOD")
            n = len(cy["fills"])
            cost = n * cy["qty"] * cost_rt
            net = sum((sgn * (c - f)) * pt_value * cy["qty"] for f in cy["fills"]) - cost
            cy["mfe_t"], cy["mae_t"] = round(cy.pop("mfe"), 1), round(cy.pop("mae"), 1)
            cy["cost_dollar"] = round(cost, 2)
            cycles.append({**cy, "n_units": n, "net_dollar": round(net, 2)})
        open_cyc.clear()

    cycles.sort(key=lambda r: r["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    # drift +15min post-fill (bleed d'entree) : close de la 1re barre >= t+15min
    import bisect as _bisect
    _dts = [b[0] for b in bars_1min]
    drift_of = {}
    for (cid_, leg, t, px, _q, kind) in legs:
        if kind != "ENTRY":
            continue
        j = _bisect.bisect_left(_dts, t + timedelta(minutes=15))
        if j < len(bars_1min):
            drift_of[cid_] = round(sgn * (bars_1min[j][4] - px) / tick, 1)
    with (out_dir / "cycles.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "zone_trend", "level", "level_px", "qty", "atr", "n_units",
                    "entry_time", "avg_entry", "exit_time", "exit_px", "reason",
                    "net_dollar", "cost_dollar", "mfe_t", "mae_t", "regime", "min_latent_d"])
        for cy in cycles:
            w.writerow([cy["id"], cy["zone_trend"], cy["level"], round(cy["level_px"], 2),
                        cy["qty"], round(cy["atr"], 2), cy["n_units"],
                        cy["entry_t"].strftime("%Y-%m-%d %H:%M"), round(cy["avg"], 2),
                        cy["exit_t"].strftime("%Y-%m-%d %H:%M"), round(cy["exit_px"], 2),
                        cy["reason"], cy["net_dollar"], cy.get("cost_dollar", 0.0),
                        cy["mfe_t"], cy["mae_t"],
                        cy.get("regime", "both"), round(cy.get("min_latent", 0.0), 2)])
    legs.sort(key=lambda r: (r[0], r[1]))
    with (out_dir / "legs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["cycle", "leg", "time", "price", "qty", "kind", "drift15_t"])
        for cid_, leg, t, px, q, kind in legs:
            w.writerow([cid_, leg, t.strftime("%Y-%m-%d %H:%M"), round(px, 2), q, kind,
                        drift_of.get(cid_, "") if kind == "ENTRY" else ""])
    return {"cycles": cycles, "nzones": len(zones),
            "refused_warmup": refused_warmup, "refused_regime": refused_regime,
            "refused_session": refused_session, "refused_age": refused_age,
            "refused_vwap": refused_vwap, "refused_or": refused_or,
            "refused_concur": refused_concur,
            "fills_by_regime": dict(fills_by_regime),
            "avg_drift15": round(sum(drift_of.values()) / len(drift_of), 1) if drift_of else 0.0}


def write_stats(out_dir: pathlib.Path, res: dict, side: str, period: str, n_sat: int, n_sun: int, n_dropped: int,
                monthly_filter: str = "none", regime_filter: str = "none",
                emerg_dollars: float = 40000.0, entry_mode: str = "immediate",
                min_zone_age: int = 0, session_filter: str = "all",
                timeframe: str = "D1", expiry_hours: float = 10.0,
                dailyopen_filter: str = "none", dayregime_filter: str = "none",
                no_expiry: bool = False, rthopen_filter: str = "none",
                atr_mult: float = 1.0, detector: str = "initial",
                vwap_filter: str = "none", vwap_anchor: str = "daily",
                pt_value: float = 20.0, cost_rt: float = 0.0,
                 unit_risk: float = 10_000.0, inst: str = "NQ",
                 eod_time=None, range_mode: str = "atr",
                 tp_mult: float = 1.0, max_open_cycles: int = 0,
                 emerg_atr_mult: float = 0.0):
    cycles = res["cycles"]
    n = len(cycles)
    with (out_dir / "stats.md").open("w", encoding="utf-8") as f:
        det_label = {"accept": "AcceptZone", "reverse": "ReverseZone", "fade": "FadeZone", "h7": "H7-DCA-OR5"}.get(detector, "InitialZone")
        f.write(f"# {det_label} DCA {timeframe} — {inst} {side.upper()} (limite au niveau, renforts 1xATR cap 3, TP avg+{tp_mult:g}ATR, urgence cycle -${emerg_dollars:,.0f}{f' (ATR x{emerg_atr_mult:g})' if emerg_atr_mult > 0 else ''})\n\n")
        f.write(f"**Periode 1-min:** {period}  \n")
        if detector == "h7":
            exp_txt = "EOD15:00" if eod_time is None else "EOD" + eod_time.strftime("%H:%M")
        else:
            exp_txt = "off" if no_expiry else ('%gh' % expiry_hours if timeframe == 'H1' else f'{EXPIRY_D}j')
        f.write(f"**Params:** TF={timeframe} atrmult={atr_mult} tp={tp_mult:g} range={range_mode} maxopen={max_open_cycles} unite=1 %/ref ({unit_risk:.0f} $) {inst} {pt_value:.0f} $/pt costRT={cost_rt:.2f} $ max adds={MAX_ADDS} expiry={exp_txt} mfilter={monthly_filter} dopen={dailyopen_filter} ropen={rthopen_filter} rfilter={regime_filter} dreg={dayregime_filter} vwap={vwap_anchor}:{vwap_filter} entry={entry_mode} minage={min_zone_age}{'h' if timeframe == 'H1' else 'j'} sess={session_filter}  \n")
        f.write(f"**Data quality:** {n_sat} sam + {n_sun} dim + {n_dropped} corrompues ecartees (fichier UTC)  \n")
        f.write(f"**Zones {side} (bull={side == 'long'}):** {res['nzones']} | **cycles:** {n}\n\n")
        if not n:
            f.write("Aucun cycle.\n")
            return
        wins = [r for r in cycles if r["net_dollar"] > 0]
        net = sum(r["net_dollar"] for r in cycles)
        avg = net / n
        var = sum((r["net_dollar"] - avg) ** 2 for r in cycles) / (n - 1) if n > 1 else 0
        tstat = avg / math.sqrt(var / n) if var > 0 else 0.0
        gw = sum(r["net_dollar"] for r in wins)
        gl = abs(sum(r["net_dollar"] for r in cycles if r["net_dollar"] < 0))
        reasons = defaultdict(int)
        adds = defaultdict(int)
        for r in cycles:
            reasons[r["reason"]] += 1
            adds[r["n_units"] - 1] += 1
        f.write(f"| n | WR% | Net $ | Avg $ | t | PF | TP/Emerg/Exp/EOD | adds 0/1/2/3 |\n")
        f.write(f"|---|---|---|---|---|---|---|---|\n")
        f.write(f"| {n} | {100 * len(wins) / n:.1f} | {net:,.0f} | {avg:,.0f} | {tstat:.2f} | "
                f"{(gw / gl if gl else float('inf')):.2f} | {reasons.get('TP', 0)}/{reasons.get('Emergency', 0)}/"
                f"{reasons.get('Expired', 0)}/{reasons.get('EOD', 0)} | {adds.get(0, 0)}/{adds.get(1, 0)}/{adds.get(2, 0)}/{adds.get(3, 0)} |\n")
        cap_ref = unit_risk * 100.0  # unite = 1 % du capital de reference
        f.write(f"\n**Equity finale theorique ({cap_ref:,.0f} + net, sans compounding) :** {cap_ref + net:,.0f} $  \n")
        if regime_filter != "none" or vwap_filter != "none" or res.get("refused_warmup") or res.get("refused_regime") or res.get("refused_session") or res.get("refused_age") or res.get("refused_vwap"):
            fbr = res.get("fills_by_regime", {})
            f.write(f"**Fills par regime a l'entree :** buy={fbr.get('buy', 0)} sell={fbr.get('sell', 0)} "
                    f"both={fbr.get('both', 0)}  \n")
            f.write(f"**Fills refuses :** warmup J1-J3={res.get('refused_warmup', 0)} regime={res.get('refused_regime', 0)} "
                    f"session={res.get('refused_session', 0)} age={res.get('refused_age', 0)} vwap={res.get('refused_vwap', 0)} or={res.get('refused_or', 0)} concur={res.get('refused_concur', 0)}  \n")
        f.write(f"**MAE~ moyen :** {sum(r['mae_t'] for r in cycles) / n:.0f} ticks | **MFE~ moyen :** {sum(r['mfe_t'] for r in cycles) / n:.0f} ticks | "
                f"**drift +15min moyen (ENTRY) :** {res.get('avg_drift15', 0.0):.0f} ticks  \n")
        f.write("\n## 20 premiers cycles\n\n")
        f.write("| # | Zone | Niv | Qty | Adds | Entree | Avg | Sortie | Motif | Net $ |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for r in cycles[:20]:
            f.write(f"| {r['id']} | {r['zone_trend']} | {r['level']} {r['level_px']:.0f} | {r['qty']} | {r['n_units'] - 1} | "
                    f"{r['entry_t'].strftime('%m-%d %H:%M')} | {r['avg']:.1f} | {r['exit_t'].strftime('%m-%d %H:%M')} {r['exit_px']:.1f} | "
                    f"{r['reason']} | {r['net_dollar']:,.0f} |\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="donnees/market")
    ap.add_argument("--out-dir", default="backtest_out/initzone_dca")
    ap.add_argument("--side", default="both", choices=["long", "short", "both"])
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--tick", type=float, default=0.25)
    ap.add_argument("--atr-mult", type=float, default=1.0)
    ap.add_argument("--tp-mult", type=float, default=1.0)
    ap.add_argument("--max-open-cycles", type=int, default=0)
    ap.add_argument("--monthly-filter", default="none", choices=["none", "long", "short", "both"])
    ap.add_argument("--regime-filter", default="none", choices=["none", "long", "short", "both"])
    ap.add_argument("--emerg-dollars", type=float, default=40000.0)
    ap.add_argument("--emerg-atr-mult", type=float, default=0.0)
    ap.add_argument("--entry-mode", default="immediate", choices=["immediate", "confirm", "second-touch", "delay", "h7"])
    ap.add_argument("--entry-delay-min", type=int, default=15)
    ap.add_argument("--min-zone-age", type=int, default=0)
    ap.add_argument("--session-filter", default="all", choices=["all", "rth", "on"])
    ap.add_argument("--timeframe", default="daily", choices=["daily", "h1"])
    ap.add_argument("--expiry-hours", type=float, default=10.0)
    ap.add_argument("--dailyopen-filter", default="none", choices=["none", "long", "short", "both"])
    ap.add_argument("--rthopen-filter", default="none", choices=["none", "long", "short", "both"])
    ap.add_argument("--vwap-filter", default="none", choices=["none", "long", "short", "both"])
    ap.add_argument("--vwap-anchor", default="monthly", choices=["daily", "monthly", "eth"])
    ap.add_argument("--range-mode", default="atr", choices=["atr", "or5"])
    ap.add_argument("--dayregime-filter", default="none", choices=["none", "long", "short", "both"])
    ap.add_argument("--no-expiry", action="store_true")
    ap.add_argument("--detector", default="initial", choices=["initial", "accept", "reverse", "fade", "h7"])
    ap.add_argument("--inst", default="NQ", choices=["NQ", "ES"])
    ap.add_argument("--pt-value", type=float, default=20.0)
    ap.add_argument("--cost-rt", type=float, default=0.0)
    ap.add_argument("--unit-risk", type=float, default=10000.0)
    ap.add_argument("--eod-time", default=None, help='"HH:MM" flat EOD file (defaut 15:00, variante H7)')
    a = ap.parse_args()
    data_dir, base_out = pathlib.Path(a.data_dir), pathlib.Path(a.out_dir)
    start, end = parse_dt(a.start), parse_dt(a.end)

    if a.detector == "h7":
        # Variante H7-DCA-OR5 : signaux + barres RTH issus du fige (pas de
        # clean_bars : jours deja valides MIN_RTH_BARS, pas de filtre corrupt).
        if a.no_expiry:
            ap.error("--detector h7 interdit avec --no-expiry (flat EOD obligatoire)")
        if a.entry_mode != "h7":
            ap.error("--detector h7 exige --entry-mode h7")
        from .detect_h7 import detect_h7, rth_close_time
        signals, bars, meta = detect_h7(a.inst)
        if start:
            bars = [b for b in bars if b[0] >= start]
            signals = [z for z in signals if z.trend_dt is not None and z.trend_dt >= start]
        if end:
            dend = end if isinstance(end, datetime) and end.time() != time.min else \
                datetime.combine(end.date() if isinstance(end, datetime) else end, time.max)
            bars = [b for b in bars if b[0] <= dend]
            signals = [z for z in signals if z.trend_dt is not None and z.trend_dt <= dend]
        print(f"[H7] {a.inst}: {meta['n_days']}j RTH {meta['first']}->{meta['last']} "
              f"signaux={meta['n_signals']} valides={meta['n_valid']} "
              f"sans-reclaim={meta['n_noreclaim']} lastbar={meta['n_lastbar']} "
              f"bars={len(bars)}", flush=True)
        eod = rth_close_time()
        if a.eod_time:
            _hh, _mm = a.eod_time.split(":")
            eod = time(int(_hh), int(_mm), 0)
        period = f"{bars[0][0].date()} -> {bars[-1][0].date()}" if bars else "vide"
        sides = ["long", "short"] if a.side == "both" else [a.side]
        for side in sides:
            out = base_out / side
            res = run_side(bars, signals, side, out, a.tick, start, {},
                           "none", None, "none", a.emerg_dollars, 0.0,
                           "h7", a.entry_delay_min, 0, "all",
                           True, a.expiry_hours, "none", None, None, "none",
                           False, "none", None, "none", "daily",
                           a.pt_value, a.cost_rt, a.unit_risk, eod)
            write_stats(out, res, side, period, 0, 0, 0, "none", "none",
                        a.emerg_dollars, "h7", 0, "all", "H1", a.expiry_hours,
                        "none", "none", False, "none", 1.0, "h7", "none", "daily",
                        a.pt_value, a.cost_rt, a.unit_risk, a.inst, eod)
            print(f"[DONE-H7-{side}] {a.inst} cycles={len(res['cycles'])} "
                  f"emerg={a.emerg_dollars:.0f} unitrisk={a.unit_risk:.0f} "
                  f"pt={a.pt_value:.0f} costRT={a.cost_rt:.2f} eod={eod.strftime('%H:%M')} "
                  f"drift15={res['avg_drift15']} -> {out}", flush=True)
        return

    s = start or datetime.min
    pre = (s - timedelta(days=60)) if isinstance(s, datetime) and s > datetime.min + timedelta(days=61) else datetime.min
    e = end or datetime.max
    paths = [data_dir / name for name, (f0, f1) in sorted(FILE_RANGES.items(), key=lambda kv: kv[1][0])
             if (data_dir / name).exists() and f1 >= pre and f0 <= e]
    if not paths:
        paths = sorted(data_dir.glob("NQ *.Last.txt"))
    print(f"[LOAD] files: {[p.name for p in paths]}", flush=True)
    bars = load_and_merge(paths)
    if start:
        bars = [b for b in bars if b[0] >= (start - timedelta(days=60))]
    if end:
        dend = end if isinstance(end, datetime) and end.time() != time.min else \
            datetime.combine(end.date() if isinstance(end, datetime) else end, time.max)
        bars = [b for b in bars if b[0] <= dend]
    print(f"[LOAD] 1min: {len(bars)}  {bars[0][0]} -> {bars[-1][0]}", flush=True)
    bars, n_sat, n_sun, n_dropped = clean_bars(bars)
    print(f"[CLEAN] samedis: {n_sat} | dimanches: {n_sun} | corrompues: {n_dropped}", flush=True)
    hourly = a.timeframe == "h1"
    if a.detector in ("accept", "reverse", "fade"):
        if hourly:
            ap.error("--detector accept/reverse/fade est daily-only (pas de H1)")
        if a.detector == "reverse":
            signals, _ = detect_reverse(bars, tick_size=a.tick)
        elif a.detector == "fade":
            signals, _ = detect_fade(bars, tick_size=a.tick)
        else:
            signals, _ = detect_accept(bars, tick_size=a.tick)
    elif hourly:
        from .detector import detect_tf as _dtf
        signals, _ = _dtf(bars, 60, 20, a.atr_mult, a.tick, 4, 70.0, "H1")
    else:
        signals, _ = detect_daily(bars, tick_size=a.tick)
    print(f"[SIG] {a.timeframe}: {len(signals)} (bull={sum(1 for z in signals if z.bullish)})", flush=True)
    period = f"{bars[0][0].date()} -> {bars[-1][0].date()}"

    sides = ["long", "short"] if a.side == "both" else [a.side]
    mo = monthly_opens(bars)
    do_d = daily_opens(bars) if a.dailyopen_filter != "none" else None
    ro_d = rth_opens(bars) if a.rthopen_filter != "none" else None
    mva = monthly_va(bars, tick_size=a.tick) if a.regime_filter != "none" else None
    if mva is not None:
        print(f"[MVA] mois caches: {len(mva)}", flush=True)
    dva = daily_va(bars, tick_size=a.tick) if a.dayregime_filter != "none" else None
    if dva is not None:
        print(f"[DVA] jours caches: {len(dva)}", flush=True)
    orr = or5_ranges(bars) if a.range_mode == "or5" else None
    if orr is not None:
        import statistics as _st
        print(f"[OR5] jours caches: {len(orr)} | ORR med: {round(_st.median(orr.values()), 2)}", flush=True)
    for side in sides:
        out = base_out / side
        mf = a.monthly_filter if a.monthly_filter in (side, "both", "none") else "none"
        rf = a.regime_filter if a.regime_filter in (side, "both", "none") else "none"
        df = a.dailyopen_filter if a.dailyopen_filter in (side, "both", "none") else "none"
        drf = a.dayregime_filter if a.dayregime_filter in (side, "both", "none") else "none"
        rof = a.rthopen_filter if a.rthopen_filter in (side, "both", "none") else "none"
        vwf = a.vwap_filter if a.vwap_filter in (side, "both", "none") else "none"
        res = run_side(bars, signals, side, out, a.tick, start, mo, mf, mva, rf, a.emerg_dollars, a.emerg_atr_mult,
                       a.entry_mode, a.entry_delay_min, a.min_zone_age, a.session_filter,
                       hourly, a.expiry_hours, df, do_d, dva, drf, a.no_expiry, rof, ro_d,
                       vwf, a.vwap_anchor, a.pt_value, a.cost_rt, a.unit_risk, None,
                       a.range_mode, orr, a.tp_mult, a.max_open_cycles)
        write_stats(out, res, side, period, n_sat, n_sun, n_dropped, mf, rf, a.emerg_dollars, a.entry_mode,
                    a.min_zone_age, a.session_filter, "H1" if hourly else "D1", a.expiry_hours, df, drf, a.no_expiry, rof, a.atr_mult, a.detector,
                    vwf, a.vwap_anchor, a.pt_value, a.cost_rt, a.unit_risk, a.inst, None, a.range_mode, a.tp_mult, a.max_open_cycles, a.emerg_atr_mult)
        print(f"[DONE-{side}] cycles={len(res['cycles'])} mfilter={mf} dopen={df} rfilter={rf} dreg={drf} emerg={a.emerg_dollars:.0f} emAtr={a.emerg_atr_mult:g} "
              f"entry={a.entry_mode} minage={a.min_zone_age} sess={a.session_filter} tf={a.timeframe} atrmult={a.atr_mult} tp={a.tp_mult:g} noexp={int(a.no_expiry)} vwap={a.vwap_anchor}:{vwf} range={a.range_mode} drift15={res['avg_drift15']} -> {out}", flush=True)


if __name__ == "__main__":
    main()
