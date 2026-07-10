"""
Pull Ligue 1 player season stats from Understat.

We moved off FBref because Opta (FBref's data provider) terminated its licensing
agreement in January 2026, and FBref deleted all advanced stats (xG, xAG, progressive
stats) site-wide as a result - see README known-issues log. Understat is a more
reliable source right now: it serves data as JSON embedded in a script tag rather
than behind FBref-style Cloudflare bot protection, and the `understatapi` package
(actively maintained, last release Feb 2026) wraps it cleanly.

Trade-off: Understat only covers the Big-5 leagues + RFPL, which is why we switched
the project's target league from Eredivisie to Ligue 1.

Output: raw JSON/CSV in data/raw/ (gitignored - see .gitignore).
"""

import json
import os
import pandas as pd
from understatapi import UnderstatClient

LEAGUE = "Ligue_1"
SEASON = "2024"  # Understat's season format is the start year: "2024" = 2024/25
OUT_DIR = "data/raw"

os.makedirs(OUT_DIR, exist_ok=True)

# Per-90 stats aren't returned directly by Understat - we compute them from the
# raw totals + minutes played, same as we'd have done with the FBref data.
PER90_SOURCE_COLS = {
    "goals": "goals_per90",
    "assists": "assists_per90",
    "xG": "xg_per90",
    "xA": "xa_per90",
    "npxG": "npxg_per90",
}


def add_per90_columns(df: pd.DataFrame) -> pd.DataFrame:
    minutes = pd.to_numeric(df["time"], errors="coerce")
    nineties = minutes / 90.0
    for source_col, target_col in PER90_SOURCE_COLS.items():
        df[target_col] = pd.to_numeric(df[source_col], errors="coerce") / nineties.replace(0, pd.NA)
    return df


def main():
    with UnderstatClient() as understat:
        print(f"Pulling player season stats for {LEAGUE} {SEASON}...")
        raw = understat.league(league=LEAGUE).get_player_data(season=SEASON)

    df = pd.DataFrame(raw)
    print(f"  -> {len(df)} player rows, columns: {list(df.columns)}")

    # Save the untouched raw pull first, before any transformation -
    # this is our source of truth if the per-90 calc ever needs re-checking.
    with open(f"{OUT_DIR}/ligue1_{SEASON}_raw.json", "w") as f:
        json.dump(raw, f, indent=2)

    df = add_per90_columns(df)
    df.to_csv(f"{OUT_DIR}/ligue1_{SEASON}_players.csv", index=False)

    print(f"\nData pull complete. Files written to {OUT_DIR}/")
    print("Next: name normalization + Transfermarkt join + load into PostgreSQL")


if __name__ == "__main__":
    main()
