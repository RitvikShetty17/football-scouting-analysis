"""
Position normalization + similarity engine - the core "find a cheaper statistical
twin of this player" logic.

Understat's position field is a space-separated code list (e.g. "D M S"), ordered
by how often the player featured in each role, with a trailing "S" meaning the
player also came on as a substitute in some matches (not a real position). ~15%
of players show only "S" with no real position code at all - these are dropped
from position-grouped comparisons since we can't group them reliably.

Known limitation (documented in README too): Understat's stats are entirely
attack/chance-creation focused (goals, xG, assists, xA, shots, key passes). There's
no tackling/interception/aerial data, so this engine is far more informative for
forwards and attacking midfielders than for defenders, whose defensive output is
invisible to us here.
"""

import os
import sys
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

MIN_MINUTES = 450  # ~5 full matches - below this, per-90 rates are too noisy to trust

POSITION_GROUP_MAP = {"GK": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Forward"}

# Per-90 stats used for the similarity vector. All attacking/creation metrics -
# see the defensive-data limitation noted above.
FEATURE_COLS = ["goals_per90", "assists_per90", "xg_per90", "xa_per90", "npxg_per90"]


def parse_primary_position(position_str: str):
    """Understat orders position codes by frequency played, so the first non-'S'
    code is the best available guess at primary position. Returns None if the
    player has no real position code (i.e. the field was just 'S')."""
    if pd.isna(position_str):
        return None
    codes = [c for c in str(position_str).split() if c != "S"]
    if not codes:
        return None
    return POSITION_GROUP_MAP.get(codes[0])


def load_player_pool(engine) -> pd.DataFrame:
    """Pull players + season stats + market data (left join - not everyone matched
    to a Transfermarkt record) from PostgreSQL."""
    query = text("""
        SELECT p.player_id, p.full_name, p.position, p.birth_date, t.team_name,
               s.minutes_played, s.goals_per90, s.assists_per90, s.xg_per90,
               s.xa_per90, s.npxg_per90,
               m.market_value_eur, m.match_confidence
        FROM players p
        JOIN player_season_stats s ON p.player_id = s.player_id
        LEFT JOIN teams t ON p.team_id = t.team_id
        LEFT JOIN player_market_data m ON p.player_id = m.player_id
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    if "birth_date" in df.columns:
        df["birth_date"] = pd.to_datetime(df["birth_date"], errors="coerce")
        df["age"] = ((pd.Timestamp.now() - df["birth_date"]).dt.days / 365.25).round(1)
    return df


def prepare_pool(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["minutes_played"] >= MIN_MINUTES].copy()
    df["position_group"] = df["position"].apply(parse_primary_position)

    excluded = df["position_group"].isna().sum()
    if excluded > 0:
        print(f"[INFO] Excluding {excluded} players with no usable position code "
              f"(Understat only showed substitute appearances for them).")
    df = df[df["position_group"].notna()].copy()
    return df


def compute_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """Percentile-rank each feature within the player's position group, so a
    striker's goals/90 and a winger's goals/90 are compared fairly against peers
    in the same role rather than against the whole league."""
    for col in FEATURE_COLS:
        df[f"{col}_pctl"] = df.groupby("position_group")[col].rank(pct=True) * 100
    return df


def compute_value_efficiency(df: pd.DataFrame) -> pd.DataFrame:
    """A simple composite output score (average of the percentile ranks) divided
    by market value. Higher = more statistical output per euro of value - the
    core 'undervalued' signal. Players with no market value match get NaN here,
    not zero, so they're not falsely ranked as infinitely efficient."""
    pctl_cols = [f"{c}_pctl" for c in FEATURE_COLS]
    df["composite_output_pctl"] = df[pctl_cols].mean(axis=1)
    df["value_efficiency"] = np.where(
        df["market_value_eur"].notna() & (df["market_value_eur"] > 0),
        df["composite_output_pctl"] / (df["market_value_eur"] / 1_000_000),
        np.nan,
    )
    return df


def compute_similar_players(df: pd.DataFrame, target_name: str, top_n: int = 10):
    """Pure computation, no printing - returns (target_row, ranked_pool_df) so both
    the CLI and the Streamlit app can use the same logic. Uses Euclidean distance
    on the percentile-normalized feature vector, restricted to the target's
    position group. We use Euclidean distance rather than cosine similarity
    deliberately: cosine similarity only measures a vector's direction, so a
    player who's uniformly weaker across every stat can still score as 'identical'
    if his stat ratios happen to point the same way. Euclidean distance correctly
    penalizes that magnitude gap - a clearly worse player ends up further away,
    not tied for first."""
    matches = df[df["full_name"].str.contains(target_name, case=False, na=False)]
    if matches.empty:
        raise ValueError(f"No player found matching '{target_name}'. Check spelling/accents.")
    target = matches.iloc[0]

    pctl_cols = [f"{c}_pctl" for c in FEATURE_COLS]
    pool = df[df["position_group"] == target["position_group"]].copy()
    pool = pool[pool["player_id"] != target["player_id"]]

    target_vec = target[pctl_cols].values.astype(float)
    pool_vecs = pool[pctl_cols].values.astype(float)

    distances = np.linalg.norm(pool_vecs - target_vec, axis=1)
    # convert to a 0-1 "similarity" for readability (max possible distance across
    # N percentile dims of range 100 is sqrt(N * 100^2))
    max_possible_distance = np.sqrt(len(pctl_cols) * 100**2)
    pool["similarity"] = 1 - (distances / max_possible_distance)

    pool = pool.sort_values("similarity", ascending=False).head(top_n)
    other_matches = matches.iloc[1:]["full_name"].tolist() if len(matches) > 1 else []

    return target, pool, other_matches


def print_comps(target: pd.Series, pool: pd.DataFrame, other_matches: list, top_n: int = 10):
    """CLI display wrapper around compute_similar_players' output."""
    if other_matches:
        print(f"[INFO] Multiple matches, using: {target['full_name']} ({target['team_name']}). "
              f"Other matches: {', '.join(other_matches)}")

    print(f"\nTarget: {target['full_name']} ({target['team_name']}, {target['position_group']}, "
          f"age {target['age'] if pd.notna(target.get('age')) else 'unknown'})")
    print(f"  Market value: {'€{:,.0f}'.format(target['market_value_eur']) if pd.notna(target['market_value_eur']) else 'no match found'}")
    print(f"  Per-90: {target['goals_per90']:.2f}G {target['assists_per90']:.2f}A "
          f"{target['xg_per90']:.2f}xG {target['xa_per90']:.2f}xA\n")

    if target["position_group"] in ("Defender", "Goalkeeper"):
        print(f"  [CAUTION] {target['position_group']}s have near-zero attacking output by nature, "
              f"and this dataset has no defensive stats (tackles, interceptions, aerials). "
              f"Similarity here is really just matching on 'other {target['position_group'].lower()}s "
              f"with similarly low attacking numbers', not genuine defensive/positional style. "
              f"Treat this list as low-signal.\n")

    print(f"Top {top_n} statistical comps ({target['position_group']}s only):")
    for _, row in pool.iterrows():
        value_str = "€{:,.0f}".format(row["market_value_eur"]) if pd.notna(row["market_value_eur"]) else "no value data"
        efficiency_str = f"eff={row['value_efficiency']:.1f}" if pd.notna(row["value_efficiency"]) else "eff=n/a"
        low_value_flag = ""
        if pd.notna(row["market_value_eur"]) and row["market_value_eur"] < 1_000_000 and pd.notna(row["value_efficiency"]):
            low_value_flag = "*"
        age_str = f"age {row['age']:.0f}" if pd.notna(row.get("age")) else "age n/a"
        print(f"  sim={row['similarity']:.3f}  {row['full_name']:<25} ({row['team_name']:<20}) "
              f"{value_str:<15}{efficiency_str}{low_value_flag:<8}{age_str}")

    if (pool["market_value_eur"] < 1_000_000).any():
        print("\n  * = market value under €1M - efficiency ratio is distorted by a very small "
              "denominator here, so a high score doesn't necessarily mean a great find. Compare "
              "efficiency scores within a similar value bracket, not across the whole range.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/similarity_engine.py \"Player Name\"")
        sys.exit(1)

    target_name = sys.argv[1]

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[ERROR] No DATABASE_URL in .env")
        sys.exit(1)

    engine = create_engine(db_url)
    df = load_player_pool(engine)
    print(f"Loaded {len(df)} player-season rows from PostgreSQL")

    df = prepare_pool(df)
    print(f"{len(df)} players remain after filtering to >= {MIN_MINUTES} minutes "
          f"and a usable position")

    df = compute_percentiles(df)
    df = compute_value_efficiency(df)

    target, pool, other_matches = compute_similar_players(df, target_name, top_n=10)
    print_comps(target, pool, other_matches, top_n=10)


if __name__ == "__main__":
    main()
