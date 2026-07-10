# Undervalued Talent Finder

A statistical scouting tool that identifies cheaper, statistically-similar alternatives to a target player — built to mirror the kind of similarity/value analysis real recruitment analytics departments run.

**Status:** Week 1 — data pipeline foundation (in progress)

## Data sources
- **Understat** — per-season advanced stats (xG, xA, npxG, xGChain, xGBuildup) via the `understatapi` package
- **Transfermarkt** — market value, contract expiry, age

## Scope (v1)
- League: Ligue 1
- Season: 2024–25

## Known issues log
- **FBref advanced stats shutdown (Jan 2026):** this project originally targeted FBref + Eredivisie. On Jan 20, 2026, FBref's data provider (Opta/Stats Perform) terminated their licensing agreement and required FBref to delete all advanced stats (xG, xAG, progressive passes, shot creation) site-wide. FBref now only retains basic stats (goals, assists, cards). We migrated to **Understat** for advanced stats, which required switching league scope from Eredivisie to **Ligue 1**, since Understat only covers the Big-5 leagues + RFPL. See `scripts/scrape_understat.py` header for details.

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
