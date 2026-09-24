"""Event study — cassures daily (close au-dela) et closes en zone, reversal vs continuation.

Events (close daily UTC, zone daily active : trend < jour <= trend+10j) :
  BO_UP  : close J > VAH (venait de <= VAH)      -> reversal SHORT / continuation LONG
  BO_DN  : close J < VAL (venait de >= VAL)      -> reversal LONG  / continuation SHORT
  RE_UP  : close J-1 > VAH et close J dedans     -> SHORT vers VAL
  RE_DN  : close J-1 < VAL et close J dedans     -> LONG vers VAH
  IN_HI  : closes J-1,J dedans, moitie haute     -> descriptif (rotation + excursions)
  IN_LO  : closes J-1,J dedans, moitie basse     -> descriptif

Forward journalier J+1/J+3/J+5 (rendement signe, MFE/MAE), P(retour en zone
sous 5 sessions), P(touche bord oppose sous 5 sessions), delai median.
Phase 2 (trades) uniquement si |t| >= 2 quelque part.

Usage :
  venv\\Scripts\\python -m python.mgi_initialzone.event_breakout --out-dir backtest_out\\initzone_breakout
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
from .reject2_atr import FILE_RANGES, parse_dt

EXPIRY_D = 10
FWINS = (1, 3, 5)


def run(bars_1min, signals: List[ZoneSignal], tick: float = 0.25):
    # --- daily OHLC calendaires UTC ---
    per_day: Dict[date, list] = defaultdict(list)
    for b in bars_1min:
        per_day[b[0].date()].append(b)
    days = sorted(per_day.keys())
    ohlc = {}
    for d in days:
        lst = sorted(per_day[d])
        ohlc[d] = (lst[0][1], max(b[2] for b in lst), min(b[3] for b in lst), lst[-1][4])
    didx = {d: i for i, d in enumerate(days)}

    zones = [z for z in signals if z.has_zone and z.val < z.vah]
    zones.sort(key=lambda z: z.trend_date)

    events = []
    for z in zones:
        for j in range(len(days)):
            d = days[j]
            if not (z.trend_date < d <= z.trend_date + timedelta(days=EXPIRY_D)):
                continue
            if j < 1 or j + FWINS[-1] >= len(days):
                continue
            o, h, l, c = ohlc[d]
            _, _, _, pc = ohlc[days[j - 1]]
            inside = z.val <= c <= z.vah
            was_above = pc > z.vah
            was_below = pc < z.val
            was_inside = not was_above and not was_below
            mid = (z.vah + z.val) / 2
            prev_half = None
            if was_inside:
                prev_half = "HI" if pc >= mid else "LO"
            cur_half = "HI" if c >= mid else "LO"
            ev = None
            if c > z.vah and not was_above:
                ev = ("BO_UP", "VAH", -1, +1)
            elif c < z.val and not was_below:
                ev = ("BO_DN", "VAL", +1, -1)
            elif inside and was_above:
                ev = ("RE_UP", "VAH", -1, None)
            elif inside and was_below:
                ev = ("RE_DN", "VAL", +1, None)
            elif inside and prev_half != cur_half:
                ev = ("IN_HI" if cur_half == "HI" else "IN_LO", "MID", 0, None)
            if ev is None:
                continue
            kind, lvl, s_rev, s_con = ev
            row = {"date": d.strftime("%Y-%m-%d"), "zone_trend": z.trend_date.strftime("%Y-%m-%d"),
                   "bull": int(z.bullish), "kind": kind, "level": lvl,
                   "vah": round(z.vah, 2), "val": round(z.val, 2), "close": round(c, 2)}
            # forward
            fwd = days[j + 1:j + 1 + FWINS[-1]]
            mxh = max(ohlc[x][1] for x in fwd)
            mnl = min(ohlc[x][2] for x in fwd)
            for H in FWINS:
                cH = ohlc[days[j + H]][3]
                row[f"ret_rev_{H}"] = round(s_rev * (cH - c) / tick, 1) if s_rev else ""
                row[f"ret_con_{H}"] = round(s_con * (cH - c) / tick, 1) if s_con else ""
            row["mfe_up"] = round((mxh - c) / tick, 1)
            row["mfe_dn"] = round((c - mnl) / tick, 1)
            # retour en zone / touche bord oppose sous 5 sessions
            back = ""
            opp = ""
            for k in range(1, FWINS[-1] + 1):
                _o, hh, ll, cc = ohlc[days[j + k]]
                if back == "" and (ll <= z.vah and hh >= z.val):
                    back = k
                tgt = z.val if kind in ("BO_UP", "RE_UP", "IN_HI") else (z.vah if kind in ("BO_DN", "RE_DN", "IN_LO") else None)
                if opp == "" and tgt is not None:
                    if tgt == z.val and ll <= z.val:
                        opp = k
                    elif tgt == z.vah and hh >= z.vah:
                        opp = k
                if back != "" and (opp != "" or tgt is None):
                    break
            row["back_bar"] = back
            row["opp_bar"] = opp
            events.append(row)
    return events, days


def summarize(events):
    groups = defaultdict(list)
    for r in events:
        groups[(r["kind"], r["level"])].append(r)

    def tstat(xs):
        n = len(xs)
        if n < 2:
            return 0.0
        m = sum(xs) / n
        var = sum((x - m) ** 2 for x in xs) / (n - 1)
        return m / math.sqrt(var / n) if var > 0 else 0.0

    stats = []
    for (kind, lvl), rs in sorted(groups.items()):
        n = len(rs)
        row = {"kind": kind, "level": lvl, "n": n,
               "back_pct": round(100 * sum(1 for r in rs if r["back_bar"] != "") / n, 1),
               "opp_pct": round(100 * sum(1 for r in rs if r["opp_bar"] != "") / n, 1)}
        for H in FWINS:
            for col in (f"ret_rev_{H}", f"ret_con_{H}"):
                xs = [r[col] for r in rs if r[col] != ""]
                row[col + "_m"] = round(sum(xs) / len(xs), 1) if xs else 0.0
                row[col + "_t"] = round(tstat(xs), 2) if xs else 0.0
        stats.append(row)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="donnees/market")
    ap.add_argument("--out-dir", default="backtest_out/initzone_breakout")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--tick", type=float, default=0.25)
    a = ap.parse_args()
    data_dir, out_dir = pathlib.Path(a.data_dir), pathlib.Path(a.out_dir)
    start, end = parse_dt(a.start), parse_dt(a.end)
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
    print(f"[LOAD] 1min: {len(bars)}", flush=True)
    bars, n_sat, n_sun, n_drop = clean_bars(bars)
    print(f"[CLEAN] sam:{n_sat} dim:{n_sun} corr:{n_drop}", flush=True)
    signals, _ = detect_daily(bars, tick_size=a.tick)
    print(f"[SIG] daily: {len(signals)}", flush=True)
    events, _days = run(bars, signals, a.tick)
    print(f"[EVT] {len(events)}", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    cols = (["date", "zone_trend", "bull", "kind", "level", "vah", "val", "close"]
            + [f"ret_rev_{H}" for H in FWINS] + [f"ret_con_{H}" for H in FWINS]
            + ["mfe_up", "mfe_dn", "back_bar", "opp_bar"])
    with (out_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        w.writeheader()
        for r in events:
            w.writerow({k: r.get(k, "") for k in cols})
    stats = sorted(summarize(events), key=lambda r: max(abs(r["ret_rev_5_t"]), abs(r.get("ret_con_5_t", 0))), reverse=True)
    with (out_dir / "stats.md").open("w", encoding="utf-8") as f:
        f.write("# Breakouts daily vs closes en zone — reversal vs continuation (forward J+1/J+3/J+5)\n\n")
        f.write(f"**Events:** {len(events)} | **Seuil edge:** |t_5|>=2 (marque ★)  \n\n")
        f.write("| Event | Niv | n | rev1/t | rev3/t | rev5/t | con1/t | con3/t | con5/t | back% | opp% |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for r in stats:
            star = " ★" if (abs(r["ret_rev_5_t"]) >= 2 or abs(r.get("ret_con_5_t", 0)) >= 2) and r["n"] >= 20 else ""
            f.write(f"| {r['kind']} | {r['level']} | {r['n']} | {r['ret_rev_1_m']}/{r['ret_rev_1_t']} | "
                    f"{r['ret_rev_3_m']}/{r['ret_rev_3_t']} | {r['ret_rev_5_m']}/{r['ret_rev_5_t']} | "
                    f"{r.get('ret_con_1_m', '-')}/{r.get('ret_con_1_t', '-')} | {r.get('ret_con_3_m', '-')}/{r.get('ret_con_3_t', '-')} | "
                    f"{r.get('ret_con_5_m', '-')}/{r.get('ret_con_5_t', '-')} | {r['back_pct']} | {r['opp_pct']}{star} |\n")
    print(f"[DONE] -> {out_dir}", flush=True)


if __name__ == "__main__":
    main()
