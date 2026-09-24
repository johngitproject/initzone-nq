"""Backtest V1 — Buy InitialZone Daily : touche + 2 closes 5-min > L+BUF => BUY.

Spec figee :
- Zones Daily bullish avec VA (detector.py, miroir MGIHtfInitialZone.cs).
- Niveaux : VAL et VAH bruts. Seuil d'entree L* = L + BUF ticks (defaut 2, NQ 0.25).
- Touche (5-min, resample floor convention open) : low <= L*, arrivee above
  (close prec > L*) ou inside (close prec dans [VAL,VAH]) ; arrivee below exclue.
- Confirmation : 2 closes 5-min consecutives > L*. Entree buy market au 2e close.
- TP = entree + 1x ATR Daily (atr_ref fige a la detection, Wilder non-biaise).
- SL = premiere close 1H (horloge) < L BRUT (sans buffer). Egalite de temps TP/SL : TP gagne.
- Activation : barres 5-min de date > trend_date. Expiry zone/entree : +10 jours calendaires.
- 1 contrat par signal, signaux chevauchants autorises, ticks bruts sans frais.

Usage :
  venv\\Scripts\\python -m python.mgi_initialzone.reject2_atr --start 2025-01-01 --end 2025-01-31 --out-dir backtest_out\\initzone_reject2\\smoke_jan
  venv\\Scripts\\python -m python.mgi_initialzone.reject2_atr --out-dir backtest_out\\initzone_reject2\\full_2023_2025
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
from .detector import ZoneSignal, clean_bars, detect_daily

# Plages approximatives des fichiers (rollover = vendredi d'expiration - 8j).
# Bornes 2022 relevees des fichiers (first/last bars) le 2026-09-20 :
# 03-22: 2021-12-16 -> 2022-03-09 ; 06-22: 2022-03-17 -> 2022-06-08 ;
# 09-22: 2022-06-16 -> 2022-09-07 ; 12-22: 2022-09-15 -> 2022-12-09.
# Trous semaine d'expiration : 10-16 mar, 9-15 juin, 8-14 sep, 10-17 dec 2022.
FILE_RANGES = {
    "NQ 03-22.Last.txt": (datetime(2021, 12, 16), datetime(2022, 3, 9, 23, 59)),
    "NQ 06-22.Last.txt": (datetime(2022, 3, 9), datetime(2022, 6, 8, 23, 59)),
    "NQ 09-22.Last.txt": (datetime(2022, 6, 8), datetime(2022, 9, 7, 23, 59)),
    "NQ 12-22.Last.txt": (datetime(2022, 9, 7), datetime(2022, 12, 9, 23, 59)),
    "NQ 03-23.Last.txt": (datetime(2022, 12, 1), datetime(2023, 3, 9, 23, 59)),
    "NQ 06-23.Last.txt": (datetime(2023, 3, 9), datetime(2023, 6, 8, 23, 59)),
    "NQ 09-23.Last.txt": (datetime(2023, 6, 8), datetime(2023, 9, 7, 23, 59)),
    "NQ 12-23.Last.txt": (datetime(2023, 9, 7), datetime(2023, 12, 7, 23, 59)),
    "NQ 03-24.Last.txt": (datetime(2023, 12, 7), datetime(2024, 3, 8, 23, 59)),
    "NQ 06-24.Last.txt": (datetime(2024, 3, 8), datetime(2024, 6, 13, 23, 59)),
    "NQ 09-24.Last.txt": (datetime(2024, 6, 13), datetime(2024, 9, 12, 23, 59)),
    "NQ 12-24.Last.txt": (datetime(2024, 9, 12), datetime(2024, 12, 12, 23, 59)),
    "NQ 03-25.Last.txt": (datetime(2024, 12, 12), datetime(2025, 3, 13, 23, 59)),
    "NQ 06-25.Last.txt": (datetime(2025, 3, 13), datetime(2025, 6, 12, 23, 59)),
    "NQ 09-25.Last.txt": (datetime(2025, 6, 12), datetime(2025, 9, 11, 23, 59)),
    "NQ 12-25.Last.txt": (datetime(2025, 9, 11), datetime(2025, 12, 11, 23, 59)),
}


def parse_dt(s: str | None):
    if s is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"bad date {s}")


def resample_floor(bars_1min: List[Tuple], step_min: int):
    """Resample convention open : opens [T,T+step) -> close time T+step."""
    grouped: Dict[datetime, list] = defaultdict(list)
    for dt, o, h, l, c, v in bars_1min:
        base = dt.replace(second=0, microsecond=0)
        mins = base.hour * 60 + base.minute
        bucket = (mins // step_min) * step_min
        bdt = datetime.combine(base.date(), time.min) + timedelta(minutes=bucket + step_min)
        grouped[bdt].append((dt, o, h, l, c, v))
    out = []
    for bdt in sorted(grouped.keys()):
        lst = sorted(grouped[bdt])
        out.append((bdt, lst[0][1], max(b[2] for b in lst), min(b[3] for b in lst),
                    lst[-1][4], sum(b[5] for b in lst)))
    return out


def run(data_dir: pathlib.Path, out_dir: pathlib.Path, start, end,
        tick: float = 0.25, buf_ticks: int = 2, expiry_days: int = 10,
        max_per_level: int = 1):
    out_dir.mkdir(parents=True, exist_ok=True)
    buf = buf_ticks * tick

    # --- fichiers utiles (fenetre + 60j de warmup pour ATR/warmup daily) ---
    s = start or datetime.min
    e = end or datetime.max
    pre = (s - timedelta(days=60)) if isinstance(s, datetime) and s > datetime.min + timedelta(days=61) else datetime.min
    paths = []
    for name, (f0, f1) in sorted(FILE_RANGES.items(), key=lambda kv: kv[1][0]):
        fp = data_dir / name
        if fp.exists() and f1 >= pre and f0 <= e:
            paths.append(fp)
    if not paths:
        paths = sorted(data_dir.glob("NQ *.Last.txt"))
    print(f"[LOAD] files: {[p.name for p in paths]}", flush=True)
    bars_1min = load_and_merge(paths)
    if start:
        bars_1min = [b for b in bars_1min if b[0] >= (start - timedelta(days=60))]
    if end:
        dend = end if isinstance(end, datetime) and end.time() != time.min else \
            datetime.combine(end.date() if isinstance(end, datetime) else end, time.max)
        bars_1min = [b for b in bars_1min if b[0] <= dend]
    print(f"[LOAD] 1min: {len(bars_1min)}  {bars_1min[0][0]} -> {bars_1min[-1][0]}", flush=True)
    bars_1min, n_sat, n_sun, n_dropped = clean_bars(bars_1min)
    print(f"[CLEAN] samedis: {n_sat} | dimanches: {n_sun} | corrompues: {n_dropped}", flush=True)

    # --- detection ---
    signals, _days = detect_daily(bars_1min, tick_size=tick)
    bull = [z for z in signals if z.bullish and z.has_zone]
    print(f"[SIG] daily: {len(signals)} dont bullish zones: {len(bull)}", flush=True)

    # --- resamples ---
    bars_5 = resample_floor(bars_1min, 5)
    bars_1h = resample_floor(bars_1min, 60)
    idx5 = {b[0]: i for i, b in enumerate(bars_5)}

    # --- signaux touche + 2 closes ---
    events = []  # dicts
    for z in bull:
        if z.val >= z.vah or math.isnan(z.val):
            continue
        active_from = z.trend_date + timedelta(days=1)
        active_to = z.trend_date + timedelta(days=expiry_days)
        for lvl_name, lvl in (("VAL", z.val), ("VAH", z.vah)):
            thr = lvl + buf  # L*
            i = 0
            # demarre a la premiere barre 5-min >= active_from
            while i < len(bars_5) and bars_5[i][0].date() < active_from:
                i += 1
            while i < len(bars_5):
                t, o, h, l, c, _v = bars_5[i]
                if t.date() > active_to:
                    break
                touched = l <= thr
                if touched and i >= 1:
                    c_prev = bars_5[i - 1][4]
                    if c_prev > thr:
                        arrival = "above"
                    elif z.val <= c_prev <= z.vah:
                        arrival = "inside"
                    else:
                        arrival = "below"
                    if arrival != "below" and i + 2 < len(bars_5):
                        c1 = bars_5[i + 1][4]
                        t2, _o2, _h2, _l2, c2, _v2 = bars_5[i + 2]
                        if c1 > thr and c2 > thr and t2.date() <= active_to:
                            events.append({
                                "zone_trend": z.trend_date.strftime("%Y-%m-%d"),
                                "level": lvl_name, "level_px": lvl,
                                "touch_time": t.strftime("%Y-%m-%d %H:%M"),
                                "arrival": arrival,
                                "entry_time": t2, "entry_px": c2,
                                "atr_ref": z.atr_ref,
                            })
                            if max_per_level <= 1:
                                break  # premier rebond seul (V1) : niveau suivant
                            i += 3
                            continue
                i += 1
    events.sort(key=lambda r: r["entry_time"])
    print(f"[EVT] signals buy: {len(events)}", flush=True)

    # --- trades : TP touche 1-min, SL close 1H < brut ---
    h1_closes = [(b[0], b[4]) for b in bars_1h]  # (close_time, close)
    m1 = [(b[0], b[2], b[3]) for b in bars_1min]  # (dt, high, low)
    trades = []
    for k, ev in enumerate(events, 1):
        et, entry, atr, lvl = ev["entry_time"], ev["entry_px"], ev["atr_ref"], ev["level_px"]
        tp = entry + atr
        # curseurs : premier index > entry_time
        j1 = next((j for j, (dt, _h, _l) in enumerate(m1) if dt > et), len(m1))
        jh = next((j for j, (dt, _c) in enumerate(h1_closes) if dt > et), len(h1_closes))
        expiry_end = datetime.combine(et.date() + timedelta(days=expiry_days), time.max)
        exit_px, exit_t, reason = None, None, None
        # plus proche SL (1H) : on avance chronologiquement
        p1, ph = j1, jh
        while True:
            t_tp = m1[p1][0] if p1 < len(m1) and m1[p1][0] <= expiry_end else None
            t_sl = h1_closes[ph][0] if ph < len(h1_closes) and h1_closes[ph][0] <= expiry_end else None
            if t_tp is None and t_sl is None:
                break
            # TP gagne les egalites : on teste le TP d'abord jusqu'a t_sl inclus
            limit = t_sl if t_sl is not None else t_tp
            hit = None
            while p1 < len(m1) and m1[p1][0] <= (limit if limit is not None else expiry_end):
                if m1[p1][1] >= tp:
                    hit = m1[p1][0]
                    break
                p1 += 1
            if hit is not None:
                exit_px, exit_t, reason = tp, hit, "TP"
                break
            if t_sl is not None:
                if h1_closes[ph][1] < lvl:  # SL brut strict
                    exit_px, exit_t, reason = h1_closes[ph][1], t_sl, "SL"
                    break
                ph += 1
                continue
            break
        if exit_px is None:
            # expiry : dernier close 1-min <= expiry_end
            tail = [b for b in bars_1min if b[0] > et and b[0] <= expiry_end]
            if tail:
                exit_px, exit_t, reason = tail[-1][4], tail[-1][0], "Expired"
            else:
                continue
        net_ticks = (exit_px - entry) / tick
        # MAE/MFE sur 1-min de la duree de vie
        seg = [(dt, h, l) for dt, h, l in m1 if et < dt <= exit_t]
        mfe = max([(h - entry) / tick for _, h, _l in seg], default=0.0)
        mae = max([(entry - l) / tick for _, _h, l in seg], default=0.0)
        trades.append({
            "id": k, "zone_trend": ev["zone_trend"], "level": ev["level"],
            "level_px": round(lvl, 2), "arrival": ev["arrival"],
            "entry_time": ev["entry_time"].strftime("%Y-%m-%d %H:%M"),
            "entry_px": round(entry, 2), "atr": round(atr, 2),
            "tp_px": round(tp, 2), "exit_time": exit_t.strftime("%Y-%m-%d %H:%M"),
            "exit_px": round(exit_px, 2), "reason": reason,
            "net_ticks": round(net_ticks, 1), "mfe": round(mfe, 1), "mae": round(mae, 1),
            "hold_h": round((exit_t - et).total_seconds() / 3600, 1),
        })

    # --- exports ---
    with (out_dir / "signals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["trend", "init", "bull", "vah", "poc", "val", "swing", "atr_ref", "body"])
        for z in signals:
            w.writerow([z.trend_date, z.init_date, int(z.bullish),
                        round(z.vah, 2) if z.has_zone else "", round(z.poc, 2) if z.has_zone else "",
                        round(z.val, 2) if z.has_zone else "", round(z.swing, 2),
                        round(z.atr_ref, 2), round(z.body, 2)])
    with (out_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["zone_trend", "level", "level_px", "touch_time", "arrival",
                    "entry_time", "entry_px", "atr_ref"])
        for ev in events:
            w.writerow([ev["zone_trend"], ev["level"], round(ev["level_px"], 2),
                        ev["touch_time"], ev["arrival"],
                        ev["entry_time"].strftime("%Y-%m-%d %H:%M"),
                        round(ev["entry_px"], 2), round(ev["atr_ref"], 2)])
    with (out_dir / "trades.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        cols = ["id", "zone_trend", "level", "level_px", "arrival", "entry_time", "entry_px",
                "atr", "tp_px", "exit_time", "exit_px", "reason", "net_ticks", "mfe", "mae", "hold_h"]
        w.writerow(cols)
        for t in trades:
            w.writerow([t[c] for c in cols])

    # --- stats ---
    def stat(rows):
        n = len(rows)
        if not n:
            return {"n": 0}
        wins = [r for r in rows if r["net_ticks"] > 0]
        gross_w = sum(r["net_ticks"] for r in wins)
        gross_l = abs(sum(r["net_ticks"] for r in rows if r["net_ticks"] < 0))
        avg = sum(r["net_ticks"] for r in rows) / n
        var = sum((r["net_ticks"] - avg) ** 2 for r in rows) / (n - 1) if n > 1 else 0
        tstat = avg / math.sqrt(var / n) if var > 0 else 0.0
        reasons = defaultdict(int)
        for r in rows:
            reasons[r["reason"]] += 1
        return {"n": n, "wr": round(100 * len(wins) / n, 1), "net": round(sum(r["net_ticks"] for r in rows), 1),
                "avg": round(avg, 1), "t": round(tstat, 2),
                "pf": round(gross_w / gross_l, 2) if gross_l else float("inf"),
                "reasons": dict(reasons),
                "mae_mean": round(sum(r["mae"] for r in rows) / n, 1),
                "mfe_mean": round(sum(r["mfe"] for r in rows) / n, 1)}

    groups = {"ALL": trades, "VAL": [t for t in trades if t["level"] == "VAL"],
              "VAH": [t for t in trades if t["level"] == "VAH"],
              "above": [t for t in trades if t["arrival"] == "above"],
              "inside": [t for t in trades if t["arrival"] == "inside"]}
    with (out_dir / "stats.md").open("w", encoding="utf-8") as f:
        period = f"{bars_1min[0][0].date()} -> {bars_1min[-1][0].date()}"
        f.write("# InitialZone Reject2+ATR — NQ buy (touche + 2 closes 5m > L+2t, TP 1xATR, SL close 1H < brut)\n\n")
        f.write(f"**Periode 1-min:** {period}  \n")
        f.write(f"**Params:** tick={tick} BUF={buf_ticks}t expiry={expiry_days}j max/level={max_per_level}  \n")
        f.write(f"**Data quality:** {n_sat} sam + {n_sun} dim + {n_dropped} corrompues ecartees  \n")
        f.write(f"**Conventions:** Daily=calendaire UTC (fichier UTC prouve : halt 21h UTC, RTH 13h30-20h UTC ; drift declare vs ETH CME NT8), "
                f"5-min/1H resample floor (timestamps=open), SL brut strict, TP gagne les egalites  \n")
        f.write(f"**Signaux daily:** {len(signals)} (bull zones: {len(bull)}) | **entries:** {len(events)} | **trades:** {len(trades)}\n\n")
        f.write("| Groupe | n | WR% | Net ticks | Avg | t | PF | TP/SL/Exp | MAE~ | MFE~ |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for g, rows in groups.items():
            s = stat(rows)
            if not s["n"]:
                f.write(f"| {g} | 0 | - | - | - | - | - | - | - | - |\n")
                continue
            r = s["reasons"]
            f.write(f"| {g} | {s['n']} | {s['wr']} | {s['net']} | {s['avg']} | {s['t']} | {s['pf']} | "
                    f"{r.get('TP',0)}/{r.get('SL',0)}/{r.get('Expired',0)} | {s['mae_mean']} | {s['mfe_mean']} |\n")
        f.write("\n## 20 premiers trades\n\n")
        f.write("| # | Zone | Niv | Arr | Entree | ATR | Sortie | Motif | Net ticks |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for t in trades[:20]:
            f.write(f"| {t['id']} | {t['zone_trend']} | {t['level']} {t['level_px']} | {t['arrival']} | "
                    f"{t['entry_time']} {t['entry_px']} | {t['atr']} | {t['exit_time']} {t['exit_px']} | "
                    f"{t['reason']} | {t['net_ticks']} |\n")
    print(f"[DONE] trades={len(trades)} -> {out_dir}", flush=True)
    return {"trades": trades, "events": events, "signals": signals}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="donnees/market")
    ap.add_argument("--out-dir", default="backtest_out/initzone_reject2")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--tick", type=float, default=0.25)
    ap.add_argument("--buf", type=int, default=2)
    ap.add_argument("--expiry-days", type=int, default=10)
    ap.add_argument("--max-per-level", type=int, default=1)
    a = ap.parse_args()
    run(pathlib.Path(a.data_dir), pathlib.Path(a.out_dir),
        parse_dt(a.start), parse_dt(a.end), a.tick, a.buf, a.expiry_days, a.max_per_level)


if __name__ == "__main__":
    main()
