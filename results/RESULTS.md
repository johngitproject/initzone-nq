# Résultats F1–F4 — NQ 2022-12-18 → 2025-12-12

Capital 1M fixe, sans compounding, hors frais/slippage. Sizing figé
`floor(10 000 $ / (réf × 20 $/pt))`, renforts 1×réf cap 3, urgence −40 000 $ latent au close.
Chaque dossier contient `stats.md` (résumé + 20 premiers cycles), `cycles.csv` (1 ligne/cycle,
séparateur `;`), `legs.csv` (jambes d'entrée/renfort/sortie).

## F1 — Daily long, zones mûries (`F1_age3_long/`)

Expiry zone/trade 10 j, session all, entry immediate, unités ATR-D, **âge zone ≥ 3 j**.
Source d'origine : `backtest_out/initzone_dca_sweep/age3_long/long/`.

| n | WR% | Net $ | Avg $ | t | PF | TP / Urg / Exp | adds 0/1/2/3 |
|---|---|---|---|---|---|---|---|
| 74 | 87,8 | 379 967 | 5 135 | 2,42 | 2,02 | 63 / 9 / 2 | 39 / 17 / 16 / 2 |

Repro : `dca_atr --side long --min-zone-age 3` (tout le reste par défaut).

## F2 — Daily short, régime monthly (`F2_short_regime/`)

Expiry 10 j, session all, entry immediate, unités ATR-D. POC developing du mois
< VAL du mois précédent (warmup J1-J3 bloqué) ; balance et acceptation acheteuse bloquées.
`trades_macro.csv` / `trades_macro_regime.csv` : fills par régime.
Source d'origine : `backtest_out/initzone_dca/short_regime/`.

| n | WR% | Net $ | Avg $ | t | PF | TP / Urg / Exp | adds 0/1/2/3 |
|---|---|---|---|---|---|---|---|
| 33 | 84,8 | 212 219 | 6 431 | 2,77 | 3,07 | 28 / 1 / 4 | 22 / 2 / 8 / 1 |

Repro : `dca_atr --side short --regime-filter short`.

## F3 — H1 short, RTH + RTH-open, expiry 20h (`F3_h1_short/`)

TF H1 (ATR20-H1 ≈ 57 pts médian → ~8 contrats/unité), session **RTH-only** (14-20h UTC),
close **< RTH-open** (open 1re barre ≥ 14h UTC). Ni régime, ni âge.
Source d'origine : `backtest_out/initzone_h1_exp20/rth_ropen/short/`.

| n | WR% | Net $ | Avg $ | t | PF | TP / Urg / Exp | adds 0/1/2/3 |
|---|---|---|---|---|---|---|---|
| 654 | 79,8 | 1 677 203 | 2 565 | 3,21 | 1,36 | 515 / 100 / 39 | 397 / 131 / 126 / 0 |

Robustesse : top-10 = 20 % du net ; gains 2023/2024/2025. *La plus solide.*
Repro : `dca_atr --timeframe h1 --side short --rthopen-filter short --expiry-hours 20 --session-filter rth`.

## F4 — H1 long, RTH + RTH-open, unités OR5 (`F4_h1_long_or5/`)

Identique à F3 côté long (zones acheteuses), sauf **unités OR5** : TP/renforts/sizing en
range OR 14h30-14h35 UTC (~35 pts médian → ~15 contrats) ; fills ≥ 14h35 uniquement.
Source d'origine : `backtest_out/initzone_h1_or5/rth_ropen_long/long/`.

| n | WR% | Net $ | Avg $ | t | PF | TP / Urg / Exp | adds 0/1/2/3 |
|---|---|---|---|---|---|---|---|
| 462 | 81,0 | 981 035 | 2 123 | 1,95 | 1,26 | 364 / 76 / 22 | 265 / 98 / 92 / 7 |

Sous le seuil t > 2 : à confirmer, pas à célébrer.
Repro : `dca_atr --timeframe h1 --side long --range-mode or5 --rthopen-filter long --expiry-hours 20 --session-filter rth`.

## Lecture des CSV

- `cycles.csv` : `id;zone_trend;level;level_px;qty;atr;n_units;entry_time;avg_entry;exit_time;exit_px;reason;net_dollar;mfe_t;mae_t;regime;min_latent_d`
- `reason` ∈ `TP | Emergency | Expired | EOD`. `MAE~/MFE~` en ticks vs première entrée.
- Spéc complète + variantes écartées : `docs/initzone_strategy.md` (document gelé).
