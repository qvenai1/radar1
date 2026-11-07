-- 042_notify_verbosity.sql
--
-- NOTIFY-3 — how much detail this subscriber wants.
--
-- The filters added in 040 answer WHICH alerts a person receives. They cannot answer HOW
-- MUCH, and those are different needs by different people: an analyst wants every hull
-- listed, while most subscribers want to know whether anything happened. Serving both from
-- one rendering means one of them is always wrong — the analyst loses detail or the casual
-- reader mutes the channel.
--
-- ⛔ VERBOSITY CHANGES PRESENTATION ONLY, NEVER ADMISSION. Whether an alert is an EVENT is
-- decided globally (notify_deliveries, 039); whether it belongs to this subscriber is
-- decided by their filters (040). This column decides only how densely what remains is
-- written. In particular:
--   * the INSTANT path is untouched at every level — a high-severity alert interrupts
--     everyone who subscribes to it, because "quiet" is a request for less noise and never
--     a request to be told late about an emergency;
--   * nothing is ever DROPPED by a level, only summarised — the counts stay true, so a
--     collapsed line still reports how many it stands for.
--
-- ⚠ THE DEFAULT IS `balanced`, NOT `detailed`. The bot is public now, and the measured
-- shape of the feed is a standing picture (200+ maritime hulls, satellite passes that are
-- routine by their own baseline) — a new subscriber defaulted to full detail would meet
-- the channel at its loudest and never come back to find the setting.
--
-- Forward-only. Reversal: DROP COLUMN.

ALTER TABLE notify_subscribers
    ADD COLUMN verbosity TEXT NOT NULL DEFAULT 'balanced';

ALTER TABLE notify_subscribers
    ADD CONSTRAINT notify_subscribers_verbosity_ck
    CHECK (verbosity IN ('quiet', 'balanced', 'detailed'));

COMMENT ON COLUMN notify_subscribers.verbosity IS
    'NOTIFY-3 — presentation density only: quiet | balanced | detailed. Never affects '
    'which alerts are admitted (039 decides events, 040 filters recipients), and never '
    'delays the instant path. See backend/notify/render.py DIGEST_CAPS.';
