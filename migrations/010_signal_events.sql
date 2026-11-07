-- 010_signal_events.sql
-- migrate:no-transaction
--
-- Unified OPERATIONAL SIGNAL log across every collector — the "leave nothing"
-- for meta-signals that aren't position/element data but are analytically
-- valuable: feed connect/disconnect (with duration), AIS dark gaps, upstream
-- throttling (CelesTrak 403, OpenSky 429), DB-mirror failures, collector runs.
--
-- Same architecture as the data layers: a hypertable is the derived index; the
-- append-only signals.jsonl raw log (per output dir) is ground truth. Kept
-- forever (no retention policy), old chunks compressed.
--
-- `detail` is JSONB so each kind carries its own shape without schema churn —
-- new signal kinds need no migration.

CREATE TABLE signal_events (
    ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    source    TEXT NOT NULL,          -- 'opensky' | 'airplanes_live' | 'aisstream' | 'celestrak' | ...
    kind      TEXT NOT NULL,          -- 'feed_connect' | 'feed_disconnect' | 'ais_gap' | 'throttle' | 'mirror_fail' | 'collector_run'
    detail    JSONB NOT NULL DEFAULT '{}'::jsonb
);

SELECT create_hypertable('signal_events', 'ts', chunk_time_interval => INTERVAL '7 days');

CREATE INDEX signal_events_source_ts_idx ON signal_events (source, ts DESC);
CREATE INDEX signal_events_kind_ts_idx   ON signal_events (kind, ts DESC);

ALTER TABLE signal_events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'source, kind',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('signal_events', INTERVAL '30 days');
