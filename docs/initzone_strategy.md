# InitialZone — 4 stratégies figées (NQ, 1-min 2022-12 → 2025-12)

Document de référence gelé. Socle : zones InitialZone (trend-candle
`|Close − Open| > ATR Wilder(20)` non-biaisé, initiale = période précédente,
Value Area **70 %** du volume 1-min, rows 4 ticks, volume uniforme,
**session-indépendant** 24h/24). Moteur : `python/mgi_initialzone/dca_atr.py`
(entrée limite au niveau, renforts même qty tous les 1×ref cap 3,
TP = moyenne ± 1×ref prioritaire, urgence −$40k latent au close, sizing
`floor(10 000 $ / (ref × 20 $))` figé, capital 1M fixe, sans compounding,
hors frais ; sens fixé au lancement, jamais inversé).
Données : exports NT8 1-min NQ, timestamps **UTC**, 65 samedis + 120
dimanches-fantômes + 1 barre corrompue écartés.

## F1 — Daily long, zones mûries (`backtest_out/initzone_dca_sweep/age3_long/`)

- TF Daily, expiry zone/trade **10 j**, session all, entry immediate, unités ATR-D.
- **Filtre unique** : âge zone **≥ 3 j** (les 0-2 j attrapent le couteau qui
  tombe : −$166k à elles seules, 9 des 13 urgences du socle).
- 74 cycles | WR 87.8 % | **+$379 967** | t 2.42 | PF 2.02 | 63 TP / 9 Urg / 2 Exp.
- Repro : `dca_atr --side long --min-zone-age 3` (tout le reste par défaut).

## F2 — Daily short, régime monthly (`backtest_out/initzone_dca/short_regime/`)

- TF Daily, expiry **10 j**, session all, entry immediate, unités ATR-D.
- **Filtre unique** : POC developing du mois < VAL du mois précédent (warmup
  J1-J3 bloqué) ; balance et acceptation acheteuse **bloquées** (vérifié
  perdantes : −$241k et −$258k — ce n'est pas un bug d'interprétation).
- 33 cycles | WR 84.8 % | **+$212 219** | t 2.77 | PF 3.07 | **1 seule urgence**.
- Repro : `dca_atr --side short --regime-filter short`.

## F3 — H1 short, RTH + RTH-open, expiry 20h, unités ATR (`backtest_out/initzone_h1_exp20/rth_ropen/`)

- TF **H1** (ATR20-H1 ≈ 57 pts médian → ~8 contrats/unité), expiry **20h**
  (10h expirait 15-24 % des cycles), session **RTH-only** (14-20h UTC).
- **Filtres** : close **< RTH-open** (open 1re barre ≥ 14h UTC). Ni régime
  (toxique en H1/RTH : −$580k/−$1.85M testé), ni âge.
- 654 cycles | WR 79.8 % | **+$1 677 203** | t 3.21 | PF 1.36 | 515 TP / 100 Urg.
- Robustesse : top-10 = 20 % du net ; gains 2023/2024/2025. *La plus solide.*
- Repro : `dca_atr --timeframe h1 --side short --rthopen-filter short --expiry-hours 20 --session-filter rth`.

## F4 — H1 long, RTH + RTH-open, expiry 20h, unités OR5 (`backtest_out/initzone_h1_or5/rth_ropen_long/`)

- Identique à F3 côté long (zones acheteuses), sauf **unités OR5** : TP/renforts/
  sizing en range OR 14h30-14h35 UTC (~35 pts médian → ~15 contrats) ; fills
  ≥ 14h35 uniquement (OR du jour même requise, pas de fallback).
- 462 cycles | WR 81.0 % | **+$981 035** | t 1.95 | PF 1.26 | 364 TP / 76 Urg.
- Sous le seuil t > 2 : à confirmer, pas à célébrer.
- Repro : `dca_atr --timeframe h1 --side long --range-mode or5 --rthopen-filter long --expiry-hours 20 --session-filter rth`.

## Candidats documentés (non figés)

| Variante | Résultat | Statut |
|---|---|---|
| Daily long + monthly-open + âge≥3 (`initzone_dca/long_mfilter_age3/`) | 34 cycles, +$300k, t 3.62, PF 4.74, 2 urg | Meilleure qualité du système, échantillon mince (2023 flat) |
| H1 short 2×, RTH+ropen@20h (`initzone_h1_exp20/rth_ropen_2x/`) | 104 cycles, +$523k, t 2.81, 0.52 adds | Concentrée (zones ≥ 2×ATR) : +$5.0k vs +$2.5k/cycle |
| H1 long RTH+ropen@20h ATR (`initzone_h1_exp20/rth_ropen_long/`) | 455 cycles, +$284k, t 0.60, top-10 = 102 % | Fragile — F4 (OR5) la remplace |
| H1 short RTH+dopen@20h | 639 cycles, +$353k, t 0.62 | Battue par RTH-open (+$1.68M) |
| H1 short + VWAP ETH (± 20h) | −$72k (10h) / +$220k, t 0.28 (20h) | Pas d'edge démontré |
| Sans-expiry (27k cycles, +$17M, ~80 % TP) | Signal réel mais **1900 contrats simultanés** | Non-tradable sans `--max-open-cycles` (à implémenter) |

## Écartées (ne pas rouvrir sans hypothèse nouvelle)

H1 + régime daily long/short (−$580k/−$1.85M) ; H1 ON-only et socles nus
(−$0.8M à −$3.3M) ; daily long + régime monthly (+$68k, t 0.47) ; entry
confirm/second-touch/delay-15 (≤ immediate partout) ; seuils urgence
−$10k/−$20k/−$30k (battus par −$40k en net et t) ; volume de touche
(non prédictif) ; filtre mois-type H1 (trop grossier).

## Garde-fous (lecture obligatoire avec les chiffres)

1. Échantillons daily faibles (33-74) ; t sur cycles chevauchants = surestimés.
2. Hors frais/slippage ; contrats entiers ; pas de compounding.
3. Buckets UTC fixes (DST non modélisé) ; warmups J1-J3 / 00-03h UTC.
4. Urgences = seuil déclencheur, pas stop garanti (dépassements à −$50k vus).
5. Concurrence plafonnée (adopté) : `--max-open-cycles` = 3 (daily long, H1),
   5 (short regime) — gratuits sauf short-regime cap 3 (−71 %, rejeté).
   Cap 1 rejeté partout (−28 % à −85 % : la valeur se concentre dans les
   chevauchements). Défauts NT8 alignés (long:3, short:5). Reste : marge
   live à dimensionner (pics ~100 contrats en H1).
6. MAE/MFE short corrigés (anciennes stats pré-fix à jeter) ; anti-refill scopé
   no-expiry ; timestamps fichier = UTC (halt 21h, RTH 13h30-20h).
7. Équivalence resample floor, TP prioritaire intra-barre, fills limites sans slippage.
8. Régime balance = bloqué (les deux côtés) : vérifié perdant côté short
   (−$241k), jamais rentable à réouvrir sans preuve nouvelle.
