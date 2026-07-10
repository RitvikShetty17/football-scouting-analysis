"""
Scrape Ligue 1 2024-25 squad data from Transfermarkt: market value, age, nationality,
position, and contract expiry, per club.

Transfermarkt is much lighter on bot-protection than FBref currently is - plain
requests + a realistic browser User-Agent header is enough (no Cloudflare-bypass
tooling needed as of this writing). Still, we scrape politely: one request per
club with a short delay between requests, since we're pulling ~18 pages total.

Approach: each club's "detailed squad" view (the /plus/1 URL suffix) includes
market value AND contract expiry in one table, so we get both in a single pass
per club rather than needing a second scrape for contract data.
"""

import time
import re
import requests
from bs4 import BeautifulSoup
import pandas as pd

BASE_URL = "https://www.transfermarkt.com"
LEAGUE_CODE = "FR1"  # Ligue 1
SEASON_ID = "2024"   # 2024-25 season
OUT_DIR = "data/raw"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2  # be polite - this is a free public site, not an API
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5  # doubles each retry: 5s, 10s, 20s


def get_with_retries(url: str) -> requests.Response:
    """GET with retries for transient failures (timeouts, connection resets, 5xx).
    Transfermarkt occasionally returns a 502 or times out under normal, non-abusive
    request patterns - this isn't bot detection, just an overloaded server, and a
    short retry almost always succeeds."""
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"{resp.status_code} server error")
            resp.raise_for_status()
            return resp
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as e:
            last_exception = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"    [RETRY {attempt}/{MAX_RETRIES}] {e} - waiting {wait}s...")
                time.sleep(wait)
    raise last_exception


def get_club_links() -> list[dict]:
    """Get all club names/ids/slugs for the league from the league overview page."""
    url = f"{BASE_URL}/ligue-1/startseite/wettbewerb/{LEAGUE_CODE}/saison_id/{SEASON_ID}"
    resp = get_with_retries(url)
    soup = BeautifulSoup(resp.text, "lxml")

    clubs = []
    # Club links on the league overview table point to /{slug}/startseite/verein/{id}
    for link in soup.select("a[href*='/startseite/verein/']"):
        href = link.get("href", "")
        match = re.search(r"/([\w-]+)/startseite/verein/(\d+)", href)
        if match and link.text.strip():
            clubs.append({"slug": match.group(1), "club_id": match.group(2), "club_name": link.text.strip()})

    # de-dupe (the club name usually appears twice per row - as a badge link and a text link)
    seen = set()
    unique_clubs = []
    for c in clubs:
        if c["club_id"] not in seen:
            seen.add(c["club_id"])
            unique_clubs.append(c)
    return unique_clubs


def parse_market_value(value_text: str) -> float:
    """Convert Transfermarkt's '€45.00m' / '€800k' / '-' style values to raw euros."""
    if not value_text:
        return None
    value_text = value_text.replace("€", "").strip()
    if value_text in ("-", ""):
        return None
    multiplier = 1
    if value_text.endswith("m"):
        multiplier = 1_000_000
        value_text = value_text[:-1]
    elif value_text.endswith("k"):
        multiplier = 1_000
        value_text = value_text[:-1]
    try:
        return float(value_text) * multiplier
    except ValueError:
        return None


def scrape_club_squad(club: dict) -> list[dict]:
    """Scrape one club's detailed squad table (market value + contract expiry)."""
    url = f"{BASE_URL}/{club['slug']}/kader/verein/{club['club_id']}/saison_id/{SEASON_ID}/plus/1"
    resp = get_with_retries(url)
    soup = BeautifulSoup(resp.text, "lxml")

    rows = soup.select("table.items > tbody > tr")
    players = []
    for row in rows:
        try:
            name_cell = row.select_one("td.posrela")
            if not name_cell:
                continue
            name_link = name_cell.select_one("a")
            player_name = name_link.text.strip() if name_link else None

            position_cell = name_cell.select_one("table.inline-table tr:nth-of-type(2) td")
            position = position_cell.text.strip() if position_cell else None

            all_cells = row.select("td")
            # Market value is consistently the last cell with class 'rechts'
            market_value_cell = row.select_one("td.rechts.hauptlink")
            market_value = parse_market_value(market_value_cell.text.strip()) if market_value_cell else None

            # Contract expiry - present as a date-formatted cell in the /plus/1 detailed view.
            # We match on the cell's text pattern (e.g. "Jun 30, 2027") rather than a fixed
            # column index, since column order can shift between Transfermarkt's table variants.
            contract_expiry = None
            for cell in all_cells:
                text = cell.text.strip()
                if re.match(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", text):
                    contract_expiry = text
                    break

            nationality_imgs = name_cell.find_next("td").select("img") if name_cell.find_next("td") else []
            nationality = nationality_imgs[0].get("title") if nationality_imgs else None

            if player_name:
                players.append({
                    "player_name": player_name,
                    "position": position,
                    "nationality": nationality,
                    "market_value_eur": market_value,
                    "contract_expiry_raw": contract_expiry,
                    "club": club["club_name"],
                })
        except Exception as e:
            print(f"    [WARN] Skipped a row in {club['club_name']}'s squad due to parse error: {e}")
            continue

    return players


def main():
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"Fetching Ligue 1 club list for season {SEASON_ID}...")
    clubs = get_club_links()
    print(f"  -> found {len(clubs)} clubs")

    if len(clubs) < 15:
        print("  [WARN] Expected ~18 Ligue 1 clubs but found fewer - check that the "
              "league overview page structure hasn't changed before proceeding.")

    all_players = []
    failed_clubs = []
    for i, club in enumerate(clubs, 1):
        print(f"[{i}/{len(clubs)}] Scraping {club['club_name']}...")
        try:
            players = scrape_club_squad(club)
            print(f"    -> {len(players)} players")
            all_players.extend(players)
        except Exception as e:
            # Broad catch is intentional here: a single club failing (timeout, 502,
            # unexpected page structure) should never lose the whole run's progress.
            print(f"    [ERROR] Failed to fetch {club['club_name']} after retries: {e}")
            failed_clubs.append(club["club_name"])
        finally:
            # Write out what we have after every club, not just at the end -
            # a crash on club 15 shouldn't cost you clubs 1-14's data.
            if all_players:
                pd.DataFrame(all_players).to_csv(
                    f"{OUT_DIR}/transfermarkt_ligue1_{SEASON_ID}.csv", index=False
                )
        time.sleep(REQUEST_DELAY_SECONDS)

    df = pd.DataFrame(all_players)
    df.to_csv(f"{OUT_DIR}/transfermarkt_ligue1_{SEASON_ID}.csv", index=False)

    print(f"\nTotal players scraped: {len(df)}")
    print(f"Players with a market value: {df['market_value_eur'].notna().sum()} "
          f"({df['market_value_eur'].notna().mean()*100:.0f}%)")
    print(f"Players with a contract expiry date: {df['contract_expiry_raw'].notna().sum()} "
          f"({df['contract_expiry_raw'].notna().mean()*100:.0f}%)")
    if failed_clubs:
        print(f"\n[INCOMPLETE] {len(failed_clubs)} club(s) failed and are missing from "
              f"this data: {', '.join(failed_clubs)}")
        print("Re-run the script - it'll re-scrape everything, which is fine at this "
              "scale (18 clubs), or ask me for a version that only retries failed clubs.")
    print(f"\nWrote {OUT_DIR}/transfermarkt_ligue1_{SEASON_ID}.csv")
    print("Next: name normalization + matching against Understat data")


if __name__ == "__main__":
    main()