-- 051_alert_triage.sql
--
-- DASH-4a — COLLECTIVE TRIAGE STATE. Several analysts, one shared screen, one shared state.
--
-- ⛔ THE DEFECT THIS REPLACES, AND IT IS A LIE ON THE SCREEN. The dashboard shows a tile
-- labelled «قيد المراجعة» (under review) and fills it from `sev.medium`
-- (`CountryEvents.tsx:333`) — the count of MEDIUM-SEVERITY alerts. The label promises a
-- WORKFLOW state and delivers a bucket of the SEVERITY axis. It read "0" on a live screen
-- carrying 21 alerts, simply because none of them was medium.
-- ⚠ With one analyst that was a bad label. With SEVERAL it is actively harmful: "0 under
-- review" is read as "nobody has started anything", so two analysts open the same vessel.
--
-- ⛔ WHY A TABLE AT ALL — THE FEED HAS NO ROWS TO UPDATE. There is no alerts table:
-- `/api/v1/alerts` rebuilds every family from live queries on each request (see api.py).
-- So state cannot live ON an alert; it must be keyed BY one, and survive the alert being
-- recomputed out of existence and back.
--
-- ⛔ AND THE KEY IS NOT INVENTED HERE. `notify/identity.py::dedup_key` already answers "the
-- stable identity of the THING this alert is about" — built for the bot's "have I sent this
-- already?" ledger, which is the same question. Reusing it means the bot and the dashboard
-- agree on what "the same alert" means; a second, parallel key would let them disagree, and
-- the disagreement would be invisible.
-- ⚠ That module carries an import-time DRIFT GATE (`SEVERITY_BASIS <= _RULES`) so a new
-- family cannot reach it without an identity rule. This table inherits that protection.
--
-- ---------------------------------------------------------------------------------------
-- THE STATES, AND WHY THESE THREE
-- ---------------------------------------------------------------------------------------
--   open          — nobody has claimed it. The default, and it is ABSENCE OF A ROW, not a
--                   stored value: an alert with no triage row is open. Storing "open" for
--                   every alert would make this table grow with the feed rather than with
--                   the work, and 226 alerts a day of "nobody touched this" is not state.
--   reviewing     — someone is on it. ⛔ THE ONE THAT PREVENTS DUPLICATE WORK, and the whole
--                   reason the mislabelled tile mattered.
--   closed        — dealt with. Carries `note` so the reason survives the person.
--
-- ⚠ Elastic (Open/Acknowledged/Closed) and Sentinel (New/Active/Closed) use exactly this
-- shape, and they use it COLLECTIVELY — the state belongs to the item, not to a person,
-- precisely so it survives a handoff between analysts. That is this deployment's case: the
-- owner's decision is a shared screen with no per-analyst identity.
--
-- ⛔ NO `analyst` COLUMN, AND ITS ABSENCE IS THE DECISION, NOT AN OMISSION. There is no user
-- model, no auth and no sessions in this project, and the owner chose to keep it that way.
-- A nullable "who" column would invite a UI to display an attribution the system cannot
-- support, and an empty attribution reads as "nobody" rather than "not recorded".
-- `note` carries whatever a human chooses to say, including their own name.
--
-- ⚠ `change_sig` IS STORED BESIDE THE STATE so a consumer can tell "this is the alert I
-- closed" from "this is the same THING, but it has changed since". identity.py defines the
-- pair as exactly that: dedup_key is what it is about, change_sig is what must move before
-- it counts as an update. A closed alert whose sig has moved is NOT still closed — it is a
-- new statement about a known object, and the UI must be able to see that.
--
-- Additive: a new table only. Nothing existing is read, altered or rewritten.
-- Reversal: DROP TABLE alert_triage;
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS alert_triage (
    dedup_key   TEXT        NOT NULL,
    state       TEXT        NOT NULL,
    change_sig  TEXT        NOT NULL DEFAULT '',
    note        TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT alert_triage_pkey PRIMARY KEY (dedup_key),
    CONSTRAINT alert_triage_state_ck CHECK (state IN ('reviewing', 'closed'))
);

COMMENT ON TABLE alert_triage IS
    'Collective triage state per alert identity (notify.identity.dedup_key). Shared by all '
    'analysts — there is no per-analyst identity in this project by decision. ⛔ ABSENCE OF A '
    'ROW MEANS "open": the table grows with the WORK, not with the feed.';

COMMENT ON COLUMN alert_triage.change_sig IS
    'The notify.identity.change_sig at the moment the state was set. If the live alert''s sig '
    'differs, the underlying thing CHANGED after triage — a closed item whose sig moved is a '
    'new statement about a known object, not a still-closed one.';

-- The dashboard reads "which of these ~226 keys has state?" on every request.
CREATE INDEX IF NOT EXISTS alert_triage_state_idx ON alert_triage (state, updated_at DESC);
