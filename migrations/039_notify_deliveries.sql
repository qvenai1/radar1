-- 039_notify_deliveries.sql
--
-- NOTIFY-1 — the delivery ledger that turns a STATELESS feed into an EVENT STREAM.
--
-- WHY THIS TABLE HAS TO EXIST. `backend/alerts.py` is read-over-signals by design:
-- "not a detector and not a stored table — every alert is computed on request". That
-- is the right shape for a dashboard, which re-reads the whole standing picture every
-- time. It is the WRONG shape for a bot, which must send each thing ONCE. The feed
-- carries no alert id, no first_seen and no created_at, so "what have I already sent?"
-- is a question the feed cannot answer about itself. This ledger answers it, and it is
-- the ONLY new state the notify layer introduces.
--
-- ⛔ IT IS NOT AN ALERT TABLE. It stores no headline, no severity judgement, no
-- geography and no entity facts — only a KEY, a CHANGE SIGNATURE and TIMESTAMPS. The
-- alert contract stays computed-on-request and single-sourced in backend/alerts.py; if
-- this table were ever read as "the alerts", two definitions of an alert would exist
-- and they would drift. Every column below is about DELIVERY, never about the signal.
--
-- MEASURED, on this DB, 2026-08-11 07:35-07:42 UTC, and each measurement changed a
-- design decision rather than confirming one:
--
--  (1) Two /api/v1/alerts polls 65 s apart returned 225 and 228 alerts. Keyed by
--      (signal_type, entity id) the diff was 3 new / 0 gone — the truth: three military
--      aircraft arrived. Keyed by (signal_type, entity id, ts) it was 14 new / 11 gone,
--      because military_aircraft carries the SNAPSHOT time in `ts`, which changes every
--      poll while nothing about the aircraft changes. At the 2-minute tick that is
--      ~11 false re-sends per minute — ~15,000 duplicate messages/day from one family.
--      => the change signature is PER-FAMILY (backend/notify/identity.py), never a hash
--         of the whole alert. This table stores the signature the family chose.
--
--  (2) `satellite_passes` PK is (norad_id, start_time) and start_time lands on a fixed
--      60 s propagation grid (extract(epoch) % 120 is 40 or 100 across all 349 rows).
--      The MINIMUM observed gap between consecutive passes of one satellite is 2 MINUTES
--      (norad 26407: 16:43:40 -> 16:45:40, both over Turkey) — one physical transit split
--      where the AOI mask breaks. A "same pass if within N minutes" heuristic with any
--      N >= 2 min would therefore SUPPRESS A REAL PASS.
--      => the satellite dedup key is the table's own PK. No bucketing, no proximity.
--
--  (3) `computed_at` has exactly 1 distinct value over all 349 rows: the passes table is
--      fully recomputed and replaced each satellite run. A recompute can move a pass by
--      one 60 s grid step, which under (2) reads as a new key and re-sends once. That is
--      accepted and DOCUMENTED rather than heuristically suppressed — bounded, rare, and
--      honest, where the heuristic that hides it silently drops real passes.
--
-- Forward-only, like every migration here. Reversal: DROP the two tables.

-- ---------------------------------------------------------------------------
-- The ledger: one row per THING WE HAVE SENT ABOUT.
-- ---------------------------------------------------------------------------
CREATE TABLE notify_deliveries (
    -- The family's stable identity for the underlying thing — NOT a hash of the alert.
    -- Built by backend/notify/identity.py, which owns the per-family rule and states
    -- the measurement behind each one. Shaped 'signal_type:...' so the family is
    -- readable in the row and a key can never collide across families.
    dedup_key     TEXT PRIMARY KEY,

    -- Which family/route produced it. DENORMALISED FROM dedup_key ON PURPOSE: it makes
    -- "what has this family been doing" answerable without parsing keys, and it is what
    -- the cooldown and digest queries filter on. Never authoritative — identity.py is.
    family        TEXT NOT NULL,
    signal_type   TEXT NOT NULL,

    -- The per-family answer to "has anything about this ACTUALLY changed?". For maritime
    -- it folds last_event_ts + severity + pattern count; for military_aircraft it is
    -- deliberately CONSTANT, because presence is presence and the churning snapshot ts is
    -- exactly the field that must not enter it (measurement 1 above).
    change_sig    TEXT NOT NULL,

    -- When we last SENT about this key, and what we sent about.
    sent_at       TIMESTAMPTZ NOT NULL,
    -- When we last SAW this key in a feed poll, whether or not we sent. This is what
    -- makes absence measurable: a presence session closes when last_seen_at falls far
    -- enough behind now, which is how a military aircraft that left the AOI can be
    -- re-announced if it returns, without re-announcing it every poll while it loiters.
    last_seen_at  TIMESTAMPTZ NOT NULL,

    -- How many messages this key has cost us. Purely observational — it is what makes a
    -- runaway family visible in one query instead of in the channel.
    send_count    INTEGER NOT NULL DEFAULT 1,

    CONSTRAINT notify_deliveries_send_count_ck CHECK (send_count >= 0)
);

COMMENT ON TABLE notify_deliveries IS
    'NOTIFY-1 delivery ledger — what the bot has already sent about. Keys and change '
    'signatures only, never alert content: backend/alerts.py stays the single source of '
    'what an alert IS. See migration 039 for the measurements behind each key rule.';

-- Absence sweeps and digest assembly both scan by family over a recent window; the
-- cooldown check reads (family, sent_at). One composite index serves all three.
CREATE INDEX notify_deliveries_family_sent_idx
    ON notify_deliveries (family, sent_at DESC);

-- The presence-session close scans purely on staleness, across families.
CREATE INDEX notify_deliveries_last_seen_idx
    ON notify_deliveries (last_seen_at);

-- ---------------------------------------------------------------------------
-- Runner state: the cold-start baseline and the digest clock.
-- ---------------------------------------------------------------------------
-- A single-row table (id = TRUE) rather than a settings row per key, because these are
-- two facts about ONE runner and reading them together must be atomic.
CREATE TABLE notify_runner_state (
    id                BOOLEAN PRIMARY KEY DEFAULT TRUE,

    -- ⛔ THE COLD-START BASELINE. The first run ever sees the ENTIRE standing picture:
    -- measured at 225 alerts, 200 of them maritime hulls whose newest event was already
    -- 3-7 days old (0 hulls with last_event_ts inside 72 h; 90 inside 168 h). Sending
    -- those would open the channel with 200 messages of week-old news and teach the
    -- operator to mute it on day one.
    --
    -- So the first run RECORDS the picture and sends nothing — the same discipline
    -- backend/stream.py's Broadcaster already applies ("Baseline only: whoever is
    -- connected already received the current snapshot on connect — pushing it again is
    -- noise"). This column is when that baseline was taken; NULL means it has not been,
    -- and the runner is in cold start.
    baselined_at      TIMESTAMPTZ,

    -- When the periodic digest last went out. The digest fires from inside the ordinary
    -- tick when this falls far enough behind, so there is ONE timer and no second
    -- scheduler to drift against it.
    digest_sent_at    TIMESTAMPTZ,

    CONSTRAINT notify_runner_state_singleton_ck CHECK (id IS TRUE)
);

COMMENT ON TABLE notify_runner_state IS
    'NOTIFY-1 runner state — cold-start baseline stamp + digest clock. Single row.';

INSERT INTO notify_runner_state (id) VALUES (TRUE);
