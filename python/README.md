# Python — données, repro, périmètre

## Format d'entrée (exports NinjaTrader 8, 1-min Last)

Fichiers nommés `NQ MM-YY.Last.txt` (ex. `NQ 03-25.Last.txt`), **sans header**, séparateur `;` :

```
20250101 230100;21269;21282.75;21253.5;21261.25;393
datetime;open;high;low;close;volume   (format datetime : %Y%m%d %H%M%S)
```

Période couverte par les résultats publiés : **2022-12-18 → 2025-12-12**
(contrats `NQ 03-22` → `NQ 12-25`, voir `mgi_initialzone/reject2_atr.py` `FILE_RANGES`
pour le rollover vendredi d'expiration − 8 j). Timestamps traités en **UTC fixe**
(qualité : 65 samedis + 120 dimanches-fantômes + 1 barre corrompue écartés).
**Ces fichiers ne sont pas fournis** (données sous licence d'origine) : exportez-les
depuis NinjaTrader (chart 1-min NQ, ETH) dans `donnees/market/`.

## Repro F1–F4

```bash
# depuis la racine du repo
python -m python.mgi_initialzone.dca_atr --data-dir donnees/market --side long --min-zone-age 3 --out-dir out/F1
python -m python.mgi_initialzone.dca_atr --data-dir donnees/market --side short --regime-filter short --out-dir out/F2
python -m python.mgi_initialzone.dca_atr --data-dir donnees/market --timeframe h1 --side short --rthopen-filter short --expiry-hours 20 --session-filter rth --out-dir out/F3
python -m python.mgi_initialzone.dca_atr --data-dir donnees/market --timeframe h1 --side long --range-mode or5 --rthopen-filter long --expiry-hours 20 --session-filter rth --out-dir out/F4
```

Options utiles : `--start/--end` (smoke test, ex. `--start 2025-01-01 --end 2025-01-31`),
`--tick`, `--cost-rt`, `--unit-risk`, `--max-open-cycles` (plafond de concurrence :
3 daily long/H1, 5 short regime), `--no-expiry` (expérimental, non-tradable en l'état).
`python -m python.mgi_initialzone.dca_atr --help` liste tout.

## Modules

| Fichier | Rôle |
|---|---|
| `mgi_initialzone/dca_atr.py` | Moteur DCA/averaging (F1–F4). Entrée limite au niveau, renforts 1×réf cap 3, TP moyenne ± 1×réf, urgence cycle, expiry 10 j (daily) / 20 h (H1). |
| `mgi_initialzone/detector.py` | Détection des zones Daily/H1 + VA (miroir `MGIHtfInitialZone.cs`, Daily only V1). |
| `mgi_initialzone/detect_accept.py` | Variantes accept/fade/reverse (hors socle F1–F4). |
| `mgi_initialzone/reject2_atr.py` | Backtest V1 « touche + 2 closes 5-min » + `FILE_RANGES` + `resample_floor`. |
| `mgi_initialzone/event_breakout.py` | Étude d'événements breakout (utilitaire, hors F1–F4). |
| `mgi_initialzone/detect_h7.py` | Variante H7 — requiert `backtest_ssrn_v2` **non inclus** : non importé par `dca_atr.py`, ignoré pour F1–F4. |
| `mgi_contact/data_loader.py` | Parsing exports NT8 + merge/dedup multi-contrats (+ `resample_5min` si pandas). |
| `mgi_contact/session_clock.py` | Règles US DST + `dst_adjusted` (référence été). |

`mgi_contact/` est un **sous-ensemble vendoré** : seuls `data_loader` et `session_clock`
sont inclus (tout ce qu'importe le chemin F1–F4). Le moteur contact complet
(`volume_profile`, `structure_contact`, …) n'est pas publié ici.
