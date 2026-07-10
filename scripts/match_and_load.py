"""
Match Understat player-season stats against Transfermarkt market value/contract
data, then load both into PostgreSQL per sql/schema.sql.

Why this needs fuzzy matching rather than an exact join: the two sources spell
names and club names differently (accents, "Saint-Germain" vs "Saint Germain",
nicknames vs full legal names). We match name-first within the same club where
possible, and record a match_confidence score for every row so low-confidence
matches can be reviewed rather than silently trusted - this mirrors the kind of
entity-resolution QA step used in the PurposeTech grant-data pipeline.
"""

import os
import re
from datetime import datetime

import pandas as pd
from unidecode import unidecode
from rapidfuzz import fuzz, process
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

UNDERSTAT_FILE = "data/raw/ligue1_2024_players.csv"
TRANSFERMARKT_FILE = "data/raw/transfermarkt_ligue1_2024.csv"
SEASON_LABEL = "2024-25"

# Only auto-accept matches at 93+; anything below is flagged or rejected
NAME_MATCH_THRESHOLD = 85   # minimum score to consider a candidate
AUTO_ACCEPT_THRESHOLD = 93  # score at or above this is trusted without review

# Manual overrides: understat name (normalized) -> transfermarkt name (normalized)
# Use this to fix known bad fuzzy matches and known unmatched players.
MANUAL_OVERRIDES = {
    # Bad fuzzy matches caught in review
    "jonathan christian david": "jonathan david",
    "hamed junior traore": "hamed traore",
    "remy labeau lascary": None,          # no valid TM match - exclude
    "mohamed bayo": None,                 # no valid TM match - exclude
    "theo bair": None,                    # no valid TM match - exclude
    "jordan siebatcheu": None,            # no valid TM match - exclude
    "fode toure": None,                   # no valid TM match - exclude
    "david pereira da costa": None,       # no valid TM match - exclude
    "ange tia": None,                     # no valid TM match - exclude
    # Known unmatched players with correct TM spellings
    "mathis cherki": "mathis cherki",
    "neal maupay": "neal maupay",
    "randal kolo muani": "randal kolo muani",
    "amine harit": "amine harit",
    "seko fofana": "seko fofana",
    "jeremie boga": "jeremie boga",
    "duje caleta-car": "duje caleta car",
    "albert gronbaek": "albert gronbaek",
}

# Manual aliases for club names that won't fuzzy-match cleanly on their own.
TEAM_ALIASES = {
    "paris saint germain": "psg",
    "paris sg": "psg",
    "saint etienne": "as saint etienne",
    "st etienne": "as saint etienne",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, remove punctuation - for both player and club names."""
    if pd.isna(name):
        return ""
    name = unidecode(str(name)).lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return TEAM_ALIASES.get(name, name)


def parse_contract_date(raw: str):
    """Transfermarkt's detailed view formats contract expiry like 'Jun 30, 2027'."""
    if pd.isna(raw) or not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%b %d, %Y").date()
    except ValueError:
        return None


def match_players(understat_df: pd.DataFrame, tm_df: pd.DataFrame) -> pd.DataFrame:
    understat_df["norm_name"] = understat_df["player_name"].apply(normalize_name)
    understat_df["norm_team"] = understat_df["team_title"].apply(normalize_name)
    tm_df["norm_name"] = tm_df["player_name"].apply(normalize_name)
    tm_df["norm_team"] = tm_df["club"].apply(normalize_name)

    # Build a lookup from normalized TM name -> TM row for manual override resolution
    tm_by_norm_name = tm_df.set_index("norm_name")

    matches = []
    unmatched = []
    review = []

    for _, u_row in understat_df.iterrows():
        u_norm = u_row["norm_name"]

        # Check manual overrides first
        if u_norm in MANUAL_OVERRIDES:
            override_target = MANUAL_OVERRIDES[u_norm]
            if override_target is None:
                # Explicitly excluded - treat as unmatched (no market value)
                unmatched.append(u_row["player_name"])
                continue
            if override_target in tm_by_norm_name.index:
                tm_row = tm_by_norm_name.loc[override_target]
                if isinstance(tm_row, pd.DataFrame):
                    tm_row = tm_row.iloc[0]
                matches.append({
                    "understat_name": u_row["player_name"],
                    "transfermarkt_name": tm_row["player_name"],
                    "team": u_row["team_title"],
                    "match_confidence": 1.0,
                    "matched_within_team": True,
                    "market_value_eur": tm_row["market_value_eur"],
                    "contract_expiry": parse_contract_date(tm_row["contract_expiry_raw"]),
                })
                continue
            # Override target not found in TM data - fall through to fuzzy
        
        # Prefer matching within the same (normalized) team first
        same_team_candidates = tm_df[tm_df["norm_team"] == u_row["norm_team"]]
        candidates = same_team_candidates if len(same_team_candidates) > 0 else tm_df

        if len(candidates) == 0:
            unmatched.append(u_row["player_name"])
            continue

        best = process.extractOne(
            u_norm, candidates["norm_name"], scorer=fuzz.WRatio
        )

        if best and best[1] >= NAME_MATCH_THRESHOLD:
            tm_row = candidates.iloc[best[2]]
            confidence = round(best[1] / 100, 3)

            if confidence < AUTO_ACCEPT_THRESHOLD / 100:
                # Below auto-accept: flag for review but still include
                review.append({
                    "understat_name": u_row["player_name"],
                    "transfermarkt_name": tm_row["player_name"],
                    "team": u_row["team_title"],
                    "confidence": confidence,
                })

            matches.append({
                "understat_name": u_row["player_name"],
                "transfermarkt_name": tm_row["player_name"],
                "team": u_row["team_title"],
                "match_confidence": confidence,
                "matched_within_team": len(same_team_candidates) > 0,
                "market_value_eur": tm_row["market_value_eur"],
                "contract_expiry": parse_contract_date(tm_row["contract_expiry_raw"]),
            })
        else:
            unmatched.append(u_row["player_name"])

    match_df = pd.DataFrame(matches)

    print(f"Matched: {len(match_df)} / {len(understat_df)} Understat players "
          f"({len(match_df) / len(understat_df) * 100:.1f}%)")

    if unmatched:
        print(f"Unmatched ({len(unmatched)}) - no market value data for these:")
        for name in unmatched[:20]:
            print(f"  - {name}")
        if len(unmatched) > 20:
            print(f"  ... and {len(unmatched) - 20} more")

    if review:
        print(f"\n[REVIEW SUGGESTED] {len(review)} matches below {AUTO_ACCEPT_THRESHOLD} confidence:")
        for r in review:
            print(f"  - '{r['understat_name']}' -> '{r['transfermarkt_name']}' "
                  f"(team: {r['team']}, confidence: {r['confidence']})")

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
            player_result = conn.execute(
                text("""
                    INSERT INTO players (understat_id, full_name, normalized_name, position, team_id)
                    VALUES (:uid, :name, :norm_name, :position, :team_id)
                    ON CONFLICT (understat_id) DO UPDATE SET full_name = EXCLUDED.full_name
                    RETURNING player_id
                """),
                {
                    "uid": str(row["id"]),
                    "name": row["player_name"],
                    "norm_name": row["norm_name"],
                    "position": row.get("position"),
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
                conn.execute(
                    text("""
                        INSERT INTO player_market_data
                            (player_id, as_of_date, market_value_eur, contract_expiry, match_confidence)
                        VALUES (:player_id, CURRENT_DATE, :value, :expiry, :confidence)
                    """),
                    {
                        "player_id": player_id, "value": m["market_value_eur"],
                        "expiry": m["contract_expiry"], "confidence": m["match_confidence"],
                    },
                )

    print(f"\nLoaded {len(understat_df)} players into PostgreSQL "
          f"({len(match_df)} with market value data attached).")


def main():
    understat_df = pd.read_csv(UNDERSTAT_FILE)
    tm_df = pd.read_csv(TRANSFERMARKT_FILE)

    print(f"Understat players: {len(understat_df)}")
    print(f"Transfermarkt players: {len(tm_df)}\n")

    match_df = match_players(understat_df, tm_df)
    understat_df["norm_name"] = understat_df["player_name"].apply(normalize_name)

    load_to_postgres(understat_df, match_df)


if __name__ == "__main__":
    main()
