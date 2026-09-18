# Limburg Basketbal Weekblad — data-scraper

Haalt wekelijks de resultaten op van Limburgse basketbalploegen (TDM1, TDM2A,
TDW, 1e/2e Landelijke heren en dames), voor gebruik in een wekelijkse
krantenpagina-achtige samenvatting.

## Setup

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
python fetch_weekly_results.py
```

Output komt terecht in `results/speeldag-<datum>.json`.

## Automatisch laten draaien

Zie `.github/workflows/weekly-scrape.yml`. Zodra dit gepusht is naar GitHub,
draait het script elke maandagochtend vanzelf en committen de resultaten
terug naar deze repo. Test het manueel via het "Actions"-tabblad op GitHub →
"Run workflow", zonder op maandag te moeten wachten.

## Bekende ID's (seizoen 2026-27)

**Basketbal Vlaanderen reeksen** (voor eindstanden):
- TDM1: `BVBL26279180NAHSE11A`
- TDM2A: `BVBL26279180NAHSE21A`
- 1e Landelijke Heren: `BVBL26279100LAHSE11A`
- 2e Landelijke Heren: `BVBL26279100LAHSE21A` (letter A/B/C nog te bevestigen
  elk seizoen, controleer via een teampagina)
- 1e Landelijke Dames: `BVBL26279100LADSE11A`
- 2e Landelijke Dames: `BVBL26279100LADSE21A`

**Genius Sports competitie-ID's** (voor boxscores/quarters/index):
- TDM1: `49412`
- TDM2A: `49442`
- TDW: `49443`

Deze ID's veranderen typisch bij het begin van elk nieuw seizoen. Als een
reeks leeg terugkomt, zoek de nieuwe ID op via een teampagina op
basketbal.vlaanderen (link "Meer details bekijken") en update `REEKSEN` in
`fetch_weekly_results.py`.

## Gevolgde Limburgse ploegen

- TDM1: BBC Croonen Lommel
- TDM2A: Hasselt BT, KSTBB (Sint-Truiden), Basket Tongeren
- TDW: Lummen
- 1e Landelijke Heren: Stevoort
- 2e Landelijke Heren: Zolder, Hades Kiewit, Tongeren B, Lommel B, Stevoort B,
  KBBC Miners Beringen, Bree Basket
