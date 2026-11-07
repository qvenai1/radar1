# RANK-pre — assessment for renaming the four alert categories

**Assessment only. Nothing was built, migrated, or modified.** Measured **2026-08-10** against
`HEAD = a9ec061`, on a working tree with pre-existing uncommitted changes in `backend/`,
`frontend/` and `docs/` (none written by this task). Numbers come from the **live DB**
(`127.0.0.1:5435`) and the **running API** (`127.0.0.1:8000/api/v1/alerts`), both read-only.

**The owner's decision being measured** (not built here) — four names, all plain words with no
internal jargon:

| proposed name | replaces |
|---|---|
| **طارئ** | الطوارئ (7700 · 7600 · 7500) |
| **يستدعي النظر** | البحري المركَّب (تزامن أنماط) |
| **عسكري** | الطائرات والأقمار العسكرية |
| **خطأ رصد** | غير المعقول (موضع مستحيل · على اليابسة) |

⛔ **The governing constraint, non-negotiable: the axis is not flipped.** Naming changes;
`severity` does not. Military stays INFO below LOW deliberately, and the documented pledge
*«الوجود العسكري معلومة لا تهديد»* stays in force. Nothing below proposes touching
`SEVERITY_ORDER`.

> **⚠ This revision supersedes the 2026-08-09 pass.** That pass reasoned from code without
> reading the data. Three of its claims did not survive measurement; they are corrected in
> §3.5 rather than quietly dropped.

---

## 0. The constraint, verified against the code

The pledge lives in four places, and a display rename reaches none of them:

| the pledge lives in | reference | reached by a rename? |
|---|---|---|
| `SEVERITY_ORDER` — INFO sinks below LOW | `backend/alerts.py:56` | no |
| `IMPORTANCE_ORDER` — the second, orthogonal axis | `backend/alerts.py:88` | no |
| `SEVERITY_BASIS["military_aircraft"]` — *"informational, not a threat"* | `backend/alerts.py:106` | no |
| the tests that assert it | `tests/test_alerts.py:66`, `:251` | no |

⚠ But the constraint can be broken **in substance** while `severity` stays byte-identical —
see §3 and §5.

---

## 1. Layer 1 — where the labels are generated, and how many consumers

The decisive distinction the task asks for: **a value on the wire** (stored, compared, keyed
on) versus **displayed text** (read by a human). Changing the first breaks consumers; changing
the second does not.

### 1a. WIRE — ⛔ frozen contract, must not move

| site | reference | what it is |
|---|---|---|
| `HIGH/MEDIUM/LOW/INFO = "high"/"medium"/"low"/"info"` | `backend/alerts.py:51` | severity values |
| `SEVERITY_ORDER` | `backend/alerts.py:56` | **comparison** (sort key) |
| `EMERGENCY/MILITARY/MARITIME/IMPLAUSIBLE` | `backend/alerts.py:50` | family values |
| `IMPORTANCE_ORDER` keyed by family | `backend/alerts.py:88` | **comparison** (sort key) |
| `composite.HIGH/MEDIUM/LOW` + `_ORD` | `backend/composite.py:23,26` | feeds `Alert.severity` directly |
| `Alert.family` / `Alert.severity` | `backend/models.py:1152`, `:1154` | response schema |
| `AlertCounts` **field names** (4, one per family) | `backend/models.py:1201-1209` | response schema |
| `AlertFamily` / `AlertSeverity` unions | `frontend/src/api/types.ts:807-808` | contract mirror |
| `SEVERITY_ORDER` (frontend mirror) | `frontend/src/lib/alertCards.ts:30` | **comparison** |
| `SEV_TONE` keys | `frontend/src/lib/alertCards.ts:55` | value → badge tone |
| `CardTone` union + `` `sev-${it.tone}` `` | `frontend/src/components/ResultCards.tsx:19`, `:102`, `:115` | value → **CSS class** |
| `.result-card.sev-*` | `frontend/src/styles/app.css:2274-2277` | CSS class → colour + stripe |
| `a.severity === "high"` | `frontend/src/components/CountryEvents.tsx:195` | **comparison** |

Total: **13 wire sites**, of which **4 are comparisons** and **2 turn the value into a CSS
class name**. None of them carries Arabic.

### 1b. DISPLAY — ✅ safe to rename, this is the whole surface

| site | reference | text | consumers outside its own file |
|---|---|---|---|
| `RANK_AR` | `backend/summaries_ar.py:68` | مرتفعة · متوسّطة · منخفضة | injected into 2 prose constants |
| `dashHome.severity` | `frontend/src/lib/strings.ts:355-359` | مرتفع · متوسّط · منخفض · معلوماتي | **1** (`CountryEvents.tsx:181`) |
| `vessel.rvRank` | `frontend/src/lib/strings.ts:860` | عالٍ · متوسط · منخفض | **3** (`HullComposite:113`, `Rendezvous:109`, `IdentityChanges:99`) |
| `sevHintHigh` | `frontend/src/lib/strings.ts:399` | «مرتفع = تزامن نمطين أو أكثر» | 1 (fallback only) |
| `dashHome.caveat` | `frontend/src/lib/strings.ts:409` | carries **3** rank words in one paragraph | 1 |
| `sortSeverityTitle` | `frontend/src/lib/strings.ts:450` | «المعلوماتي أسفل المنخفض» | 1 |
| `SIGNAL_NOTES.emergency_squawk.ar` | `frontend/src/lib/caveats.ts:124` | «مرتفع» بحكم التعريف | via `resolveSignalNote` |
| `SIGNAL_NOTES.military_aircraft.ar` | `frontend/src/lib/caveats.ts:128` | معلوماتي، لا تهديد | via `resolveSignalNote` |

Plus the six backend prose constants inventoried in §2.

**Family-derived labels** are a separate and much smaller surface. The only rendered one is
`dashHome.signals` — a **five**-entry map keyed by `signal_type`, not by family
(`strings.ts:423-429`, rendered `CountryEvents.tsx:342`, `:356`). Three further family maps —
`dashHome.families` (`:348`), `dashHome.familyBadge` (`:362`), `familiesLine`/`domainsLine`
(`:417-418`) — measured at **0 consumers outside `strings.ts`**: they are dead residue from
`DashboardHome`, which DASH-2e replaced. ⚠ They are also *exactly the four-name map the
owner's list looks like it is addressing*, so a rename that edits only those would change
nothing on screen and still read as done.

### ⚠ Unexpected — two Arabic vocabularies already exist for the same wire value

`composite_rank` is rendered as **«مرتفع»** on the alert card (`dashHome.severity`) and as
**«عالٍ»** in the vessel panel (`vessel.rvRank`) — the *same* `high`, two different Arabic
words, on two surfaces of one app. The medium entries differ too: **«متوسّط»** (with shadda)
against **«متوسط»** (without). `rvRank` has **three** consumers to `dashHome.severity`'s one,
so the less-visible map is the more-used one. **Renaming only `dashHome.severity` creates a
third vocabulary rather than replacing the first.** Not guessed — grepped.

---

## 2. Layer 2 — rank names inside the five families' Arabic prose

**The count is not zero.** The three examples cited in the task are real, and there are more.
Every one is a fixed constant, so the inventory is complete rather than sampled:

| family | constant | line | how the rank word appears |
|---|---|---|---|
| BE-SUMMARY-1 maritime | `WHY_COMPOSITE` | `summaries_ar.py:201` | «الرتبة **{rank}** لتزامن {n}» — slot, filled from `RANK_AR` |
| BE-SUMMARY-1 maritime | `WHY_SINGLE` | `:202` | «الرتبة **{rank}** لنمط واحد مرصود» — slot |
| BE-SUMMARY-2 implausible | `IMPL_WHY` | `:300-303` | «الرتبة **منخفضة**: …» — **literal** |
| BE-SUMMARY-3 emergency | `EMG_WHY` | `:402` | «الرتبة **مرتفعة** بحكم التعريف: …» — **literal** |
| BE-SUMMARY-4 military air | `MIL_AIR_WHY` | `:484-487` | «**معلوماتي**: … الرتبة ليست **منخفضة** …» — **literal, two words** |
| BE-SUMMARY-5 satellite | `SAT_WHY` | `:553-556` | «**معلوماتي**: … الرتبة ليست **منخفضة** …» — **literal, two words** |

**6 prose constants across all 5 families, plus the `RANK_AR` map.** Four of the six embed the
rank word as a **literal**, so they cannot be re-pointed by editing `RANK_AR` alone — each
sentence has to be rewritten by hand.

Frontend adds **7 more** live strings carrying a rank word (§1b table). Total prose surface:
**13 strings**, none of which a rename reaches automatically.

### ⛔ Two decoys a find-and-replace would corrupt

| site | text | why it is not a rank |
|---|---|---|
| `summaries_ar.py:577` | «والهامش يتّسع للأجسام **المنخفضة** سريعة الاضطراب» | low **altitude** — SGP4 accuracy decay |
| `summaries_ar.py:452` | «never «**منخفضاً**» / «على علوّ شاهق»» | a comment forbidding an altitude adjective |

A global `sed` on «منخفض» rewrites an orbital-mechanics sentence into nonsense. The rewrite
must be per-constant.

⚠ Also measured, unrelated to the rename but touching the same file: the module header
(`summaries_ar.py:28-32`) states `gnss_anomaly` and `zone_dwell` have **no count column**.
`maritime_hull_composite` now carries **`n_gnss_anomaly` and `n_zone_dwell`**. The comment is
stale; the code that depends on it (`_hull_patterns`, reading the timeline) is still correct.

---

## 3. Layer 3 — semantic compatibility. ⛔ The most important finding, and it is measured

### 3.1 The live distribution

`GET /api/v1/alerts`, no country filter, 2026-08-10:

```
counts: emergency 0 · military 21 · maritime 200 · implausible 0 · total 221
```

| family | high | medium | low | info | total |
|---|---:|---:|---:|---:|---:|
| emergency | 0 | 0 | 0 | 0 | **0** |
| military | 0 | 0 | 0 | 21 | **21** |
| maritime | **200** | 0 | 0 | 0 | **200** |
| implausible | 0 | 0 | 0 | 0 | **0** |
| **total** | **200** | **0** | **0** | **21** | **221** |

### 3.2 ⚠ Three results that contradict the task's stated premise

**(a) «6 من 106» does not reproduce.** Measured: **200 of 200** maritime alerts sit at HIGH,
of which **19** arrive by the ≥3-pattern path. Not 6, and not out of 106.

**(b) Every HIGH alert in the feed today is maritime. Zero are emergencies.** If «طارئ» names
the HIGH tier, it would today label **200 vessel composites** "emergency" and cover **no actual
emergency at all**. The name would be wrong on 100 % of the rows it appears on.

**(c) Maritime is never LOW or MEDIUM in the feed.** Uncapped there are 319 hulls at
`n_pattern_types ≥ 2` → **244 high / 75 medium / 0 low**. `ALERT_PER_FAMILY_CAP = 200`
(`api.py:2097`) is applied *after* a high→low sort (`api.py:2063-2065`), so the cap truncates
**every** medium hull. `composite_rank` cannot return LOW at `n_pattern_types ≥ 2` at all
(`composite.py:60-68`), and the feed admits nothing below 2 (`api.py:2259`).

### 3.3 ⛔ HIGH has three doors, and the proposed fix only closes one

`composite_rank` (`composite.py:60-68`) reaches HIGH three ways inside the feed. Measured over
the 200 live maritime alerts:

| path to HIGH | count | share |
|---|---:|---:|
| 2 pattern types **+ grey/sanctions flag** | **154** | 77 % |
| 2 pattern types **+ a HIGH underlying event** | 27 | 13.5 % |
| **≥3 pattern types** | 19 | 9.5 % |

Cross-tab, capped feed:

| `n_pattern_types` | high | medium | low |
|---:|---:|---:|---:|
| 2 | 181 | 0 | 0 |
| 3 | 19 | 0 | 0 |

⛔ **The task's proposed separation — "the triple composite drops to «يستدعي النظر»" — moves 19
of 200 alerts and leaves 181 where they were.** The dominant driver of HIGH is not the pattern
count at all; it is the **grey/sanctions flag** (176 of the 181 two-pattern hulls carry one).
Any separation built on pattern count addresses under a tenth of the population.

### 3.4 Name by name, against the measured population

| name | does it hold over everything under it today? |
|---|---|
| **طارئ** | ⛔ **no.** 200/200 rows at HIGH are maritime composites; 0 are emergencies. True only if the name attaches to **family**, not to severity. |
| **يستدعي النظر** | ⛔ **collides.** Maritime is 100 % HIGH, so this name and «طارئ» would sit on the *same* severity tier — two names for one rank. It also collides with two live phrases: «لماذا يستدعي النظر؟» (`strings.ts:378`, the disclosure on **every** card of **every** family) and «يستدعي الانتباه» (`strings.ts:412`, the **HIGH-severity** tile). Three near-identical «يستدعي…» phrases on one dashboard, on three different axes. |
| **عسكري** | ✅ **holds.** 21/21 INFO rows are military, and military is the only INFO producer. It is also already the rendered label (`strings.ts:425-426`) — zero change, zero risk. ⚠ It must keep **both** signal-type qualifiers («— طائرات» / «— مرور أقمار»): the two carry different constant bases (`CountryEvents.tsx:150-151`), so merging them would hoist a note that is not shared. |
| **خطأ رصد** | ⛔ **do not take.** Unmeasurable live (0 in the window) but non-empty in data: **557 hulls**, **9,609 flagged rows** (on_land 6,230 · impossible_speed 4,678 · teleport_return 1,518). The objection is textual and stands: `IMPL_CAVEAT` (`summaries_ar.py:312-317`) names **four** causes and refuses to choose — and **two of them are not sensing errors**. With a spoofed position or GNSS jamming the receiver recorded correctly what was broadcast; the *broadcast* lied. «خطأ رصد» closes the question the caveat below it keeps open, and the header is read first. It also silently re-bases LOW from *"the vessel is usually the affected party"* (`alerts.py:108`) to *"the data is junk"* — same number, different reason, and the reason **is** the honesty register. |

### 3.5 ⚠ Corrections to the 2026-08-09 pass

| that pass claimed | measurement |
|---|---|
| "a card under «يستدعي النظر» can carry a «منخفض» badge" (§3.3b) | **impossible in the feed.** `composite_rank` cannot return LOW at `n_pattern_types ≥ 2`; measured 0 low across 319 hulls. |
| the maritime family has "variable severity", unlike the other three | **not in the feed.** It is 200/200 HIGH. The variability exists only on `/maritime/composite` at `min_pattern_types=1`. |
| `IMPL_WHY` reads «… والسفينة في الغالب طرف متضرّر لا فاعل» | the working tree now reads «الرتبة منخفضة: هذه رصدات يُرجَّح أنها **عيب في البيانات** لا سلوك من السفينة، والسفينة في الغالب طرف متضرّر لا فاعل» (`summaries_ar.py:300-303`). The prose has already moved toward "data defect" — which makes «خطأ رصد» *less* contradictory than that pass argued, though §3.4's objection stands on the caveat, not on this line. |

### 3.6 ⚠ A pre-existing defect the rename would inherit — found by measuring, not reading

**172 of 200** live maritime `why` lines read:

> «الرتبة مرتفعة **لتزامن نمطين**؛ وعلمها مدرَج على قوائم رقابية»

But `composite_rank(2, grey=False, ord<3)` returns **MEDIUM**. Two patterns alone are *not*
sufficient for HIGH — the grey flag is what lifts it. The sentence attributes the rank to the
insufficient condition with «ل» (*due to*), then mentions the actual cause as an afterthought
clause. The English `severity_basis` (`alerts.py:457-462`) lists both **without** asserting
causation; the Arabic template adds a causal claim the English does not make. That violates
this file's own rule 2 — *"why … restates; it does not judge"* (`summaries_ar.py:15-16`).

⚠ And **1 of 200** reads «الرتبة مرتفعة **لنمط واحد مرصود**» on a hull the feed admitted only
because `n_pattern_types ≥ 2`. The `why` line counts distinct **timeline** event types
(`_hull_patterns`, `alerts.py:495-501`) while `severity` counts the **view's**
`n_pattern_types`. `alerts.py:472-473` records these as agreeing in 400/400 hulls; measured
today, they disagree on at least one live alert.

**Neither defect is caused by the rename** — but both live in the exact constants a rewrite
must touch, so they should be fixed *before* the prose is reworded, not during.

---

## 4. Layer 4 — colours and the second channel

**Every binding is keyed on `severity`. None is keyed on family.**

| binding | reference |
|---|---|
| `--sev-high/medium/low/info` (dark) | `tokens.css:414-417` |
| the same four (light) | `tokens.css:543-546` |
| `.result-card.sev-*` → colour **and** stripe | `app.css:2274-2277` |
| `SEV_TONE` → `.rc-badge` tone | `alertCards.ts:55`, `app.css:2160-2164` |
| family count tiles (2px hairline) | `app.css:2181-2183` |

The second channel (`app.css:2253-2277`) distinguishes the four **without colour**:

```
high    5px solid     thick, continuous
medium  5px dashed    thick, broken
info    5px dotted    thick, fine dots
low     2px solid     visibly the slightest
```

✅ **Answer: no colour↔rank binding changes.** A rename edits Arabic strings; the class name
is built from the wire value (`` `sev-${it.tone}` ``), which does not move. The ΔE gate
(worst pair 26.47) **stays closed** and nothing needs re-validating.

⛔ It reopens only if one of these is done — none of which the owner's list requires:
a **fifth** tier is added; a **family** is given its own colour (today families have no hue);
or the value→colour assignment is re-pointed. If any of those becomes the plan, stop and
re-run the ΔE gate before building.

---

## 5. Layer 5 — the importance axis, and whether ⓘ stays truthful

`importance` is per **family** (`alerts.py:88`): emergency 0, military 1, maritime 2,
implausible 3. Measured today:

| importance | severity | count |
|---:|---|---:|
| 1 (military) | info | 21 |
| 2 (maritime) | high | 200 |

⚠ **This is where the naming breaks hardest.** Under «رتّب بالأهمية» (`strings.ts:445`) the
21 military INFO cards sort **above** the 200 maritime HIGH cards — correct and deliberate
(`alerts.py:82-84`). But with the tiers named, the screen would read *«عسكري» above «طارئ»* —
a priority claim the project explicitly disclaims two strings away
(`capNoteImportance` `:441`, `sortImportanceTitle` `:447`). The listed order
**طارئ → يستدعي النظر → عسكري → خطأ رصد** implies a fourth ladder that *neither* axis holds:
`IMPORTANCE_ORDER` puts military (1) **above** maritime (2), the exact opposite.

### ⓘ — measured against real rows, not assumed

`toneHint = a.why || fallback` (`CountryEvents.tsx:195`). The live `why` strings:

| signal type | what ⓘ shows today |
|---|---|
| `maritime_composite` | «الرتبة **مرتفعة** لتزامن نمطين؛ وعلمها مدرَج على قوائم رقابية» |
| `military_aircraft` | «**معلوماتي**: الوجود مُسجَّل، لا حكم تهديد. الرتبة ليست **منخفضة** لضعف الخطر — هذه الفئة خارج محور الخطر أصلاً.» |
| `military_satellite` | «**معلوماتي**: الممرّ محسوب، لا حكم تهديد. الرتبة ليست **منخفضة** لضعف الخطر — …» |

⛔ **After a rename with no rewrite, 221 of 221 live alerts show a badge whose ⓘ names a
different word.** The badge would say «طارئ» while ⓘ explains why «الرتبة مرتفعة» — 200 rows.
The badge would say «عسكري» while ⓘ opens «معلوماتي:» and argues about «منخفضة», a word no
longer anywhere on screen — 21 rows. The ⓘ is the one place the register defends the honest
rank; a mismatch there is the reader losing the defence, not losing a label.

---

## 6. Layer 6 — the frontend (read-only; not this window's to touch)

**Read sites for the labels:**

| label | consumers | where |
|---|---:|---|
| `dashHome.severity` | 1 | `CountryEvents.tsx:181` |
| `dashHome.signals` | 2 | `CountryEvents.tsx:342`, `:356` |
| `vessel.rvRank` | 3 | `HullComposite.tsx:113`, `Rendezvous.tsx:109`, `IdentityChanges.tsx:99` |
| `dashHome.families` / `familyBadge` / `familiesLine` / `domainsLine` | **0** | dead |

**Logic that compares TEXT rather than a value** — the silent-break question:

| # | site | compares | failure mode |
|---|---|---|---|
| 1 | `alertCards.ts:160-168` `FLAG_MARK = " · علم التسجيل: "` | **Arabic prose** from `summaries_ar.py:195` | ⛔ **the one in a rewrite's path.** Reword the flag line and the split silently stops: the headline renders whole and the separate flag line simply never appears. Fails safe, fails **silent**. |
| 2 | `alertCards.ts:112` `MARITIME_LEAD_CLAUSE` | backend **English** basis | hoist vanishes; per-card notes repeat. Documented as deliberate degrade. |
| 3 | `caveats.ts:168` `resolve()` | live English basis vs hardcoded baseline | shows English instead of Arabic **and** `console.warn`s. Loud. But its Arabic paraphrases (`:124`, `:128`) carry rank words and go stale in the same commit. |

✅ **No logic compares the Arabic rank words themselves.** So a *label* rename alone trips
none of these. But §2 proves a prose rewrite is mandatory, and a prose rewrite trips **(1)**.
`splitHeadline` must be re-verified against real headlines in the same commit that touches
`FLAG_LINE`.

---

## 7. What breaks if the names are changed without rewriting the prose

| # | breakage | scale, measured |
|---|---|---|
| 1 | badge word ≠ ⓘ word on every card | **221 / 221** live alerts |
| 2 | `dashHome.caveat` still explains the old four words | 1 paragraph, 3 rank words, on every dashboard load |
| 3 | `sortSeverityTitle` «المعلوماتي أسفل المنخفض» names two vanished words | 1 tooltip on the sort control |
| 4 | `sevHintHigh` fallback still says «مرتفع = تزامن نمطين» | fires only where `why` is absent |
| 5 | `caveats.ts` Arabic notes say «مرتفع» / «معلوماتي» | 2 group headers |
| 6 | `vessel.rvRank` keeps a **third** vocabulary («عالٍ») | 3 components |
| 7 | four backend prose literals keep the old words | `IMPL_WHY`, `EMG_WHY`, `MIL_AIR_WHY`, `SAT_WHY` |
| 8 | the listed order reads as a ladder contradicting `IMPORTANCE_ORDER` | wherever the four appear together |

**Not broken:** the wire (§1a), the tests, the colours and the second channel (§4).
✅ `tests/test_alerts.py` asserts on wire values (`:40`, `:64`, `:78`, `:119`) and English
`severity_basis` substrings (`:66`, `:104`, `:109`) — a display rename touches none of them.
**If a proposed rename ever required a test edit, that is the signal it had reached the axis.**

---

## 8. Proposed staging — one concern per stage, in dependency order

**S0 — ⛔ BLOCKING OWNER DECISION: which axis does the name attach to?**
Everything below branches on this and nothing can be built before it. The measured evidence
points one way: **attach the names to `family`, not to `severity`** — because 200/200 HIGH
alerts are maritime and 0 are emergencies (§3.2b), so «طارئ» as a severity-tier name is wrong
on every row it would appear on. As family names, all four describe what a thing *is*, the
severity badge keeps its own four words, and §3's whole contradiction disappears.

*If S0 chooses family* → skip S1; go S2 → S3 → S4 → S5 → S6.
*If S0 chooses severity* → S1 becomes mandatory and is the largest item here.

**S1 — semantic separation (only if S0 chose severity).** Derive the displayed rank from the
family, or reclassify. ⚠ Note that the reclassification proposed in the task covers **19 of
200** rows (§3.3); a separation that actually works has to address the **grey-flag** path
(154 rows) as well. This is a backend behaviour change, not a rename, and needs its own pre-assessment.

**S2 — fix the `why` misattribution (independent of the rename, do it first).**
`WHY_COMPOSITE` asserts the rank follows from the pattern count in 172/200 rows where the grey
flag is the actual cause (§3.6), and the timeline/`n_pattern_types` divergence on 1 row. Land
this **before** any rewording, because S5 edits the same constants and would otherwise bake the
wrong causal claim into new wording.

**S3 — unify the two rank vocabularies.** One map for `high|medium|low`, consumed by both the
alert card and the three vessel components. Today «مرتفع»/«عالٍ» and «متوسّط»/«متوسط» are two
vocabularies for one wire value (§1b). Doing this before S4 means the rename edits **one** map
instead of creating a third.

**S4 — the rename itself.** Display strings only: `dashHome.signals` (5 entries, the sole
rendered family surface) and/or the unified rank map from S3. Plus a decision on the three dead
maps — update or delete in the same commit; leaving them stale is the worst option.

**S5 — the prose rewrite.** 6 backend constants (§2) + 7 frontend strings (§1b), each by hand,
per-constant, never by find-and-replace (§2's two decoys). ⛔ Re-verify `splitHeadline` against
real headlines in the same commit (§6, row 1).

**S6 — docs.** `PROJECT_STATE.md` §3 register rows, one per renamed item.

**Order: S0 → S2 → S3 → [S1] → S4 → S5 → S6.**

---

## 9. Verdict

| proposed | verdict | if not taken |
|---|---|---|
| **عسكري** | ✅ **take as-is** — already the rendered label; holds over 21/21 INFO rows | — |
| **طارئ** | ✅ as a **family** name · ⛔ as a **severity-tier** name (wrong on 200/200 rows today) | resolve S0 first |
| **يستدعي النظر** | ⚠ collides with two live phrases **and** with «طارئ» on the same tier | **«تزامن أنماط»** — the owner's own parenthetical: plain, no priority claim |
| **خطأ رصد** | ⛔ **do not take** — names a cause the project documented as open; re-bases LOW | **«موضع مستحيل»** — already this project's Arabic (`summaries_ar.py:287`, `:292`) |

⚠ Minor, if the label enumerates: the implausible family covers **three** flags —
`on_land`, `impossible_speed`, `teleport_return` (`alerts.py:632-636`). «موضع مستحيل · على
اليابسة» names two, and «قفزة وعودة» is not a position at all. Enumerate all three or none.

**Nothing was built.** Blocked on S0.

---

## 10. Queued — recorded here, not addressed by this task

| item | note carried forward |
|---|---|
| terminology simplification | — |
| filler reduction | ⛔ the caveat is **shortened, never deleted** |
| larger type | ⚠ cascade already measured: panel length against 3,042 · `PANEL_CAP = 10` · column > 720px |
| flags | — |
| attribution & logo | — |

---

## 11. Owner decision — the adopted vocabulary (recorded 2026-08-10)

⛔ **Still assessment: nothing below is built.** This section records what was decided, where
each item lands, and what remains open. It supersedes §9's recommendations wherever the two
differ.

### 11.1 The severity axis — ✅ three words kept, one changed

| value | label | decision |
|---|---|---|
| `high` | **مرتفع** | ✅ unchanged |
| `medium` | **متوسّط** | ✅ unchanged |
| `low` | **منخفض** | ✅ unchanged |
| `info` | **لا تهديد** | ⚠ replaces «معلوماتي» |

⛔ **Rejected, with the reason each was rejected** — recorded so the decision is not re-opened
by someone who only sees the words:

| proposed | why it was not taken |
|---|---|
| **حَرِج** for `high` | `EMG_CAVEAT_BASE` (`summaries_ar.py:410`) states the squawk does **not** distinguish «العطل الفنّي البسيط» from «**الحالة الحرجة**» — the badge would contradict its own caveat using the same word. And measured: 200/200 rows at `high` are maritime leads carrying «دليل أقوى — لا إثبات». |
| **مُهِم** for `medium` | collides with the `importance` axis, which is named «الأهمية» on screen (`strings.ts:218`, `:443`, `:445`). Measured: 21 `info` cards sort **above** 200 `high` cards under «رتّب بالأهمية». |
| **روتيني** for `low` | `low` is the position-anomaly family, whose recorded basis is «السفينة في الغالب **طرف متضرّر لا فاعل**» (`alerts.py:108`). «روتيني» asserts normality over what may be a record of deliberate GNSS interference. |

✅ **Rationale for keeping the three degrees:** action language belongs on the triage tiles,
which already carry it — «يستدعي الانتباه» / «قيد المراجعة» (`strings.ts:411-412`). The badge is
the per-row danger axis, and `importance` exists precisely so that display priority is never
asserted from it. Degrees on the badge, action on the tile, `why` in ⓘ.

### 11.2 The family axis — the five rendered headers

`dashHome.signals` (`strings.ts:423-429`) — the sole rendered family surface:

| signal type | today | adopted |
|---|---|---|
| `emergency_squawk` | طوارئ — رمز إرسال | **إشارة طوارئ** |
| `military_aircraft` | عسكري — طائرات | **وجود جوي (عسكري)** |
| `military_satellite` | عسكري — مرور أقمار | **مرور مداري (عسكري)** |
| `maritime_composite` | بحري — صورة مركّبة | **عدّة ملاحظات** |
| `implausible_position` | مواقع غير معقولة | **شذوذ في الرصد** |

Why each was chosen:

- **إشارة طوارئ** — the system captures the *signal*, not a confirmed emergency. Matches the
  deliberately passive headline «**بُثّ** رمز الاستيلاء» (`summaries_ar.py:367`) and the caveat
  «الرمز مُبلَّغ من الطائرة نفسها» (`:409`).
- **وجود جوي (عسكري)** — ⛔ «نشاط» was rejected: BE-SUMMARY-4's refused-verb list
  (`summaries_ar.py:437-446`) exists because the source holds «موضعاً ومُعرّفاً **فقط**»
  (`:509`). «وجود» is the committed safe form («سُجّل **وجود** طائرة عسكرية»).
- **مرور مداري (عسكري)** — «مرور» is already the project's word (`SAT_HEAD`), and a tenseless
  geometric term is correct for a pass that **has not happened yet** (`forwardPass`).
- **عدّة ملاحظات** — ⛔ «مُركّب» was rejected as the most internal term in the system
  (`composite.py`, `CompositeHull`, `maritime_hull_composite`), against the standing rule
  «كلمات مفهومة بلا مصطلح داخلي». ⚠ It is also the *minority* mechanism: measured, 77 % of
  these rows are `high` because of the **grey/sanctions flag**, not pattern composition (§3.3).
- **شذوذ في الرصد** — ✅ resolves §3.4's ⛔ on «خطأ رصد»: it describes the observation's
  deviation without naming a cause, keeping `IMPL_CAVEAT`'s four causes open. Already this
  project's Arabic in 6 places.

### 11.3 ⚠ The «شذوذ» collision — decided, but NOT fully resolved

**Decision:** the internal maritime pattern `gnss_anomaly` becomes **«شذوذ في الموضع»**
(`PATTERN_AR`, `summaries_ar.py:64`, today «شذوذ موضع GNSS»).

⚠ **Recorded reservation, for the implementation step to settle.** As assigned, both names are
«شذوذ في …» separated only by موضع/رصد — and dropping the `GNSS` qualifier removes the one token
that distinguished them today. The nouns are also the wrong way round against the code's own
semantics:

| what it actually is | code |
|---|---|
| the implausible **family** — the recorded **position** is physically impossible (on land · impossible speed · teleport) | `IMPL_HEAD_*`, `alerts.py:632-636` |
| the maritime **pattern** — the **signal** was unreliable at that moment | «شذوذ الموضع يعني أن **الإشارة** نفسها غير موثوقة» (`summaries_ar.py:228`) |

✅ **Minimal fix, if the owner wants it:** keep the family as **«شذوذ في الرصد»** and name the
pattern for its source — **«شذوذ إشارة GNSS»**. Different second noun, matches
`CAVEAT_GNSS`, and no token is lost. ⛔ Not applied; awaiting the owner's word at implementation.

### 11.4 Where the adopted changes land — ⚠ split across two ownership boundaries

⛔ **Most of this is `frontend/`, which this window does not own.** Recorded so the
implementation is scoped correctly rather than discovered mid-edit.

| # | change | file | owner |
|---|---|---|---|
| 1 | `info` → «لا تهديد» | `frontend/src/lib/strings.ts:359` | ⛔ frontend |
| 2 | the five family headers | `frontend/src/lib/strings.ts:423-429` | ⛔ frontend |
| 3 | «معلوماتي» in `MIL_AIR_WHY` | `backend/summaries_ar.py:485` | ✅ **this window** |
| 4 | «معلوماتي» in `SAT_WHY` | `backend/summaries_ar.py:554` | ✅ **this window** |
| 5 | `PATTERN_AR["gnss_anomaly"]` | `backend/summaries_ar.py:64` | ✅ **this window** |
| 6 | «شذوذ GNSS» (2 sites) | `frontend/src/lib/strings.ts:132`, `:489` | ⛔ frontend |
| 7 | `dashHome.caveat` — names «معلوماتي» | `frontend/src/lib/strings.ts:409` | ⛔ frontend |
| 8 | `sortSeverityTitle` — «المعلوماتي أسفل المنخفض» | `frontend/src/lib/strings.ts:450` | ⛔ frontend |
| 9 | ⚠ `caveats.ts:128` becomes «… **لا تهديد، لا تهديد**» — a duplication the new label creates | `frontend/src/lib/caveats.ts:128` | ⛔ frontend |
| 10 | `rvRank` unification («عالٍ») | `frontend/src/lib/strings.ts:860` | ⛔ frontend |
| 11 | the four-line legend | `frontend/src/components/`, `strings.ts` | ⛔ frontend |
| 12 | three dead maps — update or delete | `frontend/src/lib/strings.ts:348`, `:362`, `:417` | ⛔ frontend |

✅ **Narrower than feared, because the three degree words were kept:** the only backend prose
edits are rows 3–5. `IMPL_WHY` («الرتبة منخفضة») and `EMG_WHY` («الرتبة مرتفعة») and `RANK_AR`
need **no** change under this decision.

### 11.5 What is still unblocked-but-unresolved, and must precede the rename

⛔ **S2 stays first** (§8). `WHY_COMPOSITE` attributes the rank to the pattern count in **172 of
200** live rows where the **grey flag** is the actual cause, and 1 of 200 says «لنمط واحد
مرصود» on a hull the feed admitted at `n_pattern_types ≥ 2` (§3.6). Landing the rename first
would bake the wrong causal claim into new wording.

⚠ **§6 row 1 still applies to any prose edit.** `FLAG_MARK = " · علم التسجيل: "`
(`alertCards.ts:160`) matches Arabic prose from `FLAG_LINE` (`summaries_ar.py:195`). None of the
adopted changes touch `FLAG_LINE`, so it is safe **today** — re-verify if that constant moves.

✅ **Unaffected and requiring no re-validation:** the wire (§1a), `SEVERITY_ORDER`,
`IMPORTANCE_ORDER`, the tests, the `--sev-*` colours and the second channel — the ΔE gate
(worst pair 26.47) **stays closed** (§4). The four severity values remain four, and every
colour binding is keyed on the wire value, not on the label.

---

## 12. As built (2026-08-10) — what shipped, and two corrections to §11

Three commits, in dependency order: `0c4a826` (this assessment), `78edd87` (S2 — the causal
fix), `7d8f255` (the adopted vocabulary). ⛔ S2 landed **before** the rename, so no new
wording was written over the wrong causal claim.

### 12.1 S2 — the `why` line now names a sufficient cause

`composite.rank_with_reason` returns `(rank, reason)` and `composite_rank` is a thin wrapper,
so the rule stays written **once**. The reason travels on `CompositeHull.rank_reason`; the
Arabic layer only looks it up, so rule 5's no-second-derivation holds.

| gate | before | after |
|---|---:|---:|
| HIGH rows crediting 2 patterns alone | 172 | **0** |
| rows carrying the causal «ل» | 200 | **0** |
| «single event» why on an `n ≥ 2` hull | 1 | **0** |
| listed flag stated twice in one sentence | — | **0** |
| `why` rank word ≠ its own severity badge | — | **0** |

⛔ **`severity` did not move** — verified over all 70 input combinations against a
reimplementation of the original rule: zero divergence. Live distribution unchanged at
200/200 HIGH. 21 new gates; suite **775 passed** (the 3 remaining failures are pre-existing
data drift, proven below).

### 12.2 ⚠ Correction to §3.3 — the three paths are not mutually exclusive

The reason codes split what §3.3's table reported as one row. Measured over the same 200:

| reason | count |
|---|---:|
| two patterns + grey flag | 154 |
| two patterns + grey flag **AND** a high underlying event | **22** |
| three patterns | 19 |
| two patterns + a high underlying event only | **5** |

§3.3's "2 types + a HIGH underlying event = 27" is true as a count of hulls *having* one, but
**22 of those 27 also carry a grey flag** — either condition alone would have produced HIGH.
The old code short-circuited and reported only the first matched; both are now named.

### 12.3 ⚠ Correction to §11.4 row 6 — the «شذوذ GNSS» surface was mis-scoped

That row named `strings.ts:132` and `:489`. Measured while implementing: `:132` is the **map
layer toggle**, and `T.gnss.*` is a **standalone overlay feature** with its own endpoint —
neither is a pattern label. Both were deliberately left as «شذوذ GNSS»; renaming them would
have renamed a map layer.

The real mirrors of `PATTERN_AR["gnss_anomaly"]` are the vessel panel's `pat.gnss` and the
caveat that enumerates the same patterns. Those carry «شذوذ في الموضع».

⚠ **The §11.3 reservation therefore still stands, and is wider than recorded**: «شذوذ» now
labels three distinct things — the implausible family («شذوذ في الرصد»), the maritime pattern
(«شذوذ في الموضع») and the map overlay («شذوذ GNSS»). The minimal fix remains
**«شذوذ إشارة GNSS»** for the pattern (different second noun, keeps the distinguishing token,
matches `CAVEAT_GNSS`, which is about the **signal** while the family is about the
**position**). One string, not applied — awaiting the owner.

### 12.4 One item S2 forced that §11.4 did not list

`sevHintHigh` claimed «مرتفع = تزامن نمطين أو أكثر». DASH-CARD-2 had already recorded that it
lies to four of five families; S2 measured it false for the fifth as well, the one it was
written for. **A string false for every family has no honest fallback role**, so it was
deleted and `CountryEvents.tsx` now falls back to `sevHint`, which claims nothing about the
cause.

### 12.5 Status of §11.4's twelve rows

✅ **Eleven done** (rows 1–10 and 12, with row 6 re-scoped per §12.3). The dead maps
`families` / `familyBadge` / `familiesLine` / `domainsLine` were **deleted**, not renamed —
all measured at zero consumers, and stale vocabularies contradicting the live one is the worst
outcome of the three.

⛔ **Row 11 — the four-line legend — is NOT built.** It is new UI rather than a text edit, and
it is the item §11.1's rationale leans on hardest: from the four rank words, **only two ever
render today** (مرتفع ×200 · لا تهديد ×21; متوسّط and منخفض are 0), and the tiles cover only
two tiers, so a reader has no way to learn the scale from the screen. It needs its own
go-ahead and its own fold measurement.

### 12.6 ⚠ The three pre-existing test failures are not from this work

Proven from the data, not asserted: `maritime_hull_composite` for the reference hull holds
`n_gaps=4, n_rendezvous=0, n_pattern_types=3`, while the tests expect `(2,2,1,6)` and `4`.
Vessel search also computes its own `n_pattern_types` at `api.py:555`, outside this work's
path entirely, and the airport failure is an elevation-band case. Same 3 before and after.

### 12.7 ⚠ Carried forward

- The running API on `:8000` serves the **old** code until restarted.
- `backend/api.py` and `backend/models.py` carry unrelated uncommitted work
  (`SeriesCoverage`, `maritime_series_multi_pattern`). Only the S2 hunks were staged from
  them, via `git apply --cached`; the working tree was never touched and that work is intact.
- §10's queue is unchanged and untouched. ⛔ The caveat was **not** shortened here: every fact
  it carried is still in it (`dashHome.caveat`), and trimming it stays a separate item.

---

## 13. The legend, and the «شذوذ» resolution (2026-08-10)

Commit `0dd3d9a`. This closes §12.5's outstanding row 11 and §11.3's reservation.

### 13.1 «شذوذ إشارة الملاحة» — §11.3 resolved

The owner offered «شذوذ إشارة GPS» and «شذوذ إشارة الملاحة». **«الملاحة» was taken.**

⛔ **GPS was rejected on the same ground that kept «خطأ رصد» out of the family label:** it names
**one** constellation, `gnss_anomaly.py` does not distinguish GPS from GLONASS / Galileo /
BeiDou, and the AIS record carries no constellation field at all — so the label would claim a
specificity the data does not hold.

✅ «الملاحة» is constellation-agnostic, carries no abbreviation, and matches what the pattern
actually is: `CAVEAT_GNSS` says «شذوذ الموضع يعني أن **الإشارة** نفسها غير موثوقة» — the
**signal** — while the implausible family («شذوذ في الرصد») is about the recorded **position**.
The two terms no longer differ by a bare noun, and no distinguishing token is dropped.

⚠ **Still open, deliberately:** the map-layer toggle and the standalone overlay (`T.gnss.*`)
remain **«شذوذ GNSS»** — a different feature with its own endpoint (§12.3). But the decision's
own rationale was *no foreign abbreviations, language everyone understands*, and that rationale
applies to a visible map toggle too. **One decision, not applied:** align it or leave it.

### 13.2 The legend — what it is, and why it is the item that mattered

⛔ **The measured reason it exists:** of the four rank words, only **two ever render** —
«مرتفع» ×200 and «لا تهديد» ×21; «متوسّط» and «منخفض» are both **0**, and the tiles cover two
tiers. A reader is shown the top of a scale and its odd one out, with no way to learn the scale
from the screen. **No rename could fix that** — which is why §11.1's rationale leaned on it.

Four rows, ordered from `SEVERITY_ORDER` rather than written out, so «لا تهديد» is last here
for the same reason it is last on the cards. It is separated by a rule, because it is a
**category** and not the fourth rung of a three-rung ladder.

✅ **The swatches share the card's own stripe declarations** — `.result-card.sev-*` and
`.sev-legend .sl-row.sev-*` are one rule. A legend that drifts from what it explains teaches a
wrong mapping with total confidence, and sharing the rule is the only way to guarantee it
cannot. It therefore also teaches the **second, non-colour channel** (5px solid / dashed /
dotted · 2px), which is what survives with every hue stripped out.

### 13.3 ⚠ The length budget, measured across all seven countries

Collapsed by default, because the panel has no room to spare. At 1440×900:

| country | absent | closed (+37) | open (+190) | headroom when open |
|---|---:|---:|---:|---:|
| سوريا | 2797 | 2834 | 2987 | 55 |
| تركيا | 2775 | 2812 | 2965 | 77 |
| العراق | 1323 | 1360 | 1513 | 1529 |
| الأردن | 1116 | 1153 | 1306 | 1736 |
| لبنان | 2742 | 2779 | 2932 | 110 |
| إسرائيل | 2797 | 2834 | 2987 | 55 |
| **قبرص** | 2837 | **2874** | **3028** | **14** |

✅ Every country stays under DASH-2d″'s ~3,042px gate **even fully open**.
⚠ **But قبرص has 14px of headroom open.** A fifth row, a longer gloss, or the queued
type-size increase would push it over — that is the budget any future edit to this surface has,
and it is the number to check first.

⚠ **A gate-methodology note worth keeping:** `verify-layout-system.mjs` renders
`http://127.0.0.1:8000/`, i.e. the **served bundle** in `frontend/dist/`. Running it without
rebuilding measures stale code and passes green while proving nothing. All four gate suites
here were re-run against a fresh build (layout 11/11 · layout-system 10/10 · contracts 20/20 ·
dataviz 9/9).

### 13.4 Status

✅ §11.4's twelve rows are now **all** done. §10's queue is still untouched, and ⛔ the caveat
is still not shortened. The API on `:8000` now serves the rebuilt bundle; the Python process
still needs a restart for the backend prose.

---

## 14. «GNSS» leaves the displayed text — §13.1's open item, closed

Commit `58b842b`. The owner's ruling: the abbreviation is jargon to an operator, so it goes
from **every** user-facing string. ✅ **No constraint blocked it** — the three that could have
were checked rather than assumed.

### 14.1 The surface was wider than §12.3 recorded — eight sites, not two

| # | site | was |
|---|---|---|
| 1 | `map.layers.gnss` — the layer label | شذوذ GNSS |
| 2 | `gnss.toggle` | شذوذ GNSS |
| 3 | `gnss.ariaOn` | إخفاء تراكب شذوذ GNSS |
| 4 | `gnss.ariaOff` | إظهار تراكب شذوذ GNSS |
| 5 | `gnss.source` — provenance | شذوذ GNSS: … |
| 6 | `…suspectNote` | (تزوير **GNSS** أو خطأ) |
| 7 | ⚠ `summaries_ar.GNSS_HEAD` | ورد في سجلّها شذوذ في **موضع GNSS** |
| 8 | ⚠ `summaries_ar.IMPL_CAVEAT` | أو تشويش على إشارة **GNSS** |

Rows 7–8 are **backend prose that every earlier pass missed**, including §11.4 and §12.3. The
lesson generalises: the label inventory in §1b was built from the *label maps*, and prose
constants that merely *mention* a term are a separate surface.

### 14.2 ⚠ Row 7 was an inconsistency, not just an abbreviation

`GNSS_HEAD` read «شذوذ في **موضع** GNSS» while `PATTERN_AR` for the **same pattern** already
read «شذوذ **إشارة** الملاحة» — one thing described with two different nouns. And «موضع» was
the wrong one: the comment directly above it and `CAVEAT_GNSS` directly below it both state the
anomaly is a property of the **SIGNAL**. The **position** noun belongs to the implausible family
(«شذوذ في الرصد»). Both now say `إشارة الملاحة`, gated.

### 14.3 ⛔ Row 8 touched the honesty register — one word, and it was verified

`IMPL_CAVEAT` is the text §3.4 exists to protect: it names **four** causes and refuses to
choose among them. Exactly one word changed («إشارة GNSS» → «إشارة الملاحة»). All four causes
are still named, in the same order, with the refusal clause intact — asserted by gate, not by
inspection. **Terminology moved; the commitment did not.**

### 14.4 What deliberately keeps «GNSS»

| kept | why |
|---|---|
| the key `gnss`, `GnssEvent`, `gnssAnomalies`, the events endpoint | wire and code, not text |
| the constant names `GNSS_HEAD`, `CAVEAT_GNSS` | Python identifiers |
| the English `_gnss_summary` (`api.py`) | English, and `tests/test_gnss_integration.py:34` asserts on it |
| `gnss.source` — **parenthetically**, «شذوذ إشارة الملاحة (GNSS)» | ⚠ provenance. Naming a source exactly is this project's standing discipline — `MIL_CAVEAT_ID` names `/v2/mil`, `SAT_CAVEAT_TAG` names UCS and CelesTrak. The plain term leads; the technical one is a gloss. **One word to drop if the owner prefers.** |

### 14.5 The constraints that were checked, and what they measured

| candidate constraint | verdict |
|---|---|
| a gate or test pinning the Arabic wording | ✅ none. The one GNSS assertion is on the **English** summary. |
| wire/prop breakage | ✅ none — only string *values* changed. |
| ⚠ width on the layer toggle (`.layer-btn`) | ✅ measured at the real computed font, **12.48px Qomra**, in the 340px button: text **58px → 89px**, and **112px** with the count suffix. No wrap, no overflow, ~2/3 of the button still free. |

⚠ «GPS» remains declined (§13.1): it names one constellation that `gnss_anomaly.py` cannot
distinguish from GLONASS / Galileo / BeiDou, and the AIS record carries no constellation field.

### 14.6 Phase status

✅ **Closed.** Seven commits: `0c4a826` · `78edd87` · `7d8f255` · `c606fea` · `0dd3d9a` ·
`836ce5a` · `58b842b`. Gates re-run against a fresh build each time (layout 11/11 ·
layout-system 10/10 · contracts 20/20 · backend 775 passed).

⚠ Still outstanding, and none of it is this phase's: §10's queue (⛔ the caveat is
**shortened, never deleted** — it has not been touched), the **14px** headroom قبرص has when
the legend is open, the Python process on `:8000` needing a restart for the backend prose, and
the three pre-existing test failures (§12.6, data drift).
