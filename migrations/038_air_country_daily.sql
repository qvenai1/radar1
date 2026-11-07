-- 038_air_country_daily.sql
--
-- DASH-BOARD-3b — a DAILY SUMMARY TABLE for the long air trend (owner question أ).
--
-- ⛔ A NEW ARCHITECTURAL PATTERN, AND THE FIRST OF ITS KIND HERE. DASH-BOARD-pre measured
-- that this project has "no summary table anywhere": everything aggregates on demand, and the
-- one pre-computed layer is the continuous aggregate `state_vectors_1m`. This table is
-- therefore added deliberately and narrowly, because DASH-BOARD-3 measured that the cagg road
-- is CLOSED for this unit — `count(DISTINCT icao24)` is not decomposable (one-minute distincts
-- cannot roll up to a day; daily distincts cannot sum to a week), and no approximate-distinct
-- extension is available on this instance (installed: plpgsql, timescaledb 2.28.2).
--
-- ⛔⛔ THE UNIT CHANGES WHEN ROWS ARE SUMMED, AND THAT IS THE WHOLE POINT OF THIS COMMENT.
--
--   * ONE ROW is an EXACT distinct-airframe count for ONE day. Not an approximation.
--   * SUMMING ROWS ACROSS DAYS YIELDS **HULL-DAYS**, NOT DISTINCT AIRFRAMES. An airframe
--     present on ten days contributes 10. There is no way back to a multi-day distinct count
--     from this table, and none is implied.
--   * ⛔ A hull-day total must NEVER be labelled "number of airframes", and must never sit
--     beside a distinct count from /air/series/country without the difference being visible.
--     They are two units. Adding them is the /adsb trap wearing a different hat.
--   * ⚠ AND HULL-DAYS ARE NOT A DEGRADED SUBSTITUTE — for question (أ) they are arguably the
--     better measure: they weight PRESENCE OVER TIME rather than mere appearance, so a country
--     an airframe overflew once does not score the same as one it loitered above for a week.
--     Written here so the unit is read as a choice, not as a shortfall.
--
-- ⛔ MISSING DAYS STAY MISSING. Days on which the host was off (measured: 2026-07-17, 07-23,
-- 07-24, 07-28) have NO ROW here, and none is to be inserted as zero. A zero reads "no
-- aircraft flew"; an absent row reads "we did not observe". That distinction is the entire
-- purpose of the DASH-BOARD-0 coverage contract, and a summary table is exactly where it
-- would be quietly destroyed by a well-meaning `generate_series` fill.
--
-- ⛔ ADDITIVE ONLY (project rule): one new table, no change to any existing object, so
-- rollback is a single DROP and nothing else has to be undone. The rollback statement is at
-- the foot of this file and was EXECUTED and re-applied before this migration was committed.
--
-- Shape follows 024_maritime_events: explicit column comments, a named PRIMARY KEY constraint,
-- named indexes, `computed_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

CREATE TABLE air_country_daily (
    day            DATE        NOT NULL,               -- the UTC day this row summarises (complete days only)
    country        TEXT        NOT NULL,               -- AOI polygon classification of the POSITION, not a registry
    source         TEXT        NOT NULL,               -- opensky | adsbexchange | airplanes_live — NEVER summed across
    distinct_hulls INTEGER     NOT NULL,               -- EXACT count(DISTINCT icao24) for THIS day; summing days = hull-days
    rows           BIGINT      NOT NULL,               -- position reports behind the count (a density/effort measure)
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT air_country_daily_pkey PRIMARY KEY (day, country, source),
    CONSTRAINT air_country_daily_hulls_ck CHECK (distinct_hulls > 0),
    CONSTRAINT air_country_daily_rows_ck  CHECK (rows >= distinct_hulls)
);

-- `distinct_hulls > 0`: a row exists only where something was observed. ⛔ This CHECK is the
-- schema-level guard against the zero-fill described above — an attempt to write a zero day
-- fails loudly instead of silently turning "unobserved" into "nothing there".
-- `rows >= distinct_hulls`: each distinct airframe contributed at least one position report,
-- so the two columns cannot drift into an impossible relationship.

CREATE INDEX air_country_daily_country_day_idx ON air_country_daily (country, day DESC);
CREATE INDEX air_country_daily_day_idx         ON air_country_daily (day DESC);

COMMENT ON TABLE air_country_daily IS
    'DASH-BOARD-3b. One EXACT distinct-airframe count per (day, country, source). '
    'Summing rows across days yields HULL-DAYS, not distinct airframes. '
    'Days with no observation have NO ROW — never a zero.';
COMMENT ON COLUMN air_country_daily.distinct_hulls IS
    'Exact count(DISTINCT icao24) for this day alone. NOT additive across days.';
COMMENT ON COLUMN air_country_daily.source IS
    'Feed population. opensky/adsbexchange/airplanes_live are different communities '
    '(measured hourly coverage 74.46/41.19/70.88 %) and are never summed together.';

-- ⛔ ROLLBACK (verified by execution before commit — not asserted):
--   DROP TABLE IF EXISTS air_country_daily;
-- Nothing else is touched by this migration, so that single statement is the complete undo.
