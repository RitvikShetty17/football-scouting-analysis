"""
Streamlit UI for ValueScout.

Reuses the exact same computation functions as the CLI (scripts/similarity_engine.py)
rather than reimplementing any logic here - the app is a thin display layer over
the same tested pipeline.

Known gap: contract expiry isn't available. Transfermarkt's squad-table view only
has birth date, joined date, and transfer history - not contract dates. Getting
that would mean scraping every player's individual profile page (~600 extra
requests), deferred as documented future work. Age IS available and used here,
via the birth_date column added after inspecting the real page structure.
"""

import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
from similarity_engine import (
    FEATURE_COLS,
    MIN_MINUTES,
    load_player_pool,
    prepare_pool,
    compute_percentiles,
    compute_value_efficiency,
    compute_similar_players,
)

load_dotenv()

st.set_page_config(page_title="ValueScout", layout="wide")


@st.cache_data(ttl=3600)
def get_prepared_pool() -> pd.DataFrame:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        st.error("No DATABASE_URL found. Copy .env.example to .env and fill in your connection string.")
        st.stop()
    engine = create_engine(db_url)
    df = load_player_pool(engine)
    df = prepare_pool(df)
    df = compute_percentiles(df)
    df = compute_value_efficiency(df)
    return df


def format_eur(value):
    return f"€{value:,.0f}" if pd.notna(value) else "no value data"


def build_radar_chart(target: pd.Series, comps: pd.DataFrame, top_k: int = 3):
    pctl_cols = [f"{c}_pctl" for c in FEATURE_COLS]
    labels = ["Goals/90", "Assists/90", "xG/90", "xA/90", "npxG/90"]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=target[pctl_cols].values.tolist() + [target[pctl_cols].values[0]],
        theta=labels + [labels[0]],
        name=f"{target['full_name']} (target)",
        line=dict(width=3),
    ))
    for _, row in comps.head(top_k).iterrows():
        fig.add_trace(go.Scatterpolar(
            r=row[pctl_cols].values.tolist() + [row[pctl_cols].values[0]],
            theta=labels + [labels[0]],
            name=row["full_name"],
            opacity=0.6,
        ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        height=500,
    )
    return fig


def main():
    st.title("ValueScout")
    st.caption(
        "Ligue 1, 2024-25 - statistical comps via Understat (xG/xA/npxG) + "
        "Transfermarkt (value/age). Attack/creation stats only - see the "
        "sidebar note on defenders."
    )

    df = get_prepared_pool()

    with st.sidebar:
        st.header("Filters")
        top_n = st.slider("Number of comps to show", min_value=3, max_value=20, value=10)

        max_value_available = df["market_value_eur"].max()
        budget_ceiling = st.slider(
            "Budget ceiling (€M) - only show comps at or below this value",
            min_value=0.0,
            max_value=float(max_value_available / 1_000_000) if pd.notna(max_value_available) else 100.0,
            value=float(max_value_available / 1_000_000) if pd.notna(max_value_available) else 100.0,
            step=0.5,
        )
        age_range = st.slider(
            "Age range",
            min_value=int(df["age"].min()) if df["age"].notna().any() else 15,
            max_value=int(df["age"].max()) + 1 if df["age"].notna().any() else 45,
            value=(15, 45),
        )

        st.divider()
        st.caption(
            "**Known gap:** contract expiry isn't available - Transfermarkt's squad "
            "page doesn't include it (only birth date, joined date, transfer history). "
            "Getting it would mean scraping every player's individual profile page. "
            "See README known-issues log."
        )
        st.caption(
            f"Players below {MIN_MINUTES} minutes played are excluded (too noisy "
            "for reliable per-90 rates)."
        )

    player_names = sorted(df["full_name"].unique())
    target_name = st.selectbox("Search for a player", options=player_names, index=None,
                                placeholder="Start typing a name...")

    if not target_name:
        st.info("Pick a player above to see statistical comps.")
        return

    try:
        target, pool, other_matches = compute_similar_players(df, target_name, top_n=top_n)
    except ValueError as e:
        st.error(str(e))
        return

    if other_matches:
        st.caption(f"Multiple matches for '{target_name}' - showing {target['full_name']}. "
                   f"Other matches: {', '.join(other_matches)}")

    # Apply sidebar filters to the comps pool (not to the target itself)
    filtered_pool = pool[
        pool["market_value_eur"].isna() | (pool["market_value_eur"] / 1_000_000 <= budget_ceiling)
    ]
    filtered_pool = filtered_pool[
        filtered_pool["age"].isna()
        | ((filtered_pool["age"] >= age_range[0]) & (filtered_pool["age"] <= age_range[1]))
    ]

    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader(target["full_name"])
        age_display = f"{target['age']:.0f}" if pd.notna(target.get("age")) else "unknown"
        st.write(f"{target['team_name']} · {target['position_group']} · age {age_display}")
        st.metric("Market value", format_eur(target["market_value_eur"]))
        st.write(
            f"**Per-90:** {target['goals_per90']:.2f}G  {target['assists_per90']:.2f}A  "
            f"{target['xg_per90']:.2f}xG  {target['xa_per90']:.2f}xA"
        )

        if target["position_group"] in ("Defender", "Goalkeeper"):
            st.warning(
                f"{target['position_group']}s have near-zero attacking output by nature, and "
                "this dataset has no defensive stats (tackles, interceptions, aerials). "
                "Similarity here mostly reflects 'other low-attacking-output players', not "
                "genuine defensive/positional style. Treat this list as low-signal."
            )

    with col2:
        if filtered_pool.empty:
            st.warning("No comps match the current filters - try raising the budget ceiling.")
        else:
            st.plotly_chart(build_radar_chart(target, filtered_pool), use_container_width=True)

    st.subheader(f"Top statistical comps ({target['position_group']}s)")

    if filtered_pool.empty:
        st.info("No comps to display with the current filters.")
        return

    display_df = filtered_pool[[
        "full_name", "team_name", "similarity", "age", "market_value_eur",
        "value_efficiency",
    ]].copy()
    display_df.columns = ["Player", "Team", "Similarity", "Age", "Market Value (€)", "Value Efficiency"]
    display_df["Similarity"] = display_df["Similarity"].round(3)
    display_df["Value Efficiency"] = display_df["Value Efficiency"].round(1)

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    if (filtered_pool["market_value_eur"] < 1_000_000).any():
        st.caption(
            "Value Efficiency for players under €1M market value is distorted by a very "
            "small denominator - a high score there doesn't necessarily mean a great find. "
            "Compare efficiency within a similar value bracket, not across the whole range."
        )


if __name__ == "__main__":
    main()
