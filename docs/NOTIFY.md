# NOTIFY — the update bot, as built

> **NOTIFY-2 (2026-08-11) made this a PUBLIC SUBSCRIPTION service.** Any Telegram user sends
> `/start` and receives alerts in their own chat. §§1–6 below describe the event layer,
> which is unchanged; **§§9–13 describe the public layer** — the state split that made a
> fan-out correct, the filters, the command surface, and the licence/privacy position.


**Measured:** 2026-08-11 **07:35–07:42 UTC** on `gis-syr-ThinkCentre-neo-50t-Gen-5`, against
this DB and this running API. Every figure below was produced by a command in the session
that wrote it, and each command is cited so any value can be re-measured. Nothing here is
restated from a prompt or carried from another project (§0 of `PROJECT_STATE.md`).

**What it is.** A send-only Telegram bot that posts **new and changed** alerts as messages.
It never reads: no `getUpdates`, no webhook, no commands, no inbound port. The only traffic
is an outbound HTTPS POST, so the host needs no public address and nothing on the internet
can reach it — it works unchanged behind the NAT this box sits behind.

---

## 1. The problem, and why it is not a filter

`backend/alerts.py` is **read-over-signals** by design — *"not a detector and not a stored
table — every alert is computed on request"*. Every poll returns the whole standing picture,
and the feed carries **no alert id, no `first_seen`, no `created_at`**.

So "send updates only" cannot be expressed as a filter over the feed. The feed cannot tell
you what it already told you. The bot has to supply the identity the feed does not have.

**The measurement that defines the whole design.** Two polls of `/api/v1/alerts`, 65 seconds
apart:

```
curl -s "http://127.0.0.1:8000/api/v1/alerts?window_hours=24"
```

| keyed by | new in poll 2 | gone |
|---|---|---|
| `(signal_type, entity id)` | **3** | 0 |
| `(signal_type, entity id, ts)` | **14** | 11 |

The truth is 3 — three military aircraft entered the AOI. The other 11 are the *same*
aircraft: their `ts` is the **snapshot time**, which advances every poll while nothing about
the aircraft changes. At the 2-minute tick that is ~11 phantom messages a minute, about
**15,000 duplicate messages a day from one family**.

A whole-alert hash cannot distinguish "a new observation" from "the feed was read again",
which is why there is no such hash anywhere in `backend/notify/`.

---

## 2. Identity — per family, and why each rule is what it is

`backend/notify/identity.py`. Two strings per alert: `dedup_key` ("which thing?") and
`change_sig` ("has anything actually changed?").

| family | key | signature | why |
|---|---|---|---|
| `emergency_squawk` | aircraft | **`summary`** (carries the squawk code) | the code lives only in prose, and without it a **7500 would be suppressed** by an earlier 7600 on the same aircraft |
| `military_aircraft` | aircraft | **constant** | presence is the whole claim; every field that moves poll-to-poll describes the *reading*, not the claim. Re-armed by absence, not by change |
| `military_satellite` | **`(norad, start_time)`** | constant | that pair is `satellite_passes`' own primary key |
| `maritime_composite` | hull (IMO) | `last_event_ts` + `severity` | structured only — prose is excluded on purpose |
| `implausible_position` | vessel (MMSI) | `last_ts` | a real new observation each time; rate-limited by policy, not by identity |

**Two measurements overrode what reasoning would have chosen:**

**(a) The satellite proximity heuristic is unsafe.** The obvious rule — *"same pass if the
start time is within N minutes"* — would absorb recompute drift. But the **minimum observed
gap between consecutive passes of one satellite is 2 minutes**:

```sql
-- norad 26407: 16:43:40 → 16:45:40, both over Turkey
```

That is one physical transit split where the AOI mask breaks. Any `N ≥ 2 min` would
**suppress a real pass**. So the key is the table's primary key, and the consequence is
accepted and stated rather than hidden: `satellite_passes` is fully recomputed each run
(`computed_at` had exactly **1 distinct value** across all **349** rows) on a 60 s grid
(`extract(epoch) % 120 ∈ {40, 100}`), so a recompute that moves a pass by one grid step
re-announces it **once**. Bounded, rare, INFO-severity — and better than dropping real passes.

**(b) Prose must stay out of signatures.** 200 maritime alerts stand in the feed at once,
and their `headline` / `why` / `caveat` / `severity_basis` are strings this project actively
edits — the «GNSS» rename touched every displayed string. Folding prose into a signature
would make one copy edit re-blast all 200. `last_event_ts` + `severity` is sufficient: a
hull cannot gain a pattern type without gaining an event, and an event moves `last_event_ts`.

**The failure direction is chosen per family by what the mistake costs** — emergency
over-sends, military under-sends, maritime stays structured.

---

## 3. Routing — the existing severity axis, not a new one

`backend/notify/policy.py`. Routing reads **`severity`**, never `importance`.

That is a deliberate refusal. `importance` exists and would have been easy to use — it lifts
military presence to the top of a country card. But `alerts.py` is explicit: *"Do not colour
a card by it, do not sum it, and never present it as risk."* **An interrupt is a risk
assertion.** Routing on importance would push military presence to instant, against both the
operator's choice and the standing commitment that presence is *"informational, NEVER a
threat"*.

| family | severity | route | measured now |
|---|---|---|---|
| emergency | HIGH | **instant** | 0 |
| maritime composite | its own rank | **instant** at high, else digest | 200 (all high) |
| implausible | LOW | digest, 6 h cooldown per vessel | 0 |
| military air + satellite | INFO | digest | 25 |

**Cold start sends nothing.** The first run records the whole picture and delivers one
explanatory message. Measured reason: **225 alerts standing, 200 of them maritime hulls
whose newest event was already days old** — 0 hulls with an event inside 72 h, 90 inside
168 h, 319 multi-pattern hulls total. Delivering that as "updates" would open the channel
with a flood of week-old news. It is the same rule `stream.py`'s Broadcaster already applies
on connect.

**The burst valve.** At most 10 instant messages per tick; the overflow **spills into one
digest sent in the same tick** — never dropped, never delayed to the next hour, and the
digest states how many spilled. It exists because the instant path is severity-gated, not
volume-gated: one detector catch-up could rank a large batch high at once (0 hulls inside
72 h but 90 inside 168 h).

### 3.1 A flood found by dry run, not by review

The first end-to-end run against the live feed exposed a real defect, and it is recorded
here because the correction is itself a finding.

Absence was allowed to re-arm **every** family, not only the presence ones. Ticks simulated
63 minutes apart therefore re-announced the **entire standing picture**:

| tick | before the fix | after |
|---|---|---|
| cold start | 1 message (225 baselined) | 1 message |
| +2 min, unchanged feed | 0 | 0 |
| **+1 h 05** | **24 messages** — 220 alerts re-routed as "returned" | **1** digest, 12 items |
| **+8 h (outage)** | *(same flood)* | **1** digest, 12 items |

**Why review missed it.** At the 2-minute production tick `last_seen_at` never goes stale,
so the path is unreachable in normal running. It fires only when the **bot itself** has been
down longer than the session window — a reboot, an outage, a full disk — which is precisely
the moment a 200-message burst is least welcome and least likely to be noticed in testing.

**The rule now.** Only families whose signature is **constant by design** may be re-armed by
absence (`identity.PRESENCE_SIGNALS`), because only they have no other way to become news.
That is `military_aircraft` alone. `military_satellite` is excluded despite also having a
constant signature — its key already contains the pass start time, so a "return" could only
re-announce the same predicted pass. The remaining three carry meaningful signatures, so
anything that genuinely changed during an outage is still delivered on the first tick back.
The 12 that legitimately re-announce above are military aircraft, in one digest.

Detonated by `test_an_outage_does_not_reblast_the_standing_picture`.

---

## 4. Messages — the existing Arabic, never re-written

`backend/notify/render.py` authors **no Arabic about any signal**. Every claim — what was
observed, why it ranks there, what it does *not* mean — is taken verbatim from the
`headline` / `why` / `caveat` the alert already carries. Those three fields are separately
calibrated per family across five worked items (BE-SUMMARY-1..5); re-phrasing them in a
surface nobody reviews would fork that calibration. The only authored Arabic is chrome:
section labels, a severity word, a count.

**The caveat is never dropped.** A body too long to fit is *continued* into a second
message; nothing in the module discards content, and the tests assert it.

> ⚠ **Since going live, the per-message caveat is the SHORT form** (`render.SHORT_CAVEAT`).
> Measured cause: the full text was **66–77 % of every message** — maritime 454 of 679
> chars, military air 424 of 627, satellite 740 of 956 — against 109–167 chars of actual
> observation, and it is near-identical every time (200 maritime alerts share 17 caveats).
>
> **This is not a relaxation of the honesty rule; it is what makes it work.** A reservation
> restated at full length in hundreds of messages is not read — it is scrolled past, and it
> trains the reader to scroll past the message with it. The full text is stated **once**
> where a newcomer actually reads it (`commands.CAVEAT_BLOCK` at `/start`, repeated in
> `/help`), and every message still names the same limit in one line, lifted verbatim from
> that family's own existing caveat. An unknown family falls back to the FULL text, so the
> chain errs toward more reservation, never none.
>
> Result: single alerts **35–53 % shorter**; the hourly digest **12,404 → 3,430 chars, 4
> Telegram messages → 1**.

> ⛔ **NEWS-3 (2026-08-22) — THE HEADING ABOVE IS NO LONGER TRUE, AND THE REVERSAL IS THE
> OWNER'S.** The bot now applies a **newsroom register** on the way out
> (`backend/notify/newsroom.py`). §4's rule — "the existing Arabic, never re-written" —
> held from NOTIFY-1 to today; it is recorded rather than overwritten because the reason
> behind it has not gone away, only been bounded.
>
> **What changed, precisely.** It is a **term substitution**, not a re-composition. Nothing
> in the module builds a sentence: the calibrated string arrives whole, 37 jargon terms are
> swapped for their news-register equivalents, and the sentence leaves with its subject,
> verb, hedge and scope untouched. «سجلّ هذا البدن» → «السجلّ الملاحي للسفينة»,
> «شذوذ إشارة الملاحة» → «اضطراب في إشارة الملاحة», «سُجّل مكوثها» → «سُجّل تمركزها».
>
> **What did NOT change, and this is why the item is safe.** `summaries_ar.py` and
> `alerts.py` are untouched, so `/api/v1/alerts` returns exactly what it returned before
> and the frontend renders exactly what it rendered before. That is not tidiness:
> «شذوذ إشارة الملاحة» is the outcome of RANK §11.3/§13.1 and shipped in 58b842b / 9f6e7de,
> and `frontend/` was under active parallel work when this landed. The bot got its register;
> nothing else moved.
>
> **The line the owner drew.** The requested spec offered replacements that cross from
> DESCRIPTION into INFERENCE — «تلاعب بالبيانات» (an act with an actor), «احتمال تشويش
> وتزييف إحداثيات» (a cause), «التصاق … مناورة شحن/تفريغ» (a transfer the detector never
> measures). Rule 3 of `summaries_ar.py` forbids exactly this and the decision was
> **«حرّر الأسلوب، واحفظ الوصف»**: each is rendered fluently while staying an observation,
> and `newsroom.FORBIDDEN_INFERENCE` is the gate that keeps a later "small" addition from
> crossing the line quietly. **The hedges survive** — «بحكم التعريف» reads as boilerplate and
> is replaced, but only by a phrase carrying the same denial («تلقائياً لأنّ رمز الطوارئ
> نشط»), because its job is to deny that anyone judged *this* flight.
>
> **A pre-existing defect this item fixed in passing, in its own layer.**
> `NEWS_LEDE_EMERGENCY` is «طائرة تُبلغ {meaning}» and every `SQUAWK_MEANING_AR` value opens
> with its own verb, so the shipped lede reads «طائرة تُبلغ أعلنت …» — two finite verbs in a
> row. Fixed in `newsroom.py`, **not** at the template, because the template also leaves
> through the API. The real fix belongs upstream as its own decision.
>
> **Gate:** `tests/test_notify_newsroom.py` — 130 assertions. Every shipped template is run
> through the register and audited for a surviving banned term *and* for an introduced
> inference term; the caveat is asserted present in every rendered brief; `edit` is asserted
> idempotent (proving the single-pass scan never compounds one rule's output into another's
> input); and **both audits are detonated** against the pre-edit text, because a check that
> can only pass is not a check. Full suite: **1,040 passed**, 6 pre-existing DB-bound
> failures unrelated to this work (proven by re-running them with `edit` stubbed to a no-op).
>
> ⚠ **Aliases are input, not output.** `/filters` still accepts «شذوذ» — deleting it would
> break the filter for anyone who learned the word from a message this bot sent them. The
> NEWS-3 words were added **alongside**.

> ⛔ **NEWS-4 (2026-08-22) — THE DISPATCH TEMPLATE, AND THE RESERVATION LEAVES THE WIRE.**
> The owner restructured this surface into news dispatches for «القارئ العادي». Two templates
> now govern every message:
>
> ```
> 🔴 عاجل | طوارئ جوية              🟡 رصد بحري
> أعلنت الطائرة X حالة طوارئ         رصد تمركز السفينة Y قرب منشآت
> عامة في أجواء تركيا.               قبالة سواحل إسرائيل.
>                                    🕒 11:40 بتوقيت دمشق
> مستوى التنبيه: مرتفع
> 🕒 12:12 بتوقيت دمشق
> ```
>
> **Removed from the wire:** the platform name (`المصدر: OpenSky`, `latest.json`), the
> vintage and data-nature lines, the navigational code (`رمز 7700` — the *meaning* stays),
> the rank justification (`التصنيف: … — الرتبة مرتفعة تلقائياً`), and the duplication
> between the lede and the headline, which restated one fact twice by construction.
>
> ⛔ **AND THE PER-MESSAGE RESERVATION, WHICH IS THE ONE TO KNOW ABOUT.** §4 above called the
> caveat mandatory and the section caveat is gone from the bulletin too. The honesty chain
> now rests **entirely** on `commands.CAVEAT_BLOCK` at /start and /help.
> `test_the_reservation_has_exactly_one_home` asserts that block is present, substantial, and
> reachable from both surfaces — it is load-bearing in a way it was not when a caveat also
> rode on every message. **The fallback brief keeps its caveat**, deliberately: that path
> renders an alert the template could not compose, which is where the reader has least
> context and the reservation is worth most.
>
> **How it avoids inventing anything.** Every slot is filled from a field the alert already
> carries — `entity_ref.label`, `countries`, `ts`, `severity` — or from a **closed-set lookup**
> over a machine-readable one (`summary` opens «squawk 7700 — …», written by
> `build_emergency_alerts`; the five `PATTERN_AR` labels are matched as membership, not
> parsed as prose). Where a fact is absent, `newsroom.dispatch` returns **None** and the
> message degrades to the NEWS-3 brief. Nothing is dropped and no gap is filled with a word.
>
> ⚠ **The two-vessel form cannot be rendered and is not faked.** «رصد تمركز السفينتين X وY»
> needs the rendezvous COUNTERPART. The pairing exists in the database
> (`detail ->> 'partner_mmsi'`, served by `/api/v1/rendezvous`) but is **not a field on
> `Alert`**, so a message cannot reach it. The singular form renders instead;
> `newsroom._DUAL_VESSEL` is where the dual form goes the day the counterpart lands on the
> model. Joining two vessels that merely share a digest would assert a rendezvous nobody
> detected.
>
> ⚠ **Two smaller departures, both stated rather than absorbed.** (1) The dispatch prints
> **Damascus time alone**; `_local_pair`'s "UTC is never dropped" rule was written for an
> operator who must find the row, and the digest and the fallback brief still print the pair.
> (2) The **level line is on air and not on sea**, because that is what the two supplied
> templates say — `newsroom.SHOW_LEVEL` is a per-family switch, one line to change.
>
> **Gate:** `tests/test_notify_newsroom.py` (140 assertions) now also asserts the four
> removals as removals, that no path names a platform or a file across **every** family and
> **both** render paths, that a missing fact degrades instead of being invented, that an
> unattributed alert states no place, that 7500 keeps its passive verb, and that every
> `PATTERN_AR` entry has a dispatch clause. Seven tests in `test_notify.py` and five in
> `test_pass_baseline.py` were rewritten to the new contract, each keeping its original
> subject — the change is recorded in each docstring rather than replaced silently.
> Full suite: **1,050 passed**, the same 6 pre-existing DB-bound failures.

Two measurements shaped the format:

* **One alert always fits.** Longest rendered single alert: **1,110** of Telegram's 4,096
  units (satellite, whose caveat carries the epoch clauses); maritime p50 is **757**. So the
  instant path is one alert per message with ~3.7× headroom.
* **Caveats collapse, which is what makes a digest both honest and short.** Distinct caveats
  per family: military aircraft **14 → 1**, satellites **14 → 8**, maritime **200 → 17**. So
  a digest block carries **every distinct caveat of the alerts it lists**, each exactly once.
  Deduplication is over the exact string, so no caveat is ever attributed to an alert that
  did not carry it.

---

## 5. Delivery — at-least-once, on purpose

**A delivery is discharged only when it demonstrably happened.** A crash in between re-sends
one message; the reverse order would mark an alert delivered that nobody received —
silently, permanently, and invisibly. **A duplicate is noticed and forgiven; a hole is
neither.**

> ⚠ **NOTIFY-2 moved where that guarantee lives.** With one recipient it was "send, then
> record" against `notify_deliveries`. With a fan-out there is no single send to key that
> write on, so the debt moved to `notify_outbox` — one row per (recipient, event), deleted
> only when *that person's* send returns. See §9. The guarantee is unchanged and is now
> **per recipient**: one unreachable subscriber costs nobody else their message, and does
> not lose their own.

`test_a_failed_delivery_stays_owed_to_that_person_only` detonates this: one recipient's send
fails, their row survives with its backoff, and the other's is discharged.

**The rate limit is obeyed, not guessed.** Telegram's 429 carries `parameters.retry_after` —
the exact wait the server wants — and it is honoured. A conservative client-side floor keeps
normal operation away from the limit; the 429 handler is the real limiter.

**One family's failure is not the tick's failure**, matching how the endpoint *"degrades
family-by-family — never a 500"*.

---

## 6. State

Migration **039** — `notify_deliveries` (key, family, signature, `sent_at`, `last_seen_at`,
`send_count`) and `notify_runner_state` (baseline stamp, digest clock, and — since 040 —
the gateway's `update_offset`).

Migration **040** adds `notify_subscribers` and `notify_outbox` (§9). `notify_deliveries` is
**unchanged**: it answers *"is this an event?"*, and only that.

It is **not an alert table**: no headline, no severity judgement, no geography, no entity
facts. Keys and timestamps only. `backend/alerts.py` stays the single definition of what an
alert *is*; two definitions would drift.

`last_seen_at` is what makes absence measurable, and it is touched for **suppressed keys
too** — otherwise a loitering aircraft's presence session would expire while it sat plainly
in the feed, and the next tick would re-announce it (the phantom re-send arriving through
the absence door). Keys unseen for 30 days are pruned; a hull returning after a month is
genuinely news again.

Postgres rather than a local file for three reasons that are all about failure: it survives
restarts and redeploys, it rides the existing `scraplan-backup` timer so a restore brings the
bot's memory back with the data, and it is queryable — *"why did the channel go quiet"* is
answered with SQL instead of a guess.

---

## 7. Deployment

```bash
# 1. configure — see .env.example. Only TELEGRAM_BOT_TOKEN is required.
# 2. install BOTH units
cp deploy/systemd/scraplan-notify.{service,timer} ~/.config/systemd/user/
cp deploy/systemd/scraplan-notify-gateway.service  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now scraplan-notify.timer            # the tick
systemctl --user enable --now scraplan-notify-gateway.service  # the gateway
# 3. watch
journalctl --user -u scraplan-notify-gateway.service -f
```

**Two processes, and the split is load-bearing.** The **tick** is a oneshot every 2 minutes
that decides events and enqueues them; it holds nothing between runs (§6.10 records memory
as this host's tightest resource) and never touches Telegram. The **gateway** is resident,
long-polls for commands, and is the *only* sender — Telegram's rate limits are per-**bot**,
so two senders would each respect a client-side floor and together break the real ceiling,
with neither able to see the other's 429s.

The 2-minute tick is a **latency budget**, not a poll rate: it bounds how late a
high-severity alert can be decided. Delivery itself is continuous — the gateway drains the
outbox within seconds of a row appearing.

Useful queries:

```sql
-- who is subscribed, and how they left
SELECT status, count(*) FROM notify_subscribers GROUP BY status;
-- delivery backlog (should be near zero; a rising number means the gateway is stuck)
SELECT count(*), min(queued_at) FROM notify_outbox;
-- anything failing repeatedly, and why
SELECT chat_id, dedup_key, attempts, last_error FROM notify_outbox
 WHERE attempts > 0 ORDER BY attempts DESC LIMIT 20;
-- what has been decided as an event, by family
SELECT family, count(*), max(sent_at) FROM notify_deliveries GROUP BY family;
-- the runner's clocks + the gateway's read cursor
SELECT * FROM notify_runner_state;
```

---

## 8. What this does not do

* **No new judgements.** No detector, no severity of its own, no third ordering axis. It
  relays what `alerts.py` already decided, with the honesty fields attached.
* **No conversational surface.** The gateway acts on its seven commands and ignores
  everything else; it does not answer questions, accept queries, or expose the API.
* **It inherits whatever the API serves, including staleness.** ⚠ At the time of writing the
  running `scraplan-server.service` started **2026-08-08**, before the «GNSS» rename landed
  (**2026-08-10**, commit `58b842b`), so the live feed still serves the pre-rename Arabic.
  A deployment fact about the server, not the bot — but the bot relays it verbatim until the
  server is restarted.

---

# NOTIFY-2 — the public layer

## 9. Going public split one question into two

Migration 039's ledger conflated two questions that are only identical while there is one
recipient:

| question | scope |
|---|---|
| **"is this an EVENT?"** | a property of the **signal** — global, one answer |
| **"has THIS PERSON seen it?"** | a property of the **delivery** — per recipient |

`notify_deliveries` is written *after* a send succeeds. With a fan-out there is no single
send to key that write on, and both obvious repairs lose messages:

* record after the **first** success → everyone who failed loses the event, permanently
* record after the **last** success → one unreachable subscriber re-sends to *everybody*

No amount of care inside the loop fixes it; the state model was wrong. So migration 040 adds
`notify_outbox` — one row per (recipient, event), deleted only when *that person's* send
returns. `notify_deliveries` is **unchanged** and still decides only what counts as an event.

That also makes the rate limit tractable: with N subscribers one event is N messages, so
delivery has to be paced and resumable rather than attempted inside one tick. A queue is
both for free.

## 10. Filters, and the 64 % that a country filter hides

`/countries` and `/families`, ANDed (each filter *reduces* traffic — the union reading would
make selecting a filter increase it). Empty means no filter, so a fresh `/start` needs no
configuration.

**The country predicate is the endpoint's own**, character for character with
`api._scoped`: an alert matches if the code appears in its `countries` list. Writing a more
"helpful" rule here would make the bot and the dashboard disagree about which alerts belong
to Syria, with no way to tell which was lying.

⚠ **Measured consequence, and it is large: 64 % of military aircraft carry no country
attribution at all** (9 of 14 in the live feed) — they sit outside the seven AOI polygons.
Any country filter therefore removes most of that family. That is *correct*, but it is not
something a subscriber would guess, so the bot **says so at the moment the filter is set**
(`commands.COUNTRY_FILTER_NOTE`) rather than quietly shrinking their feed. A filter that
silently removes two thirds of a family is what gets discovered months later as "the bot
stopped reporting aircraft".

Confirmed in the end-to-end dry run: of 9 digest items, the unfiltered subscriber received
all; the `SY`-filtered subscriber received **0**.

## 11. The command surface

| command | effect |
|---|---|
| `/start` | subscribe · welcome + honesty block + a **counts** summary of the current picture |
| `/status` | their subscription and filters |
| `/countries` | show the seven / set a filter · accepts `SY`, `Syria`, **`سوريا`**, `الكل` |
| `/families` | show the four / set a filter |
| `/stop` | pause · keeps filters **only** so `/start` restores them |
| `/delete` | erase the row outright; outbox rows cascade |
| `/help` | the commands + the honesty block |

**`/start` delivers a summary, not a backlog.** It reports per-family counts plus the highest
-severity headline *their filters admit* — it does not deliver the standing alerts. This is
the cold-start rule applied per person, and it is what makes `/start` safe to expose
publicly.

**The honesty block runs before any alert can arrive.** Every alert already carries its own
caveat, calibrated for an analyst who knows a composite is a lead. A stranger who typed
`/start` does not know that and will not infer it — so the three standing commitments
(military presence is not a threat judgement; a composite is not proof; AIS/ADS-B are
spoofable) are stated in plain language at `/start` and `/help`, and pinned by
`test_a_newcomer_is_told_what_the_alerts_are_not`.

⚠ **An Arabic-facing bot must accept Arabic country names**, and it did not: `country_iso`
holds the *English* names (it is the filtering layer; the Arabic names live in
`summaries_ar`). `/countries تركيا` was rejected as unknown — the default way an Arabic
speaker names a country — while every test written in ISO codes passed. Found by smoke test,
not by review. The map is now inverted from `AOI_ISO_TO_NAME_AR` so it cannot drift, and
**no aliases beyond the project's own seven labels** are added: every extra alias is this
layer asserting that two names denote the same place, a claim with political weight that
nobody asked it to make. An unrecognised name gets the menu, never a guess.

## 12. Blocked users, privacy, and licence

**A blocked user is not a failed send.** When someone blocks the bot or deletes their
account Telegram answers 403 forever; retrying burns the rate limit that live subscribers
are queued behind and can never succeed. `TelegramBlocked` is deliberately **not** a
subclass of `TelegramError`, so no retry handler can catch it. Such a subscriber is marked
`blocked` — never `stopped`, because they did not tell us to stop; the API told us they are
unreachable, and an operator must be able to tell those apart — and their queue is dropped.

**Privacy.** `chat_id` is the only personal data stored, and `notify_subscribers` is the only
place it lives. `/stop` pauses and retains *only* the filters, so `/start` does not reset
what they chose. `/delete` removes the row; outbox rows cascade, so the erasure is complete
with no second place to sweep.

**Licence — cleared before going public.** GFW data is CC-BY-NC and quarantined; it does
**not** feed `maritime_events` and therefore never reaches a subscriber. Everything the bot
sends comes from this project's own detectors over AISStream/MarineTraffic plus public
ADS-B and TLE sources.

## 12.1 A bug that reached production: the drain did not chunk

Recorded because the correction is itself a finding, and because it is the second time in
this layer that a defect was invisible to review and to the whole test suite.

`render.chunk` exists precisely so no message is ever truncated or rejected for length. The
command path used it. **The outbox drain did not** — it called `tg.send(item.chat_id,
item.body)` on the raw body. Two lines apart in the same file, one chunked and one did not.

It surfaced on the **first hourly digest**, 65 minutes after go-live:

```
last_error : http 400: Bad Request: message is too long
body length: 12,404   (Telegram's limit: 4,096)
attempts   : 3, backing off — two subscribers receiving nothing
```

**Why nothing caught it.** Every unit test asserted `chunk()`'s own behaviour, and the
rolled-back dry runs used a fake transport that accepted any string — so no test ever
enforced the property Telegram actually enforces: *no part reaching the wire may exceed the
limit*. The single-alert path was also safe by measurement (longest rendered alert: 1,110
units), so only the digest — whose size is unbounded in the number of items — could trip it.

**The fix is structural, not a remembered call.** Both paths now go through
`gateway.send_message`, the only function permitted to send, and it chunks. The regression
test drives a transport that **asserts on the part size**, so a future path that forgets to
chunk fails on the property rather than on a mock's tolerance.

Chunking stays at SEND time rather than enqueue time: one outbox row remains one logical
message, so its parts arrive in order and a retry re-sends the whole thing. That can
duplicate earlier parts on a mid-message failure — the trade this layer already declares
(at-least-once), and the better half of it, since splitting into independently-retried rows
would fix the duplicate and break the ordering.

## 13. What the public layer still does not do

* **No access control.** Anyone who finds the bot can subscribe — that was the requirement.
  There is no allowlist, approval step, or per-user quota.
* **No inline keyboards.** Filters are set by typed commands, with `setMyCommands`
  registering the menu so they autocomplete.
* **No per-subscriber rate cap.** The burst valve and the digest bound volume globally, but
  a subscriber with no filters during a busy period can still receive a lot.
* **Fan-out is not sharded.** One gateway drains one queue. Fine for the scale a single
  home host serves; a large subscriber base would want the drain parallelised per chat.

---

# BASELINE-1 — «unusual» as a measurement

## 14. The sentence that could not be written

A reviewer asked, correctly, for the digest to say «ضمن النمط المعتاد» instead of listing
fourteen passes. That sentence needs a baseline, and **this project had none** — measured
before building anything:

| source | what it holds | why it cannot answer "usual" |
|---|---|---|
| `satellite_passes` | **one distinct `computed_at`** across all rows | fully recomputed and replaced each run — no history |
| `satellite_elements` | 800 rows / 777 satellites = **1–2 epochs each** | TLEs replaced, not accumulated — no element history |

The wording could have been added in ten minutes and would have been an **invention**. What
made it derivable instead: **SGP4 propagates backward as readily as forward.**
`scripts/build_pass_baseline.py` runs each object's *current* element over the preceding 14
days through the **same `classify_passes()`** the forward prediction uses — reuse is the
point, since a second pass definition would make "usual" and "today" measure different
things.

⛔ **It is derived, not observed.** No sensor saw these passes; `method` says so in every
row and the alert's caveat repeats it to the reader. It characterises **orbital geometry** —
how often this track crosses the AOI and how close it comes — never "what happened". Only
distributions are stored: SGP4 error grows with |t − epoch| both ways, so a count and a
quantile over ~27 passes are robust where an individual reconstructed pass would not be.

## 15. What it refuses to conclude

`unknown` is a real verdict and reaches the reader as itself. A missing or thin baseline
quietly rendering as "routine" is how a system starts asserting normality it never
measured — worse than the noise it replaced.

| verdict | when | in the message |
|---|---|---|
| `routine` | inside its own usual spread | «ضمن النمط المعتاد لهذا الجسم (وسيط اقترابه N كم)» |
| `routine` | **geostationary** | «جسم ثابت بالنسبة للأرض ومستقرّ فوق هذا النطاق دوماً» |
| `notable` | at/inside its own **p10** | «**أقرب من المعتاد لهذا الجسم**» |
| `notable` | crosses a country it usually does not | «يعبر نطاقاً لا يعبره عادةً» |
| `unknown` | no baseline · sample < 8 · no distance | «لا سند كافٍ بعد لمقارنته بنمطه المعتاد» |

**The geostationary case was found by running it, not by design.** The first build returned
`n_passes = 1` for 7 of 13 objects — SKYNET 4C, SYRACUSE 3A/4A, RADUGA-1M 3, ATHENA-FIDUS,
COSMOS 2520, TIBA-1 — and the sample floor would have called all seven "not enough history".
Wrong twice: `classify_passes` emits *one persistent row* for a geosync object because its
sub-point is fixed, and a satellite parked over the region is the most routine thing in the
catalogue. It is settled before any sample counting.

⛔ **A third axis, and not a threat one.** `severity` stays INFO for every satellite pass;
atypical is not dangerous. Do not colour by it, do not sum it.

## 16. The payoff

Routine crossings are **counted, not listed** — they are still reported, they just stop
costing a line each. `unknown` and `notable` are always listed in full.

```
before   6 satellite alerts, each ~550 units, listed in full
after    250 units total
         «• 6 ضمن النمط المعتاد لأجسامها (DMSP 5D-3 F16، CORIOLIS، … و2 غيرها)»
```

Rebuilt daily at 07:20 by `scraplan-pass-baseline.timer` — one hour after the 06:20 element
refresh, because the baseline is propagated *from* the current TLE and must never race it.
Cost measured: 84 satellites × 20,161 samples = **1.4 s**.

---

## 17. NOTIFY-3 — verbosity, and the aggregated digest

`/level` — **هادئ · متوازن · تفصيلي** (migration 042). Presentation density only.

⛔ **It never changes admission, and never delays the instant path.** Whether something is
an event is decided globally (039); whether it belongs to this person is decided by their
filters (040). The level decides only how densely what remains is written — a
high-severity alert interrupts everyone at every level, because *quiet* is a request for
less noise, never to be told late about an emergency. `render_alert` takes no `verbosity`
parameter at all, so that property is structural rather than a rule someone must remember.

| level | listed per section | routine passes | empty-ish digest |
|---|---|---|---|
| هادئ | 3 | collapsed to a count | **not sent** if everything is routine |
| متوازن *(default)* | 5 | collapsed to a count | sent |
| تفصيلي | all | **listed in full** | sent |

**Nothing is dropped, only summarised.** Every cap prints what it stands for («و N أخرى»),
so a collapsed line still reports its true count — otherwise the section header's number
would disagree with the lines beneath it.

Each section also carries a **geographic breakdown** — «يلامس: تركيا 5 · العراق 2 · بلا
إسناد جغرافي 4» — a count of a field the alerts already carry. ⚠ These are *touches*, not a
partition: an alert answers to every country it crosses, so they legitimately exceed the
section total, and unattributed alerts are counted explicitly rather than dropped (64 % of
military aircraft).

### 17.1 Two defects a live render caught that review did not

* **`quiet` had a cap of 0**, which suppressed the one pass flagged *closer than usual* into
  «و1 أخرى» while the five routine ones kept their count line — exactly backwards. Quiet
  means only what matters, not everything except what matters. Cap is now 3.
* **The Arabic register carried `<b>` markup**, which printed as literal `&lt;b&gt;` because
  the digest HTML-escapes headlines (correctly — a vessel name may contain `<`). The deeper
  reason it was wrong: `summaries_ar` is shared with the React frontend, where a tag would
  print literally or force `dangerouslySetInnerHTML`. **Emphasis is a rendering decision**;
  the register supplies words. A test now scans the register for markup.

### 17.2 It works on real data

The baseline's first live catch, in the same render:

> **DMSP 5D-3 F17** — ground track **143 km**, against its own median of **608 km** →
> «وهو أقرب من المعتاد لهذا الجسم … ضمن أقرب عُشر ممرّاته»

while the other five passes collapsed into one counted line.
