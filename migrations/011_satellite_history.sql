-- 011_satellite_history.sql
-- migrate:no-transaction
--
-- Historical archive of orbital elements. `satellite_elements` (migration 009)
-- stays the CURRENT fast-serve table the frontend reads (DELETE+INSERT each
-- refresh); this hypertable APPENDS every fetched element so past orbits are
-- never lost — enabling past-instant SGP4 replay later.
--
-- Key (norad_id, epoch, grp): a new TLE has a new epoch, so re-fetching the
-- same element between epochs is a no-op (ON CONFLICT DO NOTHING) while each
-- genuinely new element is preserved. Partitioned on epoch; low volume (a few
-- thousand objects × a few epochs/day), 30-day chunks, compressed old, kept
-- forever. Ground truth is the append-only tle.jsonl raw log.

CREATE TABLE satellite_history (
    epoch        TIMESTAMPTZ NOT NULL,
    norad_id     INTEGER NOT NULL,
    grp          TEXT NOT NULL,
    object_name  TEXT NOT NULL DEFAULT '',
    tle_line1    TEXT NOT NULL,
    tle_line2    TEXT NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT satellite_history_pkey PRIMARY KEY (norad_id, epoch, grp)
);

SELECT create_hypertable('satellite_history', 'epoch', chunk_time_interval => INTERVAL '30 days');

CREATE INDEX satellite_history_norad_idx ON satellite_history (norad_id, epoch DESC);

ALTER TABLE satellite_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'norad_id, grp',
    timescaledb.compress_orderby = 'epoch DESC'
);
SELECT add_compression_policy('satellite_history', INTERVAL '30 days');
