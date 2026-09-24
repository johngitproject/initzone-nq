# NT8 — Installation MGIHtfInitialZone

## Fichiers

- `MGIHtfInitialZone.cs` — indicateur (NT8 8.1).
- `MGIHtfInitialZone_RESUME.md` — résumé de la détection (règles, paramètres, prérequis).

## Installation

1. NinjaTrader 8 → **New → NinjaScript Editor → Indicators → ... → Add** (ou copier le `.cs`
   dans `Documents\NinjaTrader 8\bin\Custom\Indicators\`), puis **Compile** (F5).
2. Ouvrir un chart NQ/MNQ (ou US30) en template **ETH CME** (sessions ETH requises pour
   la VA 1-min et les semaines dim–ven).
3. Ajouter l'indicateur `MGIHtfInitialZone`. **Days to load : 120 minimum**
   (90 séances ≈ 120 jours calendaires pour daily + weekly ; 35 jours si Daily seul).
   En dessous, un warning s'affiche dans la sortie (`daily 12/23`, `weekly 12/15`).

## Paramètres (défauts)

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

## Lecture

- **Daily = rouge plein**, **Weekly = bleu pointillé** (lignes seules, sans label).
- Zones = rectangles transparents + légère bordure, tracés depuis l'open de l'initiale ;
  swings depuis l'open de la trend-candle. Extension jusqu'au dernier prix
  (redessinés à chaque clôture), FIFO 5 daily + 5 weekly.
