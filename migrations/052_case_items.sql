-- 052_case_items.sql
--
-- DASH-5 — THE SHARED CASE FILE. The dashboard's output is «تقرير ومتابعة», and until now
-- nothing on it could be KEPT.
--
-- ⛔ THE DESIGN DECISION THAT SHAPES THIS TABLE: IT SNAPSHOTS THE EVIDENCE, IT DOES NOT
-- REFERENCE IT. The alert feed is STATELESS — `/api/v1/alerts` rebuilds every family from live
-- queries on each request — so an alert collected today is simply absent tomorrow once its
-- window rolls past. A case file holding only a `dedup_key` would therefore EMPTY ITSELF as
-- the feed moved on, and a report citing it would reference rows nobody can produce.
--
-- ⇒ Each row stores the claim AS IT WAS WHEN COLLECTED: the entity, the severity, the three
-- calibrated fields (`headline` / `why` / `caveat`) and the timestamps. That is also what an
-- analytic product is supposed to do — ICD 206 requires a source descriptor "when the time of
-- pertinent information in a source is significantly different from the time of publication",
-- which is exactly the gap between collecting an item and writing the report.
--
-- ⚠ THE SNAPSHOT CAN GO STALE, AND THAT IS THE POINT, NOT A DEFECT. `dedup_key` is kept so a
-- consumer can look the item up in the CURRENT feed and show both — what was seen, and what is
-- true now. A snapshot that silently refreshed itself would destroy the record of what the
-- analyst actually based a judgement on.
--
-- ⛔ COLLECTIVE, LIKE THE TRIAGE STATE, AND FOR THE SAME REASON: several analysts share one
-- screen and there is no per-analyst identity by the owner's decision. There is NO `owner`
-- column — a nullable one would invite a UI to display an attribution the system cannot
-- support, and an empty attribution reads as "nobody" rather than "not recorded". `note` is
-- where a human writes who and why.
--
-- ⚠ `caveat` IS SNAPSHOTTED WITH THE REST AND IS NOT OPTIONAL HERE EITHER. models.py calls it
-- mandatory because "a lead read as a finding is the failure mode this project is built to
-- avoid" — and a lead copied into a report, stripped of what it does NOT mean, is that failure
-- in its most durable form.
--
-- Additive: a new table only. Nothing existing is read, altered or rewritten.
-- Reversal: DROP TABLE case_items;

CREATE TABLE IF NOT EXISTS case_items (
    dedup_key    TEXT        NOT NULL,
    -- the snapshot: what the analyst was looking at when they kept it
    label        TEXT        NOT NULL,
    domain       TEXT,
    signal_type  TEXT,
    severity     TEXT,
    headline     TEXT,
    why          TEXT,
    caveat       TEXT,
    countries    TEXT[]      NOT NULL DEFAULT '{}',
    alert_ts     TIMESTAMPTZ,
    note         TEXT,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT case_items_pkey PRIMARY KEY (dedup_key)
);

COMMENT ON TABLE case_items IS
    'The shared case file — evidence kept for a report. SNAPSHOTS the claim rather than '
    'referencing it, because the alert feed is stateless and rebuilt per request: a key-only '
    'row would empty itself as the window rolled past. Collective; there is no owner column '
    'by decision (migration 052).';

COMMENT ON COLUMN case_items.caveat IS
    'The alert''s caveat AS COLLECTED. ⛔ Kept with the rest and never dropped: a lead copied '
    'into a report without what it does NOT mean is the failure mode models.py names.';

COMMENT ON COLUMN case_items.dedup_key IS
    'notify.identity.dedup_key — the SAME key the bot ledger and alert_triage use. Kept so a '
    'consumer can look the item up in the CURRENT feed and show both what was seen and what is '
    'true now. ⚠ The snapshot is NOT refreshed from it: silently updating the record would '
    'destroy the evidence of what a judgement was actually based on.';

CREATE INDEX IF NOT EXISTS case_items_added_idx ON case_items (added_at DESC);
