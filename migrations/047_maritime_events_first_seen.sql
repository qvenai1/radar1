-- 047_maritime_events_first_seen.sql
--
-- DASH-3 (item 3/5) — «متى رأيناه أول مرّة؟»، وهو السؤال الذي لا يستطيع هذا المخطّط
-- الإجابة عنه اليوم.
--
-- ⛔ THE DEFECT, MEASURED BY READING THE DETECTORS' OWN SQL — not inferred. All SIX writers
-- into `maritime_events` end their upsert with `computed_at = now()`:
--
--     scripts/detect_ais_gaps.py          scripts/detect_gnss_anomaly.py
--     scripts/detect_rendezvous.py        scripts/detect_zone_dwell.py
--     scripts/detect_identity_change.py   scripts/backfill_imo_integrity.py
--
-- The detectors run on timers and re-scan a trailing window, and the table's own contract is
-- RECOMPUTE-AND-REPLACE (migration 024: "a re-detection over the same history UPSERTs the
-- same row"). So a gap first detected three weeks ago has its `computed_at` pushed forward
-- on every subsequent pass.
--
-- ⇒ `computed_at` means LAST RECOMPUTED. It has never meant FIRST DETECTED, and the column
--   comment in 024 does not claim otherwise — but every consumer that wanted a "new since I
--   last looked" axis would have reached for it, and would have been wrong.
--
-- ⚠ THIS IS A KNOWN, NAMED FAILURE, not a local oddity. RFC 4287 (Atom) defines
--   `atom:updated` as changing only "in a way the publisher considers significant", precisely
--   because a timestamp touched on every poll carries no information. An ingest that refreshes
--   its own update stamp unconditionally has spent the field.
--
-- ⛔ AND THE LOSS IS ONGOING AND IRREVERSIBLE. "When did we first see this?" cannot be
--   recovered retroactively from any column that exists. Every timer tick that passes without
--   this column is a day of first-sightings that can never be reconstructed. That is why this
--   migration is the first thing built in its item rather than the last.
--
-- ---------------------------------------------------------------------------------------
-- WHAT IS ADDED, AND WHY IT NEEDS NO DETECTOR CHANGE
-- ---------------------------------------------------------------------------------------
-- `first_seen_at` is written ONCE, by the column DEFAULT, and never again. That property is
-- not a convention this migration asks the detectors to honour — it is a consequence of how
-- they are already written, verified across all six before this file existed:
--
--   * every `INSERT INTO maritime_events (...)` uses an EXPLICIT column list, and none of
--     them names this column ⇒ the DEFAULT applies on insert;
--   * every `ON CONFLICT ... DO UPDATE SET` names its columns EXPLICITLY, and a column absent
--     from a SET list keeps its stored value ⇒ a re-detection cannot touch it.
--
-- ⇒ ZERO changes to the detectors. ⚠ And that is also the standing hazard: adding
--   `first_seen_at` to any future `DO UPDATE SET` silently converts it back into a second
--   `computed_at`. A detector that "refreshes" it is reintroducing exactly this defect.
--
-- ---------------------------------------------------------------------------------------
-- ⛔ WHY EXISTING ROWS ARE LEFT NULL AND ARE NOT BACKFILLED
-- ---------------------------------------------------------------------------------------
-- Two backfills were available and BOTH assert something false:
--
--   * `first_seen_at = computed_at` — claims every historical event was first seen at its
--     LAST recompute, i.e. within the detectors' most recent window. The whole archive would
--     then read as first-seen-today, and the first "what's new since I last looked" query
--     after this migration would return the entire history as new. The defect, wearing the
--     fix's clothes.
--   * `first_seen_at = start_ts` — claims we saw the event at the instant it began, which is
--     impossible: an AIS gap is only detectable after it ends, and a detector runs on a timer.
--     It reads as an honest lower bound and is in fact a fabricated one.
--
-- ⇒ NULL, and NULL means exactly one thing: THIS ROW PREDATES FIRST-SEEN TRACKING. That is
--   the same honest-absence idiom the rest of this base uses (`countries = []` is "not
--   attributable", never "nowhere"; `end_ts IS NULL` is "still open", never "zero length").
--   ⚠ CONSUMERS MUST NOT TREAT NULL AS THE EPOCH, and must not treat it as "new". A row with
--   no first-seen is a row whose novelty is UNKNOWN, and unknown is not new. Rendering it as
--   new is the fail-OPEN reading; this project's rule is that a guard fails CLOSED.
--
-- ---------------------------------------------------------------------------------------
-- Additive and forward-only. No column is dropped, altered or re-typed; no existing row is
-- rewritten; no detector, view or query changes behaviour. `vessel_tracks`, `vessel_states`,
-- `mt_vessel_states` and every key are untouched.
-- Reversal: ALTER TABLE maritime_events DROP COLUMN first_seen_at;
-- ---------------------------------------------------------------------------------------

ALTER TABLE maritime_events
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ DEFAULT now();

COMMENT ON COLUMN maritime_events.first_seen_at IS
    'When this event was FIRST written by a detector. Set once by the column DEFAULT and '
    'never updated — no ON CONFLICT ... DO UPDATE SET may name this column, or it degrades '
    'into a second computed_at. NULL = the row predates first-seen tracking (migration 047); '
    'NULL is UNKNOWN novelty, never "new" and never the epoch.';

COMMENT ON COLUMN maritime_events.computed_at IS
    'When this event was LAST (re)computed. Refreshed by every detector on every upsert, so '
    'it advances for events that are weeks old. ⛔ NOT a first-detection time — use '
    'first_seen_at for that (migration 047).';

-- The "what is new since <cursor>" query: newest first-sightings, narrowed by type and
-- classification the same way maritime_events_type_class_idx serves the existing feed.
-- ⚠ Partial on NOT NULL: the NULL rows are the pre-migration archive, they can never satisfy
-- a first-seen cursor, and excluding them keeps the index proportional to what it answers.
CREATE INDEX IF NOT EXISTS maritime_events_first_seen_idx
    ON maritime_events (first_seen_at DESC, event_type)
    WHERE first_seen_at IS NOT NULL;
