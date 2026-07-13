-- ValueScout — schema v2
-- Data sources: Understat (advanced per-season stats) + Transfermarkt (value/contracts)
-- League scope: Ligue 1 (switched from Eredivisie after FBref's Jan 2026 advanced-stats
-- shutdown left no reliable free source for Eredivisie xG data; Understat only covers
-- the Big-5 + RFPL)

CREATE TABLE IF NOT EXISTS teams (
    team_id         SERIAL PRIMARY KEY,
    team_name       VARCHAR(100) NOT NULL UNIQUE,
    league          VARCHAR(100) NOT NULL,
    season          VARCHAR(20)  NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    player_id           SERIAL PRIMARY KEY,
    understat_id         VARCHAR(50) UNIQUE,        -- Understat's internal player id
    full_name            VARCHAR(150) NOT NULL,
    normalized_name       VARCHAR(150) NOT NULL,     -- lowercased, accent-stripped, for Transfermarkt name matching
    position              VARCHAR(20),
    birth_date            DATE,                      -- from Transfermarkt's squad table (DD/MM/YYYY format)
    team_id               INTEGER REFERENCES teams(team_id)
);

-- One row per player per season — Understat returns a flat stat block per player,
-- so this stays a single table rather than the split standard/passing/gca layout
-- the old FBref-based schema used.
CREATE TABLE IF NOT EXISTS player_season_stats (
    stat_id         SERIAL PRIMARY KEY,
    player_id       INTEGER REFERENCES players(player_id),
    season          VARCHAR(20) NOT NULL,
    games           INTEGER,
    minutes_played  INTEGER,
    goals           INTEGER,
    assists         INTEGER,
    shots           INTEGER,
    key_passes      INTEGER,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    xg              NUMERIC(6,3),   -- expected goals
    xa              NUMERIC(6,3),   -- expected assists
    npg             INTEGER,        -- non-penalty goals
    npxg            NUMERIC(6,3),   -- non-penalty xG
    xg_chain        NUMERIC(6,3),   -- xG of every possession the player was involved in
    xg_buildup      NUMERIC(6,3),   -- xG chain excluding shots/key passes (pure buildup contribution)
    -- per-90 versions, computed at load time for fair cross-player comparison
    goals_per90     NUMERIC(6,3),
    assists_per90   NUMERIC(6,3),
    xg_per90        NUMERIC(6,3),
    xa_per90        NUMERIC(6,3),
    npxg_per90      NUMERIC(6,3),
    UNIQUE(player_id, season)
);

-- Market value + contract info from Transfermarkt (added once the join logic is built)
-- NOTE: contract_expiry is currently always NULL. Transfermarkt's squad-table view
-- doesn't include contract dates (only birth date, joined date, and transfer history)
-- - getting contract expiry would require scraping each player's individual profile
-- page (~600 extra requests), deferred as future work rather than done now.
CREATE TABLE IF NOT EXISTS player_market_data (
    market_id           SERIAL PRIMARY KEY,
    player_id           INTEGER REFERENCES players(player_id),
    as_of_date           DATE NOT NULL,
    market_value_eur     NUMERIC(12,2),
    contract_expiry      DATE,
    transfermarkt_id      VARCHAR(50),
    match_confidence     NUMERIC(4,3)   -- confidence of the name-matching join (1.0 = exact match)
);

CREATE INDEX IF NOT EXISTS idx_players_position ON players(position);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id);
CREATE INDEX IF NOT EXISTS idx_season_stats_season ON player_season_stats(season);
