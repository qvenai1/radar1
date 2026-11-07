-- 040_notify_subscribers.sql
--
-- NOTIFY-2 — from one operator to a PUBLIC SUBSCRIPTION service.
--
-- Migration 039 built a bot with exactly one recipient, and its ledger conflated two
-- questions that only look identical while N = 1:
--
--     "is this an EVENT?"        a property of the SIGNAL     — global, one answer
--     "has THIS PERSON seen it?" a property of the DELIVERY   — per recipient
--
-- With many subscribers those come apart, and conflating them breaks in a specific,
-- unrecoverable way: `notify_deliveries` is written only after a send succeeds, so with a
-- fan-out there is no single send to key that write on. Recording after the FIRST success
-- silently drops the event for everyone who failed; recording after the LAST forces a
-- re-send to everyone whenever one subscriber is unreachable. Neither is acceptable, and
-- no amount of care inside a loop fixes it — the state model is wrong.
--
-- So this migration adds the missing half. `notify_deliveries` (039) is UNCHANGED and keeps
-- its exact meaning: it decides what counts as an event. Delivery moves to an OUTBOX, one
-- row per (recipient, event), which is the standard shape for multi-recipient delivery and
-- the only one that gives per-recipient retry, per-recipient failure, and a drain that can
-- be paced against a rate limit.
--
-- Forward-only. Reversal: DROP the two tables and the added column.

-- ---------------------------------------------------------------------------
-- WHO
-- ---------------------------------------------------------------------------
CREATE TABLE notify_subscribers (
    -- Telegram chat id. TEXT, not BIGINT: group ids are negative and can exceed 32 bits,
    -- and this value is only ever echoed back to Telegram — never arithmetic.
    -- ⚠ PERSONAL DATA. It identifies a person's chat. /delete removes the row outright
    -- (see `status`), and nothing else in this schema stores anything about a subscriber.
    chat_id            TEXT PRIMARY KEY,

    --   active   receiving
    --   stopped  /stop — kept ONLY so /start restores their filters instead of resetting
    --            them. Carries no message traffic.
    --   blocked  Telegram told us the user blocked the bot or the chat is gone (403).
    --            Set by the gateway, never by the user, and distinguished from `stopped`
    --            because it is not a choice the user communicated to us — it is a fact the
    --            API reported, and the two must not be reported to an operator as the same
    --            thing.
    status             TEXT NOT NULL DEFAULT 'active',

    subscribed_at      TIMESTAMPTZ NOT NULL,
    status_changed_at  TIMESTAMPTZ NOT NULL,

    -- ⛔ THE JOIN CURSOR — the per-subscriber answer to the cold-start problem 039 solved
    -- globally. A subscriber never receives an event decided BEFORE they joined. Without
    -- it, the first tick after anyone subscribes would deliver them the whole standing
    -- picture: measured at 225 alerts, ~200 of them maritime leads days old. It is the same
    -- rule as the global baseline, applied per person, and it is what makes /start safe to
    -- expose publicly.
    cursor_at          TIMESTAMPTZ NOT NULL,

    -- Filters. NULL or empty = NO filter = everything, so a subscriber who never touches
    -- them gets the full feed and the default needs no special case anywhere.
    -- ⚠ `countries` holds ISO alpha-2 codes and is matched with the SAME predicate the
    -- /alerts endpoint uses for ?country= (backend/api.py _scoped): an alert matches if the
    -- code appears in its `countries` list. That means alerts with NO country attribution
    -- are excluded by any country filter — measured at 64% of military aircraft, which is
    -- why the bot states the cost out loud when a filter is set rather than quietly
    -- shrinking the feed.
    countries          TEXT[],
    families           TEXT[],

    -- Observability only; never read by delivery logic.
    sent_count         BIGINT NOT NULL DEFAULT 0,
    last_sent_at       TIMESTAMPTZ,
    last_error         TEXT,

    CONSTRAINT notify_subscribers_status_ck
        CHECK (status IN ('active', 'stopped', 'blocked'))
);

COMMENT ON TABLE notify_subscribers IS
    'NOTIFY-2 subscribers. chat_id is PERSONAL DATA; /delete removes the row. cursor_at is '
    'the per-subscriber baseline that stops a new /start from receiving the standing picture.';

-- The fan-out reads exactly this set, every tick.
CREATE INDEX notify_subscribers_active_idx
    ON notify_subscribers (status) WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- WHAT IS OWED TO WHOM
-- ---------------------------------------------------------------------------
CREATE TABLE notify_outbox (
    id               BIGSERIAL PRIMARY KEY,
    chat_id          TEXT NOT NULL
                     REFERENCES notify_subscribers (chat_id) ON DELETE CASCADE,

    -- What this message is about. Carried for observability and for the upsert below —
    -- NOT re-derived at send time. ⚠ Not a foreign key to notify_deliveries: a row here
    -- may outlive the ledger row it came from (30-day prune), and a delivery that failed
    -- must not be deleted by housekeeping on the other table.
    dedup_key        TEXT NOT NULL,

    -- The RENDERED message. Rendered once at enqueue time, deliberately: re-rendering at
    -- send time would mean a message could change between the decision and the delivery,
    -- so what an operator sees in the queue would not be what the subscriber receives.
    body             TEXT NOT NULL,

    queued_at        TIMESTAMPTZ NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NOT NULL,
    last_error       TEXT,

    -- ⛔ ONE PENDING MESSAGE PER RECIPIENT PER EVENT. The upsert on this key refreshes the
    -- BODY of a still-pending row rather than queueing a second message, so a subscriber
    -- who was unreachable for several ticks receives the CURRENT state once — not a stack
    -- of superseded versions of the same alert delivered back to back.
    -- Rows are deleted on success, so this constrains only what is still owed.
    CONSTRAINT notify_outbox_recipient_event_uq UNIQUE (chat_id, dedup_key)
);

COMMENT ON TABLE notify_outbox IS
    'NOTIFY-2 delivery queue — one row per (recipient, event) still owed. Deleted on '
    'success. This is what gives per-recipient retry; notify_deliveries (039) decides only '
    'whether something IS an event.';

-- The drain: due rows, oldest first.
CREATE INDEX notify_outbox_due_idx ON notify_outbox (next_attempt_at, id);

-- ---------------------------------------------------------------------------
-- The gateway's read cursor
-- ---------------------------------------------------------------------------
-- Telegram's getUpdates is acknowledged by OFFSET: passing offset = last_update_id + 1 is
-- what tells the server the previous batch was handled. Persisting it means a gateway
-- restart resumes where it stopped instead of re-processing commands it already answered —
-- a subscriber would otherwise get a second welcome message after every restart.
ALTER TABLE notify_runner_state ADD COLUMN update_offset BIGINT;
