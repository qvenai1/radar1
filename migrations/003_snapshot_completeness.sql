-- 003_snapshot_completeness.sql
--
-- The collector's JSONL writer flushes a snapshot across two file writes
-- (first line immediately, the rest with the next cycle), so the tailer
-- ingests a snapshot in two batches. "complete" records — at the DB level —
-- whether all of a snapshot's rows are known to be present:
--   * any snapshot older than the newest one is complete (the collector
--     writes snapshots strictly in time order), and
--   * the newest one is complete when its row count matches the count the
--     collector published atomically in latest.json.
-- Ingestion maintains this flag inside the same transaction as the inserts.
-- Consumers that must never serve a half-snapshot (/replay, /live DB
-- fallback, the WS broadcaster) filter on it.

ALTER TABLE snapshots ADD COLUMN complete BOOLEAN NOT NULL DEFAULT false;

-- Existing data: everything except the newest snapshot is definitionally
-- complete; the newest will be closed by the next ingestion pass.
UPDATE snapshots SET complete = true
WHERE snapshot_time < (SELECT max(snapshot_time) FROM snapshots);

CREATE INDEX snapshots_complete_idx ON snapshots (snapshot_time DESC) WHERE complete;
