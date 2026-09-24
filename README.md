# InitialZone NQ — Auction Market zones + DCA backtests

Indicateur NinjaTrader 8 de **zones de valeur InitialZone** (NQ/MNQ, US30) + **miroir Python**
pour backtester des systèmes DCA dessus. 4 configurations figées (**F1–F4**), testées sur
NQ 1-min de **déc-2022 à déc-2025**.

> Données brutes **non incluses** (exports NinjaTrader, licence d'origine) : ce repo contient
> le code, la méthode et les résultats agrégés. Re-téléchargez/exportez vos propres données
> pour reproduire (voir `python/README.md`).

## Résultats en 30 secondes (NQ, 2022-12-18 → 2025-12-12)

| Config | Filtre clé | Cycles | WR% | Net $ | t | PF | TP / Urg / Exp |
|---|---|---|---|---|---|---|---|
| **F1** Daily long, zones mûries | âge zone ≥ 3 j | 74 | 87,8 | **+379 967** | 2,42 | 2,02 | 63 / 9 / 2 |
| **F2** Daily short, régime monthly | POC developing mois < VAL mois préc. | 33 | 84,8 | **+212 219** | 2,77 | 3,07 | 28 / 1 / 4 |
| **F3** H1 short, RTH + RTH-open, expiry 20h | close < RTH-open | 654 | 79,8 | **+1 677 203** | 3,21 | 1,36 | 515 / 100 / 39 |
| **F4** H1 long, RTH + RTH-open, unités OR5 | close > RTH-open, range OR 14h30-35 UTC | 462 | 81,0 | **+981 035** | 1,95 | 1,26 | 364 / 76 / 22 |

Moteur commun : entrée limite au niveau, renforts même qty tous les 1×réf (cap 3),
TP = moyenne ± 1×réf, urgence −40 000 $ latent au close, sizing `floor(10 000 $ / (réf × 20 $))`
figé, capital 1M fixe **sans compounding**, **hors frais/slippage**. Détails : `docs/initzone_strategy.md`,
données par cycle : `results/<F>/cycles.csv`, `legs.csv`, `stats.md`.

## Contenu du repo

```
nt8/                  Indicateur MGIHtfInitialZone.cs + résumé + notice install
python/
  mgi_initialzone/    Backtester DCA (dca_atr.py) + détecteur de zones (detector.py) + variantes
  mgi_contact/        Sous-ensemble vendoré requis (data_loader, session_clock) — voir note
docs/                 Stratégie figée : specs F1–F4, variantes, garde-fous
results/              F1–F4 : stats.md + cycles.csv + legs.csv (+ trades_macro F2)
```

## 1. Indicateur NinjaTrader 8

Fichier : `nt8/MGIHtfInitialZone.cs` (NT8 8.1, chart template **ETH CME**).

- **Daily** : trend-candle si `|Close − Open| J0 > ATR Wilder(20)` calculé **avant** J0 (non biaisé,
  warmup 23 séances). Initiale = J−1. Zone = Value Area **70 %** du volume 1-min de J−1
  (rows 4 ticks, volume uniforme). Swing = Low de J0 si haussière, High si baissière.
- **Weekly** : identique, ATR Wilder(12), initiale = semaine précédente (dim–ven), warmup 15 semaines.
- Affichage : Daily rouge plein, Weekly bleu pointillé, rectangles transparents, extension
  jusqu'au dernier prix, FIFO 5 + 5 zones. Prérequis : **~120 jours chargés** (daily + weekly).

Install : voir `nt8/README-NT8.md`.

## 2. Backtests Python

```bash
python -m venv venv
venv\Scripts\activate            # Windows — ou : source venv/bin/activate
pip install -r requirements.txt  # stdlib uniquement, pandas optionnel

# Placer vos exports NT8 1-min NQ ("NQ 03-22.Last.txt", ...) dans donnees/market/
# F1 — Daily long, zones mûries
python -m python.mgi_initialzone.dca_atr --side long --min-zone-age 3 --out-dir results_repro/F1
# F2 — Daily short, régime monthly
python -m python.mgi_initialzone.dca_atr --side short --regime-filter short --out-dir results_repro/F2
# F3 — H1 short RTH + RTH-open, expiry 20h
python -m python.mgi_initialzone.dca_atr --timeframe h1 --side short --rthopen-filter short --expiry-hours 20 --session-filter rth --out-dir results_repro/F3
# F4 — H1 long RTH + RTH-open, unités OR5
python -m python.mgi_initialzone.dca_atr --timeframe h1 --side long --range-mode or5 --rthopen-filter long --expiry-hours 20 --session-filter rth --out-dir results_repro/F4
```

Format d'entrée, rollover et `--data-dir` : voir `python/README.md`.

> Note : `detect_h7.py` importe `backtest_ssrn_v2` (hors périmètre, non inclus) — il n'est
> **pas** sur le chemin F1–F4 et n'est jamais importé par `dca_atr.py`. `event_breakout.py`
> est un utilitaire d'étude d'événements, pas requis pour F1–F4.

## 3. Limites (lire avant usage)

1. Échantillons daily faibles (F1 : 74, F2 : 33) ; t calculés sur cycles chevauchants = **surestimés**.
2. **Hors frais/slippage**, contrats entiers, pas de compounding ; fills limites sans slippage, TP prioritaire intra-barre.
3. Buckets **UTC fixes** (DST non modélisé) ; warmups J1-J3 / 00-03h UTC ; 65 samedis + 120 dimanches-fantômes + 1 barre corrompue écartés.
4. **Urgences = seuil déclencheur, pas stop garanti** (dépassements à −50 k$ observés).
5. F4 sous le seuil t > 2 : **à confirmer, pas à célébrer**. F3 la plus solide (top-10 = 20 % du net, gains 2023/2024/2025).
6. Concurrence plafonnée : `--max-open-cycles` = 3 (daily long, H1), 5 (short regime) — marge live à dimensionner (pics ~100 contrats en H1).

## Licence

MIT — voir [LICENSE](LICENSE).
