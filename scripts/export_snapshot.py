"""
Export the fully processed player pool (positions parsed, percentiles computed,
value efficiency calculated) to a static CSV snapshot.

Why this exists: Streamlit Community Cloud can't reach a local PostgreSQL database
- it runs on Streamlit's own servers with no network path back to your machine.
Rather than standing up a cloud-hosted database for a portfolio demo, the deployed
app reads from this static snapshot instead. Re-run this script + redeploy whenever
you want the live demo to reflect fresh data.

This uses the exact same computation functions as the CLI and the local-DB app
mode - no logic is duplicated or reimplemented here.
"""

import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from similarity_engine import (
    load_player_pool,
    prepare_pool,
    compute_percentiles,
    compute_value_efficiency,
)

load_dotenv()

OUT_DIR = "data/processed"
SNAPSHOT_FILE = f"{OUT_DIR}/player_pool_snapshot.csv"
META_FILE = f"{OUT_DIR}/snapshot_meta.json"


def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[ERROR] No DATABASE_URL in .env")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)

    engine = create_engine(db_url)
    df = load_player_pool(engine)
    df = prepare_pool(df)
    df = compute_percentiles(df)
    df = compute_value_efficiency(df)

    df.to_csv(SNAPSHOT_FILE, index=False)

    meta = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(df),
    }
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"Exported {len(df)} players to {SNAPSHOT_FILE}")
    print(f"Wrote metadata to {META_FILE}")
    print("\nNote: 'age' is computed as of export time and will drift slightly stale "
          "between re-exports - fine for a portfolio demo, just don't expect it to "
          "update live.")


if __name__ == "__main__":
    main()
