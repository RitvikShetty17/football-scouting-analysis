"""
Match Understat player-season stats against Transfermarkt market value/birth-date
data, then load both into PostgreSQL per sql/schema.sql.

Why this needs fuzzy matching rather than an exact join: the two sources spell
names and club names differently (accents, "Saint-Germain" vs "Saint Germain",
nicknames vs full legal names). We match name-first within the same club where
possible, and record a match_confidence score for every row so low-confidence
matches can be reviewed rather than silently trusted - this mirrors the kind of
entity-resolution QA step used in the PurposeTech grant-data pipeline.

Note: contract_expiry is NOT populated - Transfermarkt's squad-table view doesn't
include contract dates (confirmed by inspecting the actual page structure), only
birth date, joined date, and transfer history. Getting contract data would require
scraping each player's individual profile page - documented as future work.
"""

import os
import re
import html

import pandas as pd
from unidecode import unidecode
from rapidfuzz import fuzz, process
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

UNDERSTAT_FILE = "data/raw/ligue1_2024_players.csv"
TRANSFERMARKT_FILE = "data/raw/transfermarkt_ligue1_2024.csv"
SEASON_LABEL = "2024-25"

NAME_MATCH_THRESHOLD = 85  # rapidfuzz score (0-100) below which we don't accept a match

# Manual aliases for club names that won't fuzzy-match cleanly on their own.
# Add to this as you spot mismatches in the unmatched-team report.
TEAM_ALIASES = {
    "paris saint germain": "psg",
    "paris sg": "psg",
    "saint etienne": "as saint etienne",
    "st etienne": "as saint etienne",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, unescape HTML entities, remove punctuation.
    Understat's API returns names with HTML entities un-decoded (e.g. "M&#039;Bala"
    instead of "M'Bala"), which would otherwise cause false near-misses in matching."""
    if pd.isna(name):
        return ""
    name = html.unescape(str(name))
    name = unidecode(name).lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return TEAM_ALIASES.get(name, name)


def match_players(understat_df: pd.DataFrame, tm_df: pd.DataFrame) -> pd.DataFrame:
    understat_df["norm_name"] = understat_df["player_name"].apply(normalize_name)
    understat_df["norm_team"] = understat_df["team_title"].apply(normalize_name)
    tm_df["norm_name"] = tm_df["player_name"].apply(normalize_name)
    tm_df["norm_team"] = tm_df["club"].apply(normalize_name)

    matches = []
    unmatched = []

    for _, u_row in understat_df.iterrows():
        # Prefer matching within the same (normalized) team first
        same_team_candidates = tm_df[tm_df["norm_team"] == u_row["norm_team"]]
        candidates = same_team_candidates if len(same_team_candidates) > 0 else tm_df

        if len(candidates) == 0:
            unmatched.append(u_row["player_name"])
            continue

        best = process.extractOne(
            u_row["norm_name"], candidates["norm_name"], scorer=fuzz.WRatio
        )

        if best and best[1] >= NAME_MATCH_THRESHOLD:
            tm_row = candidates.iloc[best[2]]
            matches.append({
                "understat_name": u_row["player_name"],
                "transfermarkt_name": tm_row["player_name"],
                "team": u_row["team_title"],
                "match_confidence": round(best[1] / 100, 3),
                "matched_within_team": len(same_team_candidates) > 0,
                "market_value_eur": tm_row["market_value_eur"],
                "birth_date": tm_row.get("birth_date"),
            })
        else:
            unmatched.append(u_row["player_name"])

    match_df = pd.DataFrame(matches)

    print(f"Matched: {len(match_df)} / {len(understat_df)} Understat players "
          f"({len(match_df) / len(understat_df) * 100:.1f}%)")
    if unmatched:
        print(f"Unmatched ({len(unmatched)}) - review manually, these won't have "
              f"market value data:")
        for name in unmatched[:20]:
            print(f"  - {name}")
        if len(unmatched) > 20:
            print(f"  ... and {len(unmatched) - 20} more")

    low_confidence = match_df[match_df["match_confidence"] < 0.92]
    if len(low_confidence) > 0:
        print(f"\n[REVIEW SUGGESTED] {len(low_confidence)} matches scored between "
              f"{NAME_MATCH_THRESHOLD}-92 confidence - spot-check these before trusting them:")
        for _, row in low_confidence.iterrows():
            print(f"  - '{row['understat_name']}' -> '{row['transfermarkt_name']}' "
                  f"(team: {row['team']}, confidence: {row['match_confidence']})")

    return match_df


def load_to_postgres(understat_df: pd.DataFrame, match_df: pd.DataFrame):
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("\n[SKIPPED DB LOAD] No DATABASE_URL found in .env - "
              "copy .env.example to .env and fill in your connection string, then re-run.")
        return

    engine = create_engine(db_url)
    match_lookup = match_df.set_index("understat_name")

    with engine.begin() as conn:
        team_ids = {}
        for team_name in understat_df["team_title"].unique():
            result = conn.execute(
                text("""
                    INSERT INTO teams (team_name, league, season)
                    VALUES (:name, 'Ligue 1', :season)
                    ON CONFLICT (team_name) DO UPDATE SET team_name = EXCLUDED.team_name
                    RETURNING team_id
                """),
                {"name": team_name, "season": SEASON_LABEL},
            )
            team_ids[team_name] = result.scalar()

        for _, row in understat_df.iterrows():
            birth_date = None
            if row["player_name"] in match_lookup.index:
                m = match_lookup.loc[row["player_name"]]
                birth_date = m.get("birth_date") if pd.notna(m.get("birth_date")) else None

            player_result = conn.execute(
                text("""
                    INSERT INTO players (understat_id, full_name, normalized_name, position, birth_date, team_id)
                    VALUES (:uid, :name, :norm_name, :position, :birth_date, :team_id)
                    ON CONFLICT (understat_id) DO UPDATE SET
                        full_name = EXCLUDED.full_name, birth_date = EXCLUDED.birth_date
                    RETURNING player_id
                """),
                {
                    "uid": str(row["id"]),
                    "name": row["player_name"],
                    "norm_name": row["norm_name"],
                    "position": row.get("position"),
                    "birth_date": birth_date,
                    "team_id": team_ids[row["team_title"]],
                },
            )
            player_id = player_result.scalar()

            conn.execute(
                text("""
                    INSERT INTO player_season_stats
                        (player_id, season, games, minutes_played, goals, assists, shots,
                         key_passes, yellow_cards, red_cards, xg, xa, npg, npxg,
                         xg_chain, xg_buildup, goals_per90, assists_per90, xg_per90,
                         xa_per90, npxg_per90)
                    VALUES
                        (:player_id, :season, :games, :minutes, :goals, :assists, :shots,
                         :key_passes, :yellow, :red, :xg, :xa, :npg, :npxg,
                         :xg_chain, :xg_buildup, :goals_p90, :assists_p90, :xg_p90,
                         :xa_p90, :npxg_p90)
                    ON CONFLICT (player_id, season) DO UPDATE SET
                        goals = EXCLUDED.goals, xg = EXCLUDED.xg
                """),
                {
                    "player_id": player_id, "season": SEASON_LABEL,
                    "games": row.get("games"), "minutes": row.get("time"),
                    "goals": row.get("goals"), "assists": row.get("assists"),
                    "shots": row.get("shots"), "key_passes": row.get("key_passes"),
                    "yellow": row.get("yellow_cards"), "red": row.get("red_cards"),
                    "xg": row.get("xG"), "xa": row.get("xA"),
                    "npg": row.get("npg"), "npxg": row.get("npxG"),
                    "xg_chain": row.get("xGChain"), "xg_buildup": row.get("xGBuildup"),
                    "goals_p90": row.get("goals_per90"), "assists_p90": row.get("assists_per90"),
                    "xg_p90": row.get("xg_per90"), "xa_p90": row.get("xa_per90"),
                    "npxg_p90": row.get("npxg_per90"),
                },
            )

            if row["player_name"] in match_lookup.index:
                m = match_lookup.loc[row["player_name"]]
                # Delete any existing market data for this player first - this INSERT
                # has no natural unique key to use ON CONFLICT with (as_of_date changes
                # every run), so without this, re-running the script repeatedly adds a
                # duplicate row per player every time instead of replacing it. That
                # silently fanned out the LEFT JOIN in load_player_pool() and inflated
                # row counts (caught when a snapshot export showed more rows than the
                # total number of players that exist).
                conn.execute(
                    text("DELETE FROM player_market_data WHERE player_id = :player_id"),
                    {"player_id": player_id},
                )
                conn.execute(
                    text("""
                        INSERT INTO player_market_data
                            (player_id, as_of_date, market_value_eur, match_confidence)
                        VALUES (:player_id, CURRENT_DATE, :value, :confidence)
                    """),
                    {
                        "player_id": player_id, "value": m["market_value_eur"],
                        "confidence": m["match_confidence"],
                    },
                )

    print(f"\nLoaded {len(understat_df)} players into PostgreSQL "
          f"({len(match_df)} with market value data attached).")


def main():
    understat_df = pd.read_csv(UNDERSTAT_FILE)
    tm_df = pd.read_csv(TRANSFERMARKT_FILE)

    # Understat's API leaves names HTML-escaped (e.g. "M&#039;Bala Nzola") - clean
    # this up before anything else touches player_name, so both the matching logic
    # and any printed/reported names are correct.
    understat_df["player_name"] = understat_df["player_name"].apply(
        lambda n: html.unescape(str(n)) if pd.notna(n) else n
    )

    print(f"Understat players: {len(understat_df)}")
    print(f"Transfermarkt players: {len(tm_df)}\n")

    match_df = match_players(understat_df, tm_df)
    understat_df["norm_name"] = understat_df["player_name"].apply(normalize_name)

    load_to_postgres(understat_df, match_df)


if __name__ == "__main__":
    main()
