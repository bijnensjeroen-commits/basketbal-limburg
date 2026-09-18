"""
Wekelijkse basketbal-resultaten voor Limburgse ploegen.

Twee databronnen:
1. basketbal.vlaanderen (reeks-pagina's) -> eindstanden, rangschikking.
   Server-rendered, gewone HTTP GET volstaat.
2. Genius Sports / FIBA LiveStats -> boxscores, quarters, index-rating.
   De schedule-pagina is client-side JS, dus die openen we met Playwright.
   De boxscore zelf (/data/{matchId}/data.json) is pure JSON, geen browser nodig.

Gebruik: python fetch_weekly_results.py
Output:  results/speeldag-<datum>.json
"""

import json
import re
import datetime
import pathlib
import sys

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

OUTPUT_DIR = pathlib.Path("results")
OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LimburgBasketbalBot/1.0)"}

# ---------------------------------------------------------------------------
# 1. basketbal.vlaanderen: reeks-resultaten (eindstanden)
# ---------------------------------------------------------------------------

# Reeks-ID's. LET OP: deze kunnen jaarlijks wijzigen bij het begin van een
# nieuw seizoen. Als een reeks leeg terugkomt, zoek de nieuwe ID op via een
# teampagina op basketbal.vlaanderen (zoek "basketbal.vlaanderen resultaten
# <clubnaam>"), die linkt door naar "Meer details bekijken" -> de reeks-URL.
REEKSEN = {
    "TDM1": "BVBL26279180NAHSE11A",
    "TDM2A": "BVBL26279180NAHSE21A",
    "1e Landelijke Heren": "BVBL26279100LAHSE11A",
    "2e Landelijke Heren": "BVBL26279100LAHSE21A",  # bevestig letter (A/B/C) elk seizoen
    "1e Landelijke Dames": "BVBL26279100LADSE11A",
    "2e Landelijke Dames": "BVBL26279100LADSE21A",
}

# Limburgse ploegen om uit te filteren (naam zoals die op de site verschijnt,
# deelstring-match volstaat).
LIMBURG_PLOEGEN = [
    "Lommel", "Hasselt BT", "KSTBB", "Sint-Truiden", "Tongeren", "Lummen",
    "Stevoort", "Zolder", "Hades", "Beringen", "Bree",
]


def is_limburg_team(name: str) -> bool:
    return any(p.lower() in name.lower() for p in LIMBURG_PLOEGEN)


# Elke gespeelde wedstrijd in de "Uitslagen"-sectie ziet er als platte tekst
# uit als: "<dd/mm/jjjj> <Thuisploeg naam> <score> - <score> <Bezoeker naam>
# Digitaal wedstrijdformulier". Toekomstige wedstrijden (nog geen score) in
# diezelfde sectie matchen dit patroon niet en worden dus vanzelf overgeslagen.
UITSLAG_PATROON = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(\d{1,3})\s*-\s*(\d{1,3})\s+(.+?)\s+Digitaal wedstrijdformulier"
)


def fetch_reeks_resultaten(reeks_id: str) -> list[dict]:
    """Haalt de uitslagenlijst op van een reeks-pagina op basketbal.vlaanderen."""
    url = f"https://www.basketbal.vlaanderen/resultaten/reeks/{reeks_id}"
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"  [fout] kon reeks {reeks_id} niet ophalen: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    full_text = soup.get_text(" ", strip=True)

    # Isoleer enkel het stuk tussen de "Uitslagen"-kop en de "Kalender"-kop,
    # zodat we niet per ongeluk toekomstige wedstrijden of menu-tekst meepakken.
    section_match = re.search(r"Uitslagen(.*?)Kalender", full_text, re.S)
    if not section_match:
        print(f"  [waarschuwing] geen 'Uitslagen'-sectie gevonden voor {reeks_id}"
              f" (reeks nog niet gestart, of pagina-structuur gewijzigd)", file=sys.stderr)
        return []

    section = section_match.group(1)
    resultaten = []
    for datum, thuis, s1, s2, bezoeker, in [m for m in UITSLAG_PATROON.findall(section)]:
        thuis, bezoeker = thuis.strip(), bezoeker.strip()
        if not (is_limburg_team(thuis) or is_limburg_team(bezoeker)):
            continue
        resultaten.append({
            "datum": datum,
            "thuisploeg": thuis,
            "bezoekers": bezoeker,
            "score": f"{s1}-{s2}",
        })

    return resultaten


# ---------------------------------------------------------------------------
# 2. Genius Sports: matchId's opsporen via de schedule-pagina (Playwright)
# ---------------------------------------------------------------------------

GENIUS_COMPETITIES = {
    "TDM1": "49412",
    "TDM2A": "49442",
    "TDW": "49443",
}


def fetch_genius_schedule_html(comp_id: str, playwright_page) -> str:
    """Opent de schedule-pagina en geeft het ingebedde 'html'-veld terug.

    De pagina retourneert een JSON-object {css, js, html} als platte tekst
    (geen echte DOM), vandaar dat we de pagina-tekst zelf parsen i.p.v. de
    DOM te doorzoeken.
    """
    url = f"https://hosted.dcd.shared.geniussports.com/embednf/BB/en/competition/{comp_id}/schedule"
    playwright_page.goto(url, timeout=30000)
    body_text = playwright_page.inner_text("body")
    try:
        data = json.loads(body_text)
    except json.JSONDecodeError:
        # Vaak een teken dat de host (bv. de cloud-IP van GitHub Actions) is
        # geblokkeerd door bot-detectie, en een andere pagina (403, CAPTCHA,
        # of iets anders dan de verwachte JSON) heeft teruggekregen.
        print(f"    [fout] geen geldige JSON terugontvangen. Eerste 300 tekens "
              f"van het antwoord:\n    {body_text[:300]!r}", file=sys.stderr)
        raise
    return data["html"]


def find_match_ids_for_team(schedule_html: str, team_query: str) -> list[str]:
    """Zoekt matchId's terug die voorkomen in de buurt van de team-naam."""
    ids_seen = []
    for m in re.finditer(r"match/(\d+)", schedule_html):
        match_id, pos = m.group(1), m.start()
        window = schedule_html[max(0, pos - 2500): pos + 2500]
        if team_query.lower() in window.lower() and match_id not in ids_seen:
            ids_seen.append(match_id)
    return ids_seen


def fetch_boxscore(match_id: str) -> dict | None:
    """Pure JSON-endpoint, geen browser nodig."""
    url = f"https://fibalivestats.dcd.shared.geniussports.com/data/{match_id}/data.json"
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        print(f"  [fout] boxscore {match_id} niet opgehaald: {e}", file=sys.stderr)
        return None


def summarize_boxscore(data: dict) -> dict:
    """Reduceert de volledige boxscore-JSON tot wat we nodig hebben voor
    de 'sterren van de week' en de quarterstanden.

    Geeft kwartierstanden van beide ploegen terug (voor de wedstrijdcontext),
    maar spelerspunten/-stats enkel voor de Limburgse ploeg.
    """
    teams = []
    limburg_spelers = []
    for tno in ("1", "2"):
        tm = data["tm"][tno]
        naam = tm.get("name", "")
        teams.append({
            "naam": naam,
            "score": tm.get("score"),
            "quarters": [tm.get(f"p{i}_score") for i in range(1, 5)],
        })
        if is_limburg_team(naam):
            for pid, pl in tm.get("pl", {}).items():
                limburg_spelers.append({
                    "naam": pl.get("name"),
                    "punten": pl.get("sPoints"),
                    "rebounds": pl.get("sReboundsTotal"),
                    "assists": pl.get("sAssists"),
                    "steals": pl.get("sSteals"),
                    "blocks": pl.get("sBlocks"),
                    "index": pl.get("eff_1"),
                })
    limburg_spelers.sort(key=lambda p: (p["index"] or -999), reverse=True)
    return {"teams": teams, "limburg_spelers": limburg_spelers}


# ---------------------------------------------------------------------------
# 3. app.basketballstatsvlaanderen.be: aparte flow voor de landelijke reeksen
# ---------------------------------------------------------------------------
# Deze site heeft geen Genius Sports-dekking, maar toont wél kwartierstanden
# en per-speler statistieken (punten, minuten, 3P/2P/1P) voor landelijke
# wedstrijden, zonder login. De inhoud is client-side gerenderd (JS), dus ook
# hier is Playwright nodig; de eigenlijke spelerstabel is een gewone HTML
# <table>, dus die lezen we generiek uit i.p.v. met fragiele regex op platte
# tekst (dat werkte niet betrouwbaar voor de kwartierstanden-lay-out).

# Vul hier de overige Limburgse landelijke ploegen aan zodra je hun
# club_id/team_slug kent: open de teampagina op
# app.basketballstatsvlaanderen.be (zoek de club op via de site's zoekfunctie),
# en kopieer het pad na "/clubs/" exact over, spaties incluis.
BSV_TEAMS = {
    "Stevoort (1e Landelijke Heren)": {
        "club_id": "BVBL1267",
        "team_slug": "BVBL1267HSE  1",
    },
    "Zolder (2e Landelijke Heren B)": {
        "club_id": "BVBL1081",
        "team_slug": "BVBL1081HSE  1",
    },
    "Hades Kiewit (2e Landelijke Heren B)": {
        "club_id": "BVBL1217",
        "team_slug": "BVBL1217HSE  1",
    },
    "Tongeren B (2e Landelijke Heren B)": {
        "club_id": "BVBL1294",
        "team_slug": "BVBL1294HSE  2",
    },
    "Lommel B (2e Landelijke Heren B)": {
        "club_id": "BVBL1156",
        "team_slug": "BVBL1156HSE  2",  # ongeverifieerd: Lommel A (HSE  1) is bevestigd, B-team is een aanname naar hetzelfde patroon
    },
    "Stevoort B (2e Landelijke Heren B)": {
        "club_id": "BVBL1267",
        "team_slug": "BVBL1267HSE  2",  # ongeverifieerd, zelfde aanname
    },
    "Beringen (2e Landelijke Heren B)": {
        "club_id": "BVBL1076",
        "team_slug": "BVBL1076HSE  1",
    },
    "Bree (2e Landelijke Heren B)": {
        "club_id": "BVBL1455",
        "team_slug": "BVBL1455HSE  1",
    },
}

BSV_BASE = "https://app.basketballstatsvlaanderen.be"


def extract_tables(page) -> list[list[list[str]]]:
    """Generieke tabel-extractie: elke <table> op de pagina wordt een lijst
    van rijen, elke rij een lijst van celteksten. Werkt onafhankelijk van
    CSS-classes, die de site kan wijzigen."""
    return page.eval_on_selector_all(
        "table",
        """tables => tables.map(t => Array.from(t.querySelectorAll('tr')).map(
            tr => Array.from(tr.querySelectorAll('th,td')).map(c => c.innerText.trim())
        ))"""
    )


def fetch_bsv_recent_game_ids(club_id: str, team_slug: str, page, max_games: int = 3) -> list[str]:
    """Opent de teampagina en geeft de meest recente gespeelde match-ID's terug."""
    url = f"{BSV_BASE}/clubs/{club_id}/{team_slug}"
    page.goto(url, timeout=30000)
    try:
        page.wait_for_selector("text=Gespeelde wedstrijden", timeout=15000)
    except Exception as e:
        print(f"    [fout] teampagina niet geladen voor {club_id}/{team_slug}: {e}", file=sys.stderr)
        return []

    hrefs = page.eval_on_selector_all(
        "a[href*='/games/']", "els => els.map(e => e.getAttribute('href'))"
    )
    game_ids = []
    for href in hrefs:
        m = re.search(r"/games/([^/?#]+)", href or "")
        if m and m.group(1) not in game_ids:
            game_ids.append(m.group(1))
    return game_ids[:max_games]


def fetch_bsv_match_detail(game_id: str, ploeg_naam_hint: str, page) -> dict:
    """Haalt kwartierstanden (overzichtpagina) en spelerspunten (thuis/uit-
    tabblad van de eigen ploeg) op voor één match."""
    result = {"game_id": game_id}

    # Overzicht: teamnamen, eindstand, kwartierstanden.
    page.goto(f"{BSV_BASE}/games/{game_id}", timeout=30000)
    try:
        page.wait_for_selector("table, text=Fouten", timeout=15000)
    except Exception:
        pass

    title_text = page.eval_on_selector("h1", "el => el ? el.innerText : ''") or ""
    result["titel"] = title_text.strip()

    tables = extract_tables(page)
    result["tabellen_overzicht"] = tables  # ruw bewaard; exacte kwartier-indeling
    # verdient een check op echte output voor we die hard parsen, net zoals
    # bij de reeksuitslagen-parser deden we dat pas na een eerste live run.

    # Bepaal of onze ploeg thuis of uit speelt, aan de hand van de titel
    # "Team A - Team B" (Team A = thuis).
    thuis_naam = title_text.split(" - ")[0].strip() if " - " in title_text else ""
    kant = "home" if is_limburg_team(thuis_naam) else "away"

    # Spelerspunten van de eigen ploeg.
    page.goto(f"{BSV_BASE}/games/{game_id}/{kant}", timeout=30000)
    try:
        page.wait_for_selector("table", timeout=15000)
    except Exception as e:
        print(f"    [fout] spelerstabel niet geladen voor {game_id}/{kant}: {e}", file=sys.stderr)
        result["spelers"] = []
        return result

    speler_tabellen = extract_tables(page)
    spelers = []
    for tabel in speler_tabellen:
        if not tabel:
            continue
        header = [h.lower() for h in tabel[0]]
        if not any("speler" in h for h in header):
            continue  # niet de spelerstabel
        for rij in tabel[1:]:
            if not rij or not rij[0]:
                continue
            spelers.append(dict(zip(tabel[0], rij)))
    result["spelers"] = spelers
    result["kant"] = kant
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    output = {
        "opgehaald_op": datetime.datetime.now().isoformat(),
        "reeksuitslagen": {},
        "boxscores": {},
    }

    # 1. Basketbal Vlaanderen reeksuitslagen
    print("Reeksuitslagen ophalen...")
    for naam, reeks_id in REEKSEN.items():
        print(f"  - {naam} ({reeks_id})")
        output["reeksuitslagen"][naam] = fetch_reeks_resultaten(reeks_id)

    # 2. Genius Sports boxscores voor Limburgse ploegen
    print("Genius Sports matchId's opsporen (Playwright)...")
    limburg_per_competitie = {
        "TDM1": ["Lommel"],
        "TDM2A": ["Hasselt BT", "KSTBB", "Tongeren"],
        "TDW": ["Lummen"],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        for comp_naam, comp_id in GENIUS_COMPETITIES.items():
            print(f"  - {comp_naam} (competitie {comp_id})")
            try:
                schedule_html = fetch_genius_schedule_html(comp_id, page)
            except Exception as e:
                print(f"    [fout] schedule niet geladen: {e}", file=sys.stderr)
                continue

            for team in limburg_per_competitie.get(comp_naam, []):
                match_ids = find_match_ids_for_team(schedule_html, team)
                print(f"      {team}: {len(match_ids)} match(es) gevonden")
                for mid in match_ids:
                    if mid in output["boxscores"]:
                        continue
                    data = fetch_boxscore(mid)
                    if data:
                        output["boxscores"][mid] = summarize_boxscore(data)

        # 2b. app.basketballstatsvlaanderen.be: landelijke reeksen (aparte flow)
        print("Landelijke boxscores ophalen (basketballstatsvlaanderen.be)...")
        output["landelijke_boxscores"] = {}
        for ploeg_naam, info in BSV_TEAMS.items():
            print(f"  - {ploeg_naam}")
            try:
                game_ids = fetch_bsv_recent_game_ids(info["club_id"], info["team_slug"], page)
            except Exception as e:
                print(f"    [fout] kon wedstrijdenlijst niet ophalen: {e}", file=sys.stderr)
                continue
            print(f"    {len(game_ids)} recente match(es) gevonden")
            for gid in game_ids:
                if gid in output["landelijke_boxscores"]:
                    continue
                try:
                    output["landelijke_boxscores"][gid] = fetch_bsv_match_detail(gid, ploeg_naam, page)
                except Exception as e:
                    print(f"    [fout] match {gid} niet verwerkt: {e}", file=sys.stderr)

        browser.close()

    # 3. Wegschrijven
    filename = OUTPUT_DIR / f"speeldag-{datetime.date.today().isoformat()}.json"
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nKlaar. Resultaten weggeschreven naar {filename}")


if __name__ == "__main__":
    main()
