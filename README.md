# ⚽ ValueScout — Football Player Similarity Engine

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-336791?style=for-the-badge&logo=postgresql&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-3F4F75?style=for-the-badge&logo=plotly&logoColor=white)

**[🔴 Try it live →](https://football-scouting-analysis.streamlit.app/)**

A statistical scouting tool that identifies cheaper, statistically-similar alternatives to a target player — built to mirror the kind of similarity and value analysis used by real recruitment analytics departments.

**Scope:** Ligue 1, 2024–25 season · ~600 players across 18 clubs

**Try it:** search **"Lee Kang-in"** — the top result, Gaëtan Perrin (Auxerre, ~€8M), is a genuinely comparable statistical profile to a player valued at ~€25M. That gap is the actual thing this tool is built to surface.

---

## 🎯 What It Does

Given any Ligue 1 player, ValueScout:

1. **Finds statistical twins** — players with a similar attacking output profile (goals, xG, assists, xA, npxG per 90) using Euclidean distance on position-group percentile rankings
2. **Ranks by value efficiency** — surfaces players who deliver similar output at a lower market value (the "undervalued player" signal)
3. **Visualizes the comparison** — radar charts overlaying the target player against their top comps
4. **Filters by budget and age** — interactive sidebar to simulate real scouting constraints

---

## 🏗️ Architecture

```
Understat API                  Transfermarkt (web scrape)
(xG, xA, npxG, goals,         (market value, age,
 assists, shots, key passes)    nationality, position)
        │                               │
        ▼                               ▼
scrape_understat.py            scrape_transfermarkt.py
        │                               │
        └──────────┬────────────────────┘
                   ▼
          match_and_load.py
     (rapidfuzz name matching +
      PostgreSQL load)
                   │
                   ▼
            PostgreSQL DB
     (teams, players, player_season_stats,
      player_market_data)
                   │
                   ▼
        similarity_engine.py
   (position normalization, percentile
    ranking, Euclidean distance, value
    efficiency scoring)
                   │
          ┌────────┴────────┐
          ▼                 ▼
   export_snapshot.py    CLI (direct
   (DB → static CSV       DB query for
    for deployment)       local use)
          │
          ▼
        app.py
   (Streamlit UI +
    Plotly radar charts)
```

The deployed app reads from a static CSV snapshot rather than querying PostgreSQL live,
since Streamlit Community Cloud can't reach a local database — see **Deployment** below.

---

## 🛠️ Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| Data Collection | `understatapi`, `requests`, `BeautifulSoup` | Pull player stats from Understat; scrape Transfermarkt squad pages |
| Name Matching | `rapidfuzz` | Fuzzy entity resolution between Understat and Transfermarkt player names |
| Storage | PostgreSQL + SQLAlchemy | 4-table normalized schema (teams, players, season stats, market data) |
| Similarity Engine | NumPy, Pandas | Euclidean distance on position-group percentile vectors |
| UI | Streamlit + Plotly | Interactive radar charts, budget/age filters, comp tables |

---

## 📂 Project Structure

```
football-scouting-analysis/
├── scripts/
│   ├── scrape_understat.py       # Pull Ligue 1 player stats via understatapi
│   ├── scrape_transfermarkt.py   # Scrape market value + birth date per club
│   ├── match_and_load.py         # Fuzzy name matching + PostgreSQL load
│   ├── similarity_engine.py      # Core similarity engine (CLI + shared logic)
│   └── export_snapshot.py        # DB → static CSV snapshot, for deployment
├── sql/
│   └── schema.sql                # PostgreSQL schema (4 tables + indexes)
├── data/processed/               # committed: the deployed app's data snapshot
├── app.py                        # Streamlit UI (reads the static snapshot)
├── requirements.txt              # app-only deps (what Streamlit Cloud installs)
├── requirements-dev.txt          # full pipeline deps (scraping, DB, matching)
└── .env.example                  # DB connection template (local pipeline only)
```

---

## 🔍 How the Similarity Engine Works

1. **Filter** — players with fewer than 450 minutes played are excluded (per-90 rates are too noisy below ~5 full matches)
2. **Position grouping** — each player's primary position is parsed from Understat's frequency-ordered position codes (GK / D / M / F)
3. **Percentile ranking** — each stat is percentile-ranked **within position group**, so a striker's xG/90 is compared fairly against other strikers, not against the whole league
4. **Euclidean distance** — computed on the 5-dimensional percentile vector. Euclidean distance is used over cosine similarity deliberately: cosine only measures vector direction, so a uniformly weaker player can still score as "identical" if his stat ratios happen to match — Euclidean distance correctly penalizes that magnitude gap
5. **Value efficiency** — composite output percentile ÷ market value (€M) — higher score = more output per euro

---

## 📊 Database Schema

| Table | Key Columns |
|---|---|
| `teams` | team_id, team_name, league, season |
| `players` | player_id, full_name, normalized_name, position, birth_date, team_id |
| `player_season_stats` | goals, assists, xg, xa, npxg, xg_chain, xg_buildup, per-90 versions of all |
| `player_market_data` | market_value_eur, as_of_date, match_confidence |

---

## 🚀 Setup

**Just to run the app** (against the committed data snapshot — no database needed):
```bash
git clone https://github.com/RitvikShetty17/football-scouting-analysis
cd football-scouting-analysis
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

**For the full pipeline** (re-scraping, re-matching, refreshing the database):
```bash
pip install -r requirements-dev.txt
cp .env.example .env
# edit .env → add DATABASE_URL (PostgreSQL connection string)

python scripts/scrape_understat.py
python scripts/scrape_transfermarkt.py
python scripts/match_and_load.py
python scripts/export_snapshot.py    # regenerate the app's data snapshot
```

---

## 🌐 Deployment

The live app reads from `data/processed/player_pool_snapshot.csv` rather than querying
PostgreSQL directly, since Streamlit Community Cloud has no network path back to a local
database. To publish fresh data:

```bash
python scripts/export_snapshot.py
git add data/processed/
git commit -m "Refresh data snapshot"
git push   # Streamlit Cloud auto-redeploys on push
```

First-time deploy: push to GitHub → share.streamlit.io → connect repo → main file `app.py`
→ in Advanced Settings, explicitly select **Python 3.12** (newer Python versions may lack
prebuilt wheels for some pinned dependencies).

---

## ⚠️ Known Limitations

- **Attack/creation stats only** — Understat provides goals, xG, assists, xA, shots, and key passes. No tackles, interceptions, or aerial data. The engine is meaningfully more informative for forwards and attacking midfielders than for defenders, and the app surfaces an explicit warning when comping a defender/goalkeeper rather than leaving this as a silent blind spot.
- **~15% of players have no market value** — players who scored Understat stats for a Ligue 1 club in 2024-25 but have since transferred won't appear on any current squad page on Transfermarkt, so they end up without a market value match. This is a structural scraping limitation (current roster vs. historical), not a bug.
- **Contract expiry unavailable** — confirmed by inspecting Transfermarkt's actual page structure: the squad view has birth date, joined date, and transfer history, but not contract dates. Getting this would require scraping each player's individual profile page (~600 extra requests instead of ~18), deferred as documented future work.
- **Built on a mid-project data source pivot** — this project originally targeted FBref + Eredivisie. On Jan 20, 2026, FBref's data provider (Opta) terminated its licensing agreement, and FBref deleted all advanced stats site-wide. The project migrated to Understat, which only covers the Big-5 leagues + RFPL — hence the Ligue 1 scope. Full detail in `scripts/scrape_understat.py`.

## Possible next steps
- Explainability layer (e.g. SHAP or regression residuals) for "why is this player undervalued" in plain language
- Retroactive validation against a real past transfer
- Contract-expiry data via player profile page scraping

---

## 👤 Author

**Ritvik Shetty**
[LinkedIn](https://www.linkedin.com/in/ritvikshetty23/) · [GitHub](https://github.com/RitvikShetty17)
