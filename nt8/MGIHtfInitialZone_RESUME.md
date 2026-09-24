# MGIHtfInitialZone — Résumé de la détection

Indicateur NT8 (8.1) : zones de valeur + swings HTF pour l'analyse HFT sur NQ/MNQ et US30.
Fichier : `MGIHtfInitialZone.cs` — chart attendu : template **ETH CME**.

## 1. Règle Daily

- **Trend-candle** si `|Close − Open| de J0 > ATR Wilder(20) × Multiplicateur`.
  - L'ATR est calculé **avant** la bougie testée (non biaisé par elle).
  - Warmup : **23 séances** minimum chargées, sinon aucun signal daily.
- **Initial candle** = la journée qui précède la trend-candle (J−1).
- **Initial Zone** = Value Area **VAH–VAL à 70 %** du volume **1-minute NT8** de J−1
  (rows de **4 ticks**, volume réparti uniformément sur [Low..High]).
- **Swing** : trend haussière → **PB** = Low de J0 ; trend baissière → **PH** = High de J0.

## 2. Règle Weekly

- Identique avec `ATR Wilder(12)`, initiale = **semaine précédente**.
- Warmup : **15 semaines** minimum (warmup unique, pas par signal :
  une fois l'ATR disponible, chaque nouvelle semaine n'a besoin que
  d'elle-même + la précédente).
- Semaines dim–ven (6 séances, futures ETH).

## 3. Affichage

- Daily = **rouge plein**, Weekly = **bleu pointillé** (lignes seules, sans label).
- Zones = rectangles transparents + légère bordure, tracés depuis l'open
  de l'initiale ; swings depuis l'open de la trend.
- Extension **jusqu'au dernier prix** (redessinés à chaque clôture).
- FIFO : **5 daily + 5 weekly** visibles max (paramétrable).

## 4. Paramètres (défauts)

| Paramètre | Défaut |
|---|---|
| ATR Daily period / Weekly period | 20 / 12 |
| Multiplicateur ATR (`body > ATR×`) | 1.0 |
| Value Area % | 70 |
| Hauteur row profil | 4 ticks |
| Afficher Daily / Weekly / Zones / Swings | oui ×4 |
| Max zones Daily / Weekly | 5 / 5 |
| Couleurs Daily / Weekly | Rouge / Bleu (DodgerBlue) |
| Opacité zone / bordure / swing | 20 / 1 / 2 |
| Swing pointillé Daily / Weekly | non / oui |
| Info `00 - Prérequis` | minimum calculé (lecture seule) |

## 5. Prérequis historique

- **90 séances ≈ 120 jours calendaires** (Days to load) pour daily + weekly.
- Daily seul : ~35 jours. En dessous, un warning dans la sortie indique
  le minimum et l'état (`daily 12/23`, `weekly 12/15`).
