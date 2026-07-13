# ValueScout

A statistical scouting tool that identifies cheaper, statistically-similar alternatives to a target player — built to mirror the kind of similarity/value analysis real recruitment analytics departments run.

**Status:** Core pipeline + similarity engine + Streamlit UI working end-to-end. Refinements ongoing.

## Data sources
- **Understat** — per-season advanced stats (xG, xA, npxG, xGChain, xGBuildup) via the `understatapi` package
- **Transfermarkt** — market value, age (contract expiry not available - see known issues log)

## Scope (v1)
- League: Ligue 1
- Season: 2024–25

## Known issues log
- **FBref advanced stats shutdown (Jan 2026):** this project originally targeted FBref + Eredivisie. On Jan 20, 2026, FBref's data provider (Opta/Stats Perform) terminated their licensing agreement and required FBref to delete all advanced stats (xG, xAG, progressive passes, shot creation) site-wide. FBref now only retains basic stats (goals, assists, cards). We migrated to **Understat** for advanced stats, which required switching league scope from Eredivisie to **Ligue 1**, since Understat only covers the Big-5 leagues + RFPL. See `scripts/scrape_understat.py` header for details.
- **Transfermarkt squad pages reflect the current roster, not the historical one:** even when requesting a past `saison_id`, a club's squad page shows players currently registered to that club - not who played there during that season. Players who scored Understat stats for a Ligue 1 club in 2024-25 but have since transferred elsewhere won't appear in any Ligue 1 club's Transfermarkt squad, so they end up with no market value match. This affects roughly 15% of players and is a structural limitation of scraping current-squad pages rather than a bug - see the unmatched-player report `scripts/match_and_load.py` prints for the current list.
- **The similarity engine is attack/creation-focused only:** Understat provides goals, xG, assists, xA, shots, and key passes - no tackles, interceptions, aerial duels, or other defensive metrics. This means the tool is meaningfully more informative for forwards and attacking midfielders than for defenders, whose defensive contribution is invisible to this stat set. Worth stating explicitly in any pitch of this tool rather than letting it be an unstated blind spot.
- **Contract expiry is not available:** initially assumed Transfermarkt's `/plus/1` squad view included contract dates - it doesn't (confirmed by inspecting the actual page HTML). That view only adds transfer-history columns (joined date, signed-from club). Getting contract expiry would require scraping each player's individual profile page (~600 extra requests instead of ~18), deferred as documented future work. Birth date, however, IS in that view and is used for the age filter/display.

## Why Ligue 1 (after the pivot)
Ligue 1 has a strong reputation as a development/selling league (Monaco, Lille, Rennes), which keeps the original "find a cheaper version of Player X" scouting narrative intact even after the source/league change.

## Project structure
```
football-scouting-analytics/
├── data/           # raw + processed data (gitignored, not committed)
├── scripts/        # scraping + processing scripts
├── sql/            # schema + queries
├── notebooks/       # exploration notebooks
├── requirements.txt
└── README.md
```

## Setup
```bash
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Build log
- Repo scaffolding, PostgreSQL schema, Understat scraper for Ligue 1 2024-25 player season stats (pivoted from FBref/Eredivisie after FBref's advanced-stats shutdown — see known issues above)
