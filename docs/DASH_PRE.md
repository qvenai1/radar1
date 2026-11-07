# DASH-pre — assessment for reshaping `DashboardHome` into the primary monitoring surface

> ✅ **Phase 1 (backend) is DONE — DASH-1, 2026-08-01.** Every item this assessment sized as a
> backend build has landed and is curl-verifiable: `countries` on all five families, the
> `country=` filter, the orthogonal `importance` axis (§2's conflict, decided as proposed),
> IMO→MMSI resolution, and the ISO map. Phase 2's owner decision is **settled** — see
> PROJECT_STATE §6.9. **Phase 3 (frontend) is unblocked.**
>
> ⚠ Two things this assessment got wrong are corrected in PROJECT_STATE §3: the military-air
> family did **not** already carry `country`/`near_country` (the overlay bypasses the
> classifier, so it now re-classifies from lat/lon), and `satellite_passes.near_countries` was
> not being selected at all. ⚠ And §4's *keep ReplayBar* recommendation was **not taken** — the
> owner chose delete, executed in UI-3 (`c8bc2e3`); the backend contracts survive (§6.2).

**Assessment only. Nothing was built, migrated, or modified.** `frontend/` was read, never
written. Measured 2026-08-01, on `gis-syr-ThinkCentre-neo-50t-Gen-5`, against
`HEAD = 035b965`. Every claim carries its code reference.

**The design being measured against** (not built here): a home surface whose top card is
**country events** — military / suspicious / abnormal first, within an hours window, for a
selectable one of the seven AOI countries with its flag — and where clicking an entity shows
its **current payload** (vessel / aircraft / satellite). The detailed list, search, analysis and
historical track stay in the dashboard tab.

---

## The headline: the expensive part is already built

The obvious reading of this design is that it needs a country-boundary filtering capability.
**It does not.** Country attribution is **already computed for all five alert families**, by the
shared classifier at ingest. The only thing missing is a **field on the `Alert` envelope to
carry it** — today it is discarded, or leaked into the free-text `summary`.

That turns the largest-looking item into an additive model change. What actually needs
deciding is smaller and more interesting: **a documented severity principle contradicts the
requested ordering** (§2).

---

## Required → present / to build

| # | Required | Verdict | Reference | Size |
|---|---|---|---|---|
| 1a | Country attribution per alert | ✅ **exists, all 5 families** — discarded by the envelope | see §1 table | — |
| 1b | `country` field on the `Alert` model | 🔨 **build** — additive, no migration | `backend/models.py:1043` | **S** (~1 field + 5 call sites) |
| 1c | `country=` query param on `/alerts` | 🔨 **build** — filter in Python over the assembled list | `backend/api.py:1886` | **S** |
| 1d | Boundary-polygon filtering engine | ✅ **not needed** — classification happens at ingest | `backend/geo_area.py` | — |
| 2 | "Military first" ordering | ⚠ **owner decision, not a build** — contradicts a documented principle | `backend/alerts.py:15-19, 40-42` | **decision** |
| 3a | Current payload — vessel | ✅ exists in the **list** contract (19 fields) | `Vessel`, `backend/models.py` | — |
| 3b | Current payload — aircraft | ✅ exists in `/live` states (25 keys) | `backend/api.py:176` | — |
| 3c | Current payload — satellite | ✅ exists in `/satellites` elements | `backend/api.py:2049` | — |
| 3d | One contract per type | ❌ **no** — detail endpoints are identity-only, no position | §3 | **S–M** if a composed contract is wanted |
| 3e | Alert → entity resolution (maritime) | 🔨 **build** — `entity_ref.id` is an **IMO**, not an MMSI | `backend/alerts.py:199` | **S** |
| 3f | The three payload renderers | ✅ exist | `VesselStrip` · `FlightStrip` · `SatelliteStrip` | — |
| 4 | Historical track in the dashboard tab | ✅ **exists in two halves** — see §4 | `/snapshots`+`/replay`, `/history`, `/vessel/{mmsi}/track` | — |
| 4b | Satellite track | ❌ **no endpoint** | — | **M** if wanted |
| 5 | Seven country flags | ❌ **absent** — zero assets in the repo | §5 | **XS** (emoji) or **S** (SVG set) |
| 6 | Country boundaries | ✅ **exists** — real polygons, 7 features | `backend/data/aoi_countries.geojson` | — |
| 6b | name → ISO-3166 map (for flags) | 🔨 **build** — the asset carries only `name` | §6 | **XS** (7 entries) |

---

## 1. Country filter — already computed, never carried

**`/alerts` accepts only `window_hours: int = Query(24, ge=1, le=720)`**
(`backend/api.py:1886-1889`). No country parameter.

⚠ **And the `Alert` model has no geography at all** — no lat/lon, no country
(`backend/models.py:1043-1060`: `domain, family, signal_type, severity, severity_basis,
entity_ref, ts, summary, source, vintage, live, matched_query`). So country filtering is
impossible today **at any layer, including client-side**: the payload simply does not contain
the fact.

**But every family already knows its country upstream:**

| Family | Where country already lives | Evidence |
|---|---|---|
| `emergency` (air) | live state `country`, `near_country`, `latitude`, `longitude` | `/live` state keys, measured |
| `military` (air) | same live-state source | `backend/alerts.py:112` |
| `military` (satellite) | **`satellite_passes.countries` + `near_countries`** — real columns | table schema, measured |
| `maritime` | **`CompositeHull.aoi_waters`** — *"AOI countries whose waters the hull's events touched (M1)"* | `backend/models.py:688` |
| `implausible` | `vessel_states.country` / `near_country` | migration 021 |

⚠ **The satellite family already renders it and then throws it away**: `build_military_sat_alerts`
reads `r.get("countries")` and formats `"over {countries}"` **into the `summary` string**
(`backend/alerts.py:139-149`). The structured fact exists and is deliberately flattened to prose.

**⇒ Answer to the either/or in the brief: neither.** No new boundary contract is needed, and no
bbox workaround. The country is a **stored, already-classified column**; the build is to stop
discarding it. *(For contrast, the one genuinely bbox-only contract is `/search/area` — D4 takes
`lat1,lon1,lat2,lon2` and runs `point(longitude,latitude) <@ box(...)` on the GiST index,
`backend/api.py:865`. It is not the right tool here and does not need changing.)*

**One shape decision the owner should make:** `maritime` (`aoi_waters`) and `military-satellite`
(`countries`) are **lists** — a hull's events can touch several countries' waters and a pass
crosses several borders. Air alerts are **single-valued**. So the field should be
`countries: list[str]` uniformly, and a `country=` filter means *"this country is among them"*,
not *"equals"*. Modelling it as a scalar would silently drop multi-country facts.

---

## 2. ⚠ Severity — the requested order contradicts a documented principle

Measured (`backend/alerts.py`):

```
SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2, INFO: 3}          # line 42
emergency   → HIGH   (the only high-by-definition family)        # line 15
military    → INFO   (presence — informational, NEVER a threat)  # line 16
maritime    → the hull's OWN composite_rank (high|medium|low)    # line 17
implausible → LOW    (a spoofed/jammed vessel is usually a VICTIM, not an actor)  # line 19
```

`sort_alerts` is most-severe-first then most-recent-within-band (`backend/alerts.py:231-235`),
and the comment on line 40-41 is explicit: *"INFO (presence) sinks below LOW so the honest threat
ordering holds (a low-but-real anomaly ranks above informational presence)."*

**So the current order is the exact inverse of the request for military.** Military presence is
deliberately placed **last**, and that placement is a stated honesty commitment, repeated in the
API docstring (`backend/api.py:1893-1896`) and carried per-card in `severity_basis` so a card
"can never present military presence as a high threat".

⚠ **This is not a missing feature — it is a values conflict, and it should not be resolved by
quietly re-ranking `SEVERITY_ORDER`.** Doing that would make every existing surface (the monitor
feed, the caveat mechanism, `severity_basis`) start asserting something the project has
explicitly committed to not asserting.

**The reconciliation that costs nothing and keeps both true** — and which matches how this
project already separates orthogonal axes (`position_quality` vs event `classification` vs
`imo_quality`, three axes never conflated, §2.9):

> Keep `severity` as the **honesty** axis, unchanged. Add a second, separate **relevance**
> ordering used *only* by the country-events card: within one country and one hours window,
> "what is most worth looking at" is legitimately *military first*, because presence in a
> specific country's airspace/waters is the salient fact — while the card still shows the
> honest `severity` badge (INFO) on each item.

Two axes, one card. Nothing is downgraded and nothing is over-claimed. **Owner's call.**

---

## 3. Current payload — present, but split list + detail

⚠ **All three detail endpoints are identity/enrichment lookups with no position:**

| Endpoint | Model | Fields | Position? |
|---|---|---|---|
| `/vessel/{mmsi}` | `VesselEnrichment` | **3** — `mmsi`, `photo`, `license` | ❌ |
| `/aircraft/{icao24}` | `AircraftEnrichment` | 6 — `registration`, `aircraft_type`, `operator`, `operator_country`, `photo`, `route` | ❌ |
| `/satellite/{norad_id}` | `SatelliteEnrichment` | 14 — SATCAT identity, `owner`, `launch_*`, `decay_date`, `ops_status`, `satcat_snapshot_date` | ❌ |

**The current payload lives in the list contracts instead:**

- **Vessel** — 19 fields: `mmsi, ship_id, lat, lon, speed, course, heading, nav_status,
  timestamp, imo, name, callsign, ship_type_code, ship_type, destination, draught, flag,
  source, data_origin`. *(This is what the 307 px card renders — the field count is why it is
  307 px.)*
- **Aircraft** — `/live` states, 25 keys incl. `latitude, longitude, baro_altitude,
  geo_altitude, velocity, true_track, vertical_rate, squawk, on_ground, country, near_country,
  distance_km, position_quality`.
- **Satellite** — `/satellites` elements (TLE-derived).

**⇒ Current payload is available per type, but as list + detail, not one contract.** The
frontend already composes exactly this, and `VesselStrip` / `FlightStrip` / `SatelliteStrip`
already render it. **No new contract is strictly required** for detail-on-click; a composed
`/entity/{kind}/{id}` would be a convenience, not a prerequisite.

⚠ **One real gap: the maritime alert's entity id is an IMO, not an MMSI.**
`entity_ref=AlertEntityRef(kind="vessel", id=(h.imo or h.hull), …)` (`backend/alerts.py:199`).
Every vessel payload path is MMSI-keyed (`Vessel.mmsi`, `/vessel/{mmsi}`,
`/vessel/{mmsi}/track`), so opening a maritime alert needs an **IMO → MMSI resolution**. The data
exists (`vessel_identity` carries both), and note it is **one-to-many across a re-flag** — the
same IMO legitimately maps to several MMSIs over time, and one of them is the *current* one.
This must resolve to "the MMSI holding this IMO **now**", with the others reachable as history.

---

## 4. ⚠ ReplayBar — **KEEP**, and it settles the pulled UI-3 item

`ReplayBar.tsx` is a **fleet-wide time scrubber**, not a per-entity track:

> *"Replay scrubber over `/api/v1/snapshots`. Pure presentation — App owns the data, the
> playback timer, and the live-feed pause/resume."* — `ReplayBar.tsx:1-4`

Controls: exit · play/pause · prev/next · a `range` scrub over the snapshot series · speed
**1 / 2 / 5**. It is driven by two working backend contracts — `/snapshots` (the snapshot index,
`backend/api.py:261`) and `/replay?at=` (the whole fleet's state at an instant,
`backend/api.py:298`) — and it is their **only UI consumer** (`frontend/src/api/client.ts:88-90`).

**Historical track exists in two distinct halves, and they are not the same capability:**

| Capability | Contract | Consumer |
|---|---|---|
| **Fleet time-travel** (all contacts, at a past instant) | `/snapshots` + `/replay?at=` | **ReplayBar — the only one** |
| **Per-entity track** — aircraft | `/history?icao24=` (`backend/api.py:227`) | — |
| **Per-entity track** — vessel | `/vessel/{mmsi}/track` (`backend/api.py:2229`) | — |
| **Per-entity track** — satellite | ❌ **no endpoint exists** | — |

**Verdict: KEEP ReplayBar and give it a nav entry.** Deleting it would remove the project's only
time-travel surface *and* orphan two implemented, working endpoints. It directly serves the
owner's "historical track live in the dashboard tab" — for the fleet half. The per-entity half
is already served for aircraft and vessels by separate contracts that currently have **no UI at
all**.

⚠ **This reverses the disposition recorded in PROJECT_STATE §6.2**, which framed the choice as
"add a nav entry **or delete**". On the evidence, delete is the wrong branch: the component is
not dead code, it is a built feature with an unwired entry point. §6.2 should be closed as
*keep + wire*.

---

## 5. Flags — absent; the cheapest option needs no asset at all

**Measured: zero flag assets.** No `frontend/public/`, no `frontend/src/assets/`. Every `.svg`
in the tree is inside `.venv` (third-party: GDAL, Playwright). Nothing to reuse.

Options, **suggested only — nothing fetched**, and open-licence only per the instruction:

| Option | Licence | Cost | Note |
|---|---|---|---|
| **Unicode regional-indicator emoji** 🇸🇾 🇹🇷 🇮🇶 🇯🇴 🇱🇧 🇮🇱 🇨🇾 | none needed — text | **zero files** | Renders natively; no build step, no attribution, no bundle weight. ⚠ Glyph coverage varies by platform/font, so verify on the target browser before committing to it. |
| `lipis/flag-icons` | **MIT** | 7 SVGs (~2 KB each) | The conventional choice; per-country files, no runtime. |
| `twemoji` flags | **CC-BY 4.0** | 7 SVGs | Requires visible attribution — a licence obligation to carry, unlike MIT. |

**Recommendation: try the emoji first** — it is the only option with no file, no licence and no
attribution, and this is a 7-item picker, not a flag library.

### 5.1 FLAG-1 — the emoji holds for six countries and NOT for Syria

⛔ **AMENDMENT (2026-08-11). The recommendation above is now correct for six of the seven and
wrong for SY, on a ground the table did not consider: not whether the glyph *renders*, but
**which flag it renders**.** Syria readopted the green-white-black flag with three red stars in
2025. The emoji codepoint `U+1F1F8 U+1F1FE` carries no version — the picture comes from whatever
emoji font the **viewer** has, so the same build shows the current flag to one analyst and the
superseded red-white-black two-star flag to another. There is no feature test for it and no way
to ask for a variant.

**Measured, not assumed** (dev host, 2026-08-11): `fonts-noto-color-emoji` **2.048** renders the
**current** flag — rasterised the glyph at 96px via `pango-view` and inspected it. So this host
is fine and the defect is invisible here. That is exactly why it needed measuring rather than
eyeballing: **the bug lives on other people's machines**, and any font predating the change
paints the rejected flag.

⚠ **The row's own caveat — "verify on the target browser" — is not a sufficient control for
this.** It assumes one target. A browser-verified emoji is still viewer-dependent, and "the old
Syrian flag is rejected" is a requirement, not a preference: a requirement that holds only on
fonts we happen to sample is not met.

**Resolution — split by context, because one of the two call sites cannot hold an SVG:**

| Call site | Context | Treatment |
|---|---|---|
| The seven-country picker (`CountryEvents`) | JSX | **`<Flag iso>`** — SY drawn as inline SVG geometry (2:3, three bands, three stars at ¼/½/¾), the other six still emoji. Deterministic on every viewer. |
| The vessel registry line (`meta`, via `splitHeadline`) | **plain string** | `isoFlag("SY")` returns `""`. No SVG can go into a string, so it declines to guess, and the mandatory words «علم التسجيل: …» carry the registry alone — the same fallback §5 already specified for the no-ISO case. |

Still **zero asset files and zero licence obligations**: the SVG is inline in the component that
documents why it exists, so it cannot 404 and adds no fetch. Colours `#007A3D` / `#CE1126` are
literals **by exception** — a national flag is not themed chrome, and repainting it with the
identity tokens (THEME-2) would make it a different flag.

Verified before hand-off: `tsc -b` clean, `vite build` clean, and the picker screenshotted from
the real build with the new flag in place beside the six emoji.

---

## 6. Boundaries — already committed, real polygons

✅ **`backend/data/aoi_countries.geojson`** — 138 KB, `FeatureCollection`, **7 features with
true polygon geometry** (measured):

| Country | Geometry | Rings | Points |
|---|---|---|---|
| Turkey | MultiPolygon | 6 | 3,640 |
| Iraq | Polygon | 1 | 841 |
| Syria | Polygon | 1 | 591 |
| Israel | Polygon | 1 | 373 |
| Jordan | Polygon | 1 | 364 |
| Lebanon | Polygon | 1 | 312 |
| Cyprus | Polygon | 1 | 202 |

This is the asset `backend/geo_area.py` already uses for AOI classification and the 12 NM
territorial-sea band — i.e. **the same boundaries that produced the `country` values in §1.**
Filtering and display are therefore guaranteed consistent by construction.

⚠ **Properties carry only `name`** — no ISO 3166-1 code. A flag lookup needs a 7-entry
name → ISO map (`Syria→SY, Turkey→TR, Iraq→IQ, Jordan→JO, Lebanon→LB, Israel→IL, Cyprus→CY`).

⚠ **Answering the brief's question directly: `zones_aoi.json` and `ports_aoi.json` do NOT
contain country boundaries.** They are infrastructure assets — 36 gas-field/platform zones and
190 port features, points with radii. Do not mistake them for a boundary source.

⚠ **A display caveat, measured:** Turkey is a 6-ring MultiPolygon of 3,640 points, and the whole
file is 138 KB. Serving raw geometry to the browser for an outline is heavier than a 7-item
picker needs. If boundaries are only for *filtering*, they never leave the backend and this does
not arise.

---

## Recommended phasing — backend first, UI touches nothing until it is done

**Phase 1 — backend only (no UI change, nothing visible).** Everything here is additive and
independently verifiable:

1. Add `countries: list[str]` to `Alert`; populate it at all five call sites from the facts
   that already exist (§1). Satellite: read `r["countries"]` structurally instead of only
   formatting it into `summary`.
2. Add `country=` to `/alerts` — filter over the assembled list, "is among".
3. Resolve the maritime `entity_ref` IMO → **current** MMSI (§3), keeping prior MMSIs reachable.
4. Add the 7-entry name → ISO map beside the AOI asset (§6).

*Verifiable without any frontend: `curl '/alerts?country=Syria&window_hours=6'` returns only
Syria-attributed alerts across all five families, and counts stay honest.*

**Phase 2 — the owner decision, before any UI work.** §2: keep `severity` as the honesty axis
and add a separate relevance ordering, or re-rank. **This must be settled first** — it changes
what the top card *is*, and it is the one item that cannot be derived from measurement.

**Phase 3 — UI (the frontend window).** Only after 1 and 2: the country picker with flags, the
country-events card, entity detail-on-click reusing the three existing strips, and a nav entry
for ReplayBar (§4).

**Explicitly out of scope until asked:** a composed `/entity/{kind}/{id}` contract (§3d — a
convenience, not a prerequisite) and a satellite track endpoint (§4b — no consumer yet).

---

## What contradicted the brief's assumptions

| Assumption in the brief | Measured |
|---|---|
| Country filtering may need a new boundary contract, or a `country` field on events to filter on | ⚠ **Neither.** Country is already computed for **all five** families; the `Alert` envelope just has nowhere to put it. The satellite family already reads it and flattens it into prose. |
| The existing four-family ordering may need re-prioritising | ⚠ **It is not a build item.** Military is INFO **by documented commitment** ("presence — informational, NEVER a threat"), deliberately sorted below LOW. Re-ranking would contradict a stated honesty principle — an owner decision, not a code change. |
| "Current payload" might be scattered | ✅ Confirmed scattered, but **not missing**: identity in the detail endpoints, live state in the list endpoints, and all three renderers already exist. `VesselEnrichment` is only **3 fields**. |
| ReplayBar might be the historical-track mechanism | ⚠ **Half.** It is *fleet* time-travel, not a per-entity track. Per-entity track exists separately for aircraft and vessels, and **not at all** for satellites. Verdict is still **KEEP** — it is the sole consumer of two working endpoints. |
| `zones_aoi` / `ports_aoi` might carry country boundaries | ⚠ **They do not.** Infrastructure points and radii. The real boundary asset is `aoi_countries.geojson`, and it was already there. |

---

# DASH-3 — المحور المعرفي: ساعتان لا ساعة واحدة

**2026-08-23.** بند الخلفية من خطّة إعادة تصميم اللوحة (البند ٣/٥). سبقه بحثٌ في أنظمة
حقيقية، وما يلي مشتقّ منه لا من الذوق.

## 1. المشكلة، مقيسة على الكود لا مستنتَجة

كل عائلة في `alerts.py` تضع في `ts` وقتاً **بشكل الرصد**، وواحدة تضع فيه **المستقبل**:

| العائلة | `ts` | ماذا يعني فعلاً |
|---|---|---|
| طوارئ / عسكري جوّي | لقطة التغذية | ماضٍ قريب |
| مركّب بحري | آخر حدث للهيكل | تاريخي، **ويصل متأخّراً** |
| مواضع غير معقولة / MT | آخر / أول رصدة | تاريخي |
| قمر عسكري | **بداية الممرّ** | ⛔ **متقدّم على الآن** |

⇒ فرز قائمة واحدة بـ`ts` يشابك تنبّؤاً بالغد مع رصدٍ من الأسبوع الماضي ويسمّي الاثنين
«متى وقع». وهذا جزء من «العشوائية» التي رفضها المالك في تصميمين متتاليين.

## 2. لماذا ساعتان — أربع هيئات معايير ترفض دمجهما

* **OGC O&M (20-082r4)** — `phenomenonTime` «الوقت الذي تنطبق عليه النتيجة» مقابل
  `resultTime` «اللحظة التي أصبحت فيها النتيجة متاحة». ومثالها رقم ٥ حالتنا نصّاً:
  المحاكاة تقدّر ظواهر في المستقبل، فـ`phenomenonTime` وقت الانطباق و`resultTime` وقت
  تنفيذ المحاكاة. **واختبار التنبّؤ الآلي هو `resultTime < phenomenonTime`.**
* **GTFS-Realtime** — `NO_DATA` حالة **مستقلّة** عن «في موعده»، و`uncertainty` بصفر محجوز
  لليقين الحقيقي.
* **NWS** — التنبّؤ يحمل **فترة** `validTime`، والرصد **لحظة**؛ ونقاط نهاية منفصلة.
* **STIX 2.1** — `indicator` (نمط) منفصل عن `sighting` (تأكيد أنه شوهد)، والثقة على
  **الحكم** لا على البيانات الخام.

وسابقة الضرر موثّقة: **SS El Faro** (NTSB MSR-17/02، ٣٣ قتيلاً) — رسمٌ خلط حركةً مرصودة
مع مسار متوقَّع بفارق بلغ **١٨٠ درجة**، وسببه الجذري **تباين دورتَي التحديث** في صورة واحدة.

## 3. ما شُحن

**الحقول** (إضافية بالكامل — `ts` لم يتغيّر ولم يُهجَر، وكل مستهلك قائم يعمل كما هو):

| الحقل | المعنى |
|---|---|
| `phenomenon_start` / `phenomenon_end` | متى ينطبق — والنهاية `None` تعني **لحظة** أو فترة **مفتوحة**، لا صفراً ولا مجهولاً |
| `result_time` | متى حُسب/وصل — يختلف عن `vintage` (طزاجة البيانات الأصل) |
| `first_seen_at` | أول مشاهدة — `None` = **نوعية مجهولة، لا «جديد» ولا الحقبة** |
| `observed_age_s` | عمر الرصدة — **إلزامي**، ويُرجَع **سالباً** للممرّ المستقبلي بلا قصر |
| `counterparts[]` | السفن المقابِلة — **قائمة**، لأن الهيكل قد يقترب من عدّة سفن |
| `is_prediction` | **محسوبة لا مخزَّنة**، وتفشل **مغلقة** عند نقص أي ساعة |

**الترحيلات 047 → 050**، وقصّتها في §4.

## 4. ثلاثة أخطاء، وما التقط كلاًّ منها

1. **`ADD COLUMN … DEFAULT now()` يملأ الصفوف القائمة** (PostgreSQL ≥ 11) — فوَسم الأرشيف
   كلّه (72,148 صفّاً) بطابع واحد، وهو حرفياً ما حذّر منه تعليق 047 نفسه.
   *التقطه:* التفجير. *أصلحه:* 048، بمُسنَد يقرأ `schema_migrations.applied_at` وحارس
   **يفشل مغلقاً**. ⚠ الصيغة الصحيحة: `ADD COLUMN` بلا افتراضي، ثم `SET DEFAULT` منفصلاً.
2. **الكواشف تحذف ثم تُدرج** (`detect_ais_gaps.py:249` وأربعة غيرها) — فالصفّ يعود جديداً
   وينطلق الافتراضي. تحقّقتُ من مسار `upsert` وحده، أي **اختبرتُ ما افترضتُه لا ما يفعله
   الكود**، فمرّ الفحص وبقي العطب. *الدليل:* بعد إفراغ العمود عند 10:03 أعادت المؤقّتات
   وسم **71,995 صفّاً** عند 10:10–10:25. *أصلحه:* 050.
3. **قراءة حقول جديدة مباشرةً** من `CompositeHull` — كسر ١٨ اختباراً ببيانات جزئية.
   *التقطته:* المجموعة. *أصلحه:* `getattr` بقيم افتراضية، وفق عُرف الملفّ القائم.

## 5. الإصلاح الجوهري — هويّة تعمّر أطول من إعادة الحساب

`maritime_event_sightings`: مفتاحه `(event_type, source, vessel_key, start_ts)` — نفس مفتاح
`maritime_events` — يُكتب مرّة و**لا يُحذف منه أبداً**. نفس شكل `dedup_key` في PagerDuty
و«بصمة التسميات» في Alertmanager: مفتاح ينجو من دورة حياة ما يُعرّفه.

⛔ **مُشغِّل، لا ستّة كواشف معدَّلة.** المُشغِّل يعمل على **كل** مسار إدراج — الكواشف الخمسة
والـbackfill وأي كاتب مستقبلي. و«صفر تعديل على الكواشف» هنا **خاصيّة في الآلية**، لا ملاحظة
عن ستّة ملفّات تتّفق اليوم — و§4.2 هو الدليل على أن الفرق بين الاثنين ليس لفظياً.

⚠ `NEW.vessel_key` **لا يُقرأ داخل المُشغِّل**: العمود مولَّد (`COALESCE(mmsi, ship_id)`،
ترحيل 043) وPostgreSQL يحسب المولَّد **بعد** مُشغِّلات BEFORE. التعبير مُعاد اشتقاقه،
**فإن تغيّر 043 وجب تغيير المُشغِّل معه** وإلا اختلف مفتاح المشاهدة عن مفتاح الحدث.

## 6. الإثبات — بدورتَي كاشف حقيقيتين

```
دورة ١ (استبدال كامل): 65,327 صفّاً · first_seen_at = 11:02
دورة ٢ (استبدال كامل): 65,329 صفّاً · first_seen_at = 11:02  ← نجا الحذف
                                      computed_at   = 11:02:55 ← تقدّم كما يجب
```

وعبر `/api/v1/alerts` الحقيقي: `HIGHLAND CHIEFTAIN` بفترة **٤٢ يوماً** ومقابِلة `SANTORINI`؛
`DMSP 5D-3 F16` انطباقه غداً 05:28 واشتقاقه اليوم 10:20 ⇒ `is_prediction = true` **بلا علَم
يدوي**. و**٢٤ تنبيهاً** يحمل مقابِلته — الفجوة التي مُنعت بها كل عروض العلاقات، مغلقة.

## 7. قيد يجب أن يُذكر للقارئ

المشاهدات بدأت التراكم **2026-08-23 11:02Z**. فـ«أول مشاهدة» لكل حدث قائم = **لحظة تفعيل
النظام**، لا تاريخه الحقيقي. صادق وغير قابل للاسترجاع، ويخفّ أثره كل يوم — لكن أي واجهة
تعرض «الجديد» في الأيام الأولى يجب ألّا تقدّمه كتاريخ اكتشاف.

## 8. ما لم يُبنَ بعد

* **مؤشّر استيعاب مستقلّ** (`ingest_seq`) — التغذية **عديمة الحالة** (لا جدول تنبيهات؛
  `/alerts` يُبنى عند كل طلب)، فعلامة «ما الجديد» تحتاج إمّا تجسيد التنبيهات أو البناء على
  `first_seen_at` الذي شُحن هنا.
* **`first_seen_at` لغير البحري** — الجوّي والفضائي بلا جدول أحداث مخزَّن.
* **علامة مائية لكل محلّل** — صفّ واحد على الخادم يكفي (محلّل واحد)؛ و`localStorage` **لا
  يكفي**: كل أوضاع فقدانه صامتة وكاملة ولا تُميَّز عن «كل شيء جديد فعلاً».

---

# DASH-4a — حالة الفرز الجماعية

**2026-08-23.** بندٌ لم يكن في الخطّة، أوجبه تصحيحُ مقدّمة: **المحلّلون عدّة، لا واحد**،
على شاشة مشتركة بحالة واحدة وبلا تمييز بينهم (قرار المالك).

## 1. ما أبطله تصحيح المقدّمة

بُنيت ثلاثة استنتاجات على «محلّل واحد»، وسقط أوّلها بالكامل:

* ❌ **«لا تنسخ نموذج مراكز العمليات — لا تسليم مناوبة عندنا».** خطأ. Elastic تستعمل
  «مفتوح/مُقَرّ/مغلق» وSentinel «جديد/نشط/مغلق»، وهي حالات **جماعية** لأنها يجب أن تنجو من
  التسليم بين محلّلين. وهذا وضع النشر الحالي بالضبط.
* ⚠ ملفّ العمل صار **مشتركاً** لا شخصياً.
* ⚠ «ما الجديد» صار **جماعياً** — «منذ آخر تصريف» لا «منذ آخر نظرتي». وهذا **تبسيط**:
  صفّ واحد بدل صفّ لكل محلّل، وبلا نموذج مستخدمين ولا مصادقة.

## 2. العيب الذي كشفه — بلاطة تكذب

`CountryEvents.tsx:333` كان يعرض `sev.medium` — **عدد التنبيهات متوسّطة الخطورة** — تحت
تسمية **«قيد المراجعة»**. تسميةٌ تَعِد بحالة عمل وتُسلّم دلواً من محور الخطورة، وقرئت «٠» على
شاشة حيّة تحمل ٢١ تنبيهاً لأنه لم يكن فيها متوسّط واحد.

⛔ **ومع عدّة محلّلين يصير العيب ضارّاً بفعله:** «٠ قيد المراجعة» تُقرأ «لم يبدأ أحد بشيء»،
فيفتح اثنان نفس السفينة. الازدواج هو الهدر الكلاسيكي لأي فريق مشترك.

## 3. لماذا جدول، ولماذا هذا المفتاح

**التغذية عديمة الحالة**: لا جدول تنبيهات؛ `/alerts` يُعيد بناء كل عائلة من استعلامات حيّة عند
كل طلب. فالحالة لا تعيش **على** تنبيه، بل تُفهرَس **بهويّته**، وتنجو من إعادة حسابه.

⛔ **والمفتاح ليس جديداً:** `notify/identity.py::dedup_key` يجيب أصلاً «ما الهويّة المستقرّة
للشيء الذي يدور عنه هذا التنبيه؟» — بُني لسجلّ إرسال البوت، وهو **نفس السؤال**. استعماله يعني
أن البوت واللوحة يتّفقان على معنى «التنبيه نفسه»؛ ومفتاح ثانٍ موازٍ كان سيسمح لهما بالاختلاف،
والاختلاف كان سيبقى غير مرئي حتى يُغلق محلّل شيئاً يعيد البوت إرساله.

⚠ وذلك الملفّ يحمل **بوابة انحراف** عند الاستيراد (`SEVERITY_BASIS <= _RULES`) تمنع عائلة
جديدة من الوصول بلا قاعدة هويّة — والجدول يرث هذه الحماية.

## 4. الحالات الثلاث

| الحالة | التمثيل |
|---|---|
| `open` | **غياب الصفّ**، لا قيمة مخزَّنة — فالجدول ينمو مع **العمل** لا مع التغذية |
| `reviewing` | أحدهم عليه — وهي التي تمنع الازدواج |
| `closed` | عولج، ومعه `note` يبقى بعد صاحبه |

⛔ **ولا عمود `analyst`، وغيابه قرار لا سهو.** لا نموذج مستخدمين في المشروع والمالك اختار
إبقاءه كذلك؛ وعمود «مَن» فارغ يُغري الواجهة بعرض إسنادٍ لا يسنده النظام، والإسناد الفارغ
يُقرأ «لا أحد» لا «غير مسجَّل».

⚠ **و`change_sig` مخزَّن بجانب الحالة**: تنبيه مُغلق تغيّر توقيعه **ليس مُغلقاً بعد** — هو
بيان جديد عن جسم معروف، وإخفاؤه خلف حكم بائت هو كيف يبدأ نظام الفرز بكتم ما وُجد ليُظهره.
يظهر على السلك كـ`triage_stale`.

## 5. أوّل نقطة كتابة في هذا الـ API

`POST /api/v1/alerts/triage` — وكل ما عداها للقراءة. نتيجتان مقصودتان:

* **بلا مصادقة**، لأن المشروع بلا نموذج مستخدمين بقرار المالك. ⚠ **وهذا آمن ما دام الـ API
  مربوطاً على localhost فقط.** كشف المضيف على شبكة يجعل الفرز — بما فيه `closed` — قابلاً
  للكتابة من أي جهة تصل المنفذ. من يغيّر عنوان الربط يملك هذا القرار.
* **تكتب صفّاً واحداً بالمفتاح الأوّلي** — لا مسار جماعي ولا حرف بدل ولا «أغلق كل شيء»: خطأ
  واحد يفقد حالة عنصر واحد.

⚠ و`state=null` **تحذف** الصفّ بدل تخزين «مفتوح» — فلـ«مفتوح» تمثيل واحد لا اثنان.

⚠ **والقراءة تفشل مفتوحة عمداً**، وهي الموضع الوحيد في `api.py` الذي يصحّ فيه ذلك: إن تعذّر
الوصول للمخزن يُعرض كل تنبيه `triage=None` أي **مفتوح**. البديل — إخفاء تنبيه لأن حالته لم
تُقرأ — يكتم إشارة حيّة بناءً على استعلام فاشل. محلّل يفتح ما عولج يكلّف نظرة مكرّرة؛ محلّل
لا يرى طارئاً لأن الاستعلام انتهت مهلته يكلّف ما وُجد المشروع لمنعه.

## 6. الإثبات — دورة الحياة كاملة عبر النقطة الحقيقية

```
هدف: CROWN IRIS (maritime_composite)   triage قبل: None ✓
POST reviewing  → 200 · القراءة: triage=reviewing note=… stale=False
POST closed بتوقيع مختلف → triage=closed  stale=True   ← «مُغلق لكنّه تغيّر» ✓
POST state=null → triage=None                           ← الصفّ حُذف ✓
POST state="خطأ" → 400                                  ← الحالة مُتحقَّق منها ✓
```

والبلاطة تعدّ الآن `triage === "reviewing"` — تقول ما تعرضه.
المجموعة: **1051 نجاحاً**؛ بوابات الواجهة الستّ خضراء.

---

# DASH-4b — إعادة بناء اللوحة، وDASH-5 — الملفّ المشترك

**2026-08-23/24.** ما بُني فعلاً بعد أن رُفضت ثلاثة مقترحات بصرية.

## 1. لماذا رُفض ما قبله — والقياس هو الحكم

| المقترح | العطب |
|---|---|
| الأول | أثاث يحكم الشاشة: ~50,000 بكسل² لكل حقيقة معروضة |
| الثاني | عارض سجلّات؛ **فكّك السفينة الواحدة إلى صفّين** |
| الثالث | رفٌّ 300px ⇒ خليّة الحارة **7px** والتحفّظ ~30 حرفاً بالسطر |

⛔ **والقياس الذي أدانها الثلاثة كان في حوزة المشروع أصلاً**: مسح Cockburn وزملائه
(ACM Computing Surveys 41(1)، 2008) ينقل عن Baudisch أن التركيز+السياق أسرع بنسبة تصل إلى
**٥٦٪** من overview+detail، وأن الشاشة المقسومة **أسوأ بدلالة إحصائية في المهام الديناميكية**.
والمراقبة المستمرة نشاط ديناميكي. صمّمتُ شاشة مقسومة ثلاث مرّات بعد نقل هذا الرقم.

## 2. الأسطح — لكلٍّ العرض كامل

| السطح | البند | ما تغيّر |
|---|---|---|
| **الخريطة** | ٤ب‑١ | الخريطة من **٤٠٪ إلى ٩٨٪**؛ المساحة تُطوى فتصير قابلة للنقر |
| **المراقبة** | ٤ب‑٢ | صفّ لكل كيان، حارة لكل نمط — مسار **636px** بدل 190 |
| **الكيان** | ٤ب‑٣ | `headline`/`why`/`caveat` عند **78ch** |
| **الفرز** | ٤ب‑٤ | «قيد المراجعة»/«مُغلق» جماعية، بعبارة كتابة مشتركة |

⚠ **ونتيجة تستحقّ التسمية في ٤ب‑٣:** عمود التحليل كان 488px وصار 493 — **الرقم لم يتحرّك
والسبب تغيّر كلياً**. كان مصادفةَ ما يتركه المقعد؛ صار حدّ القراءة المُعلَن، ويثبت عند 1900.

## 3. الحارات — ولماذا لزم حقل جديد

المظروف (`phenomenon_start`→`_end`) **لا يصلح حارة**. قياس حيّ: `ZEYNEP I` مظروفه **٢٩ يوماً**
وأنماطه الثلاثة في ٢٦ يوليو و٣ أغسطس و٢٤ أغسطس — ثلاث لحظات. رسمه كشريط يقول إن ثلاثتها
دامت شهراً.

⇒ `pattern_spans`: نافذة لكل نمط، و`end: null` يمرّ كـ**مفتوح** لا كصفر. بلا استعلام جديد —
`end_ts` عمود على العرض أصلاً (ترحيل 049). مسقوف بـ60 مع `n_pattern_spans` يحمل العدد الحقيقي.

⛔ **ولا تُجمع في درجة.** Splunk ES يعطي كل فئة حارتها ليُقرأ التزامن كمحاذاة رأسية لا كرقم،
وICD 203 يمنع دمج الاحتمال بالثقة في عبارة واحدة — و«خطر ٧٨» يُسقط المحورين في واحد.

## 4. الملفّ المشترك (DASH-5) — لقطة لا إشارة

⛔ **القرار الذي يشكّل الجدول:** التغذية **عديمة الحالة**، فعنصرٌ جُمِع اليوم يختفي غداً حين
تمضي نافذته. ملفٌّ يخزّن `dedup_key` فقط **يُفرِغ نفسه**، وتقريرٌ يستشهد به يشير إلى صفوف لا
يستطيع أحد إنتاجها.

⇒ كل صفّ يخزّن الدعوى **كما كانت لحظة الجمع**: الكيان والرتبة والحقول الثلاثة والأزمنة.
وهذا ما يوجبه ICD 206 حين يختلف زمن المعلومة عن زمن النشر.

⚠ **واللقطة قد تتقادم، وهذا مقصود.** `still_live` يقول إن نفس المفتاح في التغذية **الآن** —
معلومةٌ لا تصحيح: عنصر خرج من النافذة **لم يُدحَض**، واللقطة **لا تُحدَّث منه**. تحديثها صامتاً
يهدم سجلّ ما بُني عليه الحكم.

⛔ **والتحفّظ يُخزَّن مع البقيّة ولا يسقط**: دليلٌ يُنسخ إلى تقرير بلا «ما لا يعنيه» هو العطب
الذي يسمّيه `models.py`، في أدوم صوره.

## 5. الأمن — حارس الكتابة

الخادم يعمل بـ`--host 0.0.0.0` **عمداً** (عدّة محلّلين على أجهزة مختلفة). ونقطتا الكتابة
الوحيدتان محروستان بـ`DASHBOARD_WRITE_TOKEN`.

⛔ **وليست مصادقة، ولا تُوصف كذلك في أي موضع.** حزمة الواجهة تُسلَّم للمتصفّح فلا تخفي سرّاً
عمّن يفتحها. ما تمنعه: كل ما يصل المنفذ **دون فتح اللوحة**. تقليلٌ حقيقي للسطح، ووصفٌ أمين لحدّه.
⚠ **يفشل مغلقاً**: رمز غير مضبوط يرفض كل كتابة (503)، لا يسمح بها.
⚠ و`hmac.compare_digest` لا `!=` — المقارنة العادية تسرّب البادئة بقياس الزمن.

## 6. أربعة عيوب لم يكشفها إلا التشغيل الفعلي

1. **العميل يرسل توقيعاً خاطئاً** (`severity_basis` بدل توقيع الهوية) ⇒ كل عنصر يُفرَز يعود
   «متقادماً» فوراً. الميزة تنقلب على نفسها **وتبدو عاملة**. أُصلح بنشر `change_sig` على السلك
   ليردّه العميل — التعريف الثاني هو ما أنتج العيب.
2. **وسم التقادم يكتب «مُغلق» دائماً** — فسفينة قيد المراجعة تُعرض مغلقة.
3. **سطح الكيان يتجمّد** عند لحظة الفتح فلا يرى الكتابة.
4. **المسار المتساهل يخفي استدعاءً مكسوراً**: `still_live` كان `False` للجميع لأن استدعاء
   التغذية داخلياً يمرّر كائنات `Query(...)` بدل قيم. الفشل المتساهل عمل كما صُمّم **وستر
   عطباً** — ولم يكشفه إلا النظر إلى القيم لا إلى رمز الاستجابة.

⚠ **وتشغيلان للاختبار كانا مضلّلين**: الـ API كان يخدم كوداً أقدم من الحارس، والمِسبار أعاد
استعمال ملفّ متصفّح يحتفظ بالعبارة. «بلا رمز ⇒ بلا خطأ» كان أثراً مصطنعاً **مرّتين**.

## 7. ما بقي مفتوحاً

* **`--sev-low`: رقمان، ولكلٍّ سطحه** — **3.68:1 على `--panel`** (السطح الذي تجلس عليه شارة
  `.er-sev`) و**4.35:1 على `--bg`** (المُقاس في `tokens.css:420` بعد THEME-3). ليسا متناقضين،
  وكلاهما **دون** عتبة النصّ 4.5:1؛ والاستعمال النصّي وحيد: `app.css:2971` عند 10px، أمّا
  `app.css:2521` فحدٌّ بعرض 2px عتبته 3:1 ويمرّ. محور الخطورة مجمَّد ببوابات سابقة، فرفعه
  قرار مستقلّ (PROJECT_STATE §6.0). سُجّل ولم يُغيَّر صامتاً.
* ✅ **«عيب عزل الاختبارات» — أُغلق، ولم يكن عزلاً قطّ** (2026-08-24). كان **ثلاثة أشياء**
  لا شيئاً واحداً: تجاوُزٌ صامت يتبع ترتيب الاستيراد (13 ملفّاً من 24 بلا استيراد `backend`
  علويّ ⇒ تُطبع «1 skipped» ولا ينفَّذ شيء)، **وتأرجحٌ 6↔7 سببه مسابقة ساعة حيّة** عند عتبة
  180 ث لا ترتيب التشغيل، وأرقامٌ مثبّتة على جداول تُحذف وتُدرج. وكشف اثنين من العيوب الحقيقية
  في الطريق: §6.17 و§6.18. التفصيل في PROJECT_STATE §11.3.
* ⚠ **واجهة الملفّ المشترك — بُنيت** في `ae09c5f` (`CaseFile.tsx` موصولاً في
  `DashboardPanel.tsx:380`، مع «احفظ للتقرير» وتصدير مسوّدة التقرير). الذي **بقي** مفتوحاً هو
  **محرّر الملاحظة لكل عنصر**: `case_items.note` يُكتب عند الإضافة ولا سبيل لتعديله بعدها،
  وهو موضع «مَن ولماذا» ما دام لا عمود `owner` بقرار. (PROJECT_STATE §11.3 بند ٤.)

---

# DASH-6 — فصل اللوحة بنيوياً، وإطلاق سطح الهبوط

**2026-08-24.** بندٌ سببه أن القيد كُتب ثلاث مرّات ونُقض ثلاثاً: الحذر ليس آليّة.

## 1. البوابة كانت عمياء — وهذا أوّل ما قيس، لا آخره

نصّ البند ورّثني دعوى من رسالة `96b7f29`: «البوابة تبصم الآن الهندسة **واللون ونصّ
البلاطة**». **الدعوى غير منفَّذة.** الكوميت غيّر ملفَّين فقط (`CountryEvents.tsx` و
`tokens.css`)، وآخر من لمس `verify-scope.mjs` هو `c58b16e` — بصمته هندسة خالصة.

**التفجير قبل أي تعديل** (وهو ما طلبه البند): `--panel: #7a1f1f` تحت `[data-theme="dark"]`،
بناء نظيف، ثم:

| | النتيجة |
|---|---|
| ما تغيّر فعلاً في تبويب «جوّي» | **١٢+ عنصراً مرئياً**: أزرار تكبير الخريطة · `input` · `.dash-secondary` (314×39) · **ثماني بطاقات** (314×195) |
| ما قالته البوابة | **6/6 خضراء · خروج 0** |

⇒ فأُضيف إلى البصمة: الرموز المحسوبة (`--bg`/`--panel`/`--panel-raised`/`--panel-glass`/
`--hair`/`--scrim`) **والمطلي فعلاً** (`backgroundColor`/`backgroundImage`/`borderTopColor`/
`color`) على `body` و`.home-panel.lead` والبلاطة والبطاقة و`.dash-secondary`. ثم أُعيد
التفجير نفسه: **خروج 1، و6 من 6 مرفوضة**.

⚠ **والرقم الذي رفضتُ تجميده: عدّاد البلاطة.** الدعوى الأصلية أرادت التقاط تبديل DASH-4a
(`sev.medium` ← الفرز)، وهو **عدد من تغذية حيّة**؛ تجميده هو بالضبط «بوابة تصرخ ذئباً» التي
يرفضها رأس الملفّ. فجُمِّدت **التسميات** وبنية `.n/.l`، و**التبديل عُولج بنيوياً** في §2.

## 2. الفصل — لا مكوّن مشترك بعد اليوم

| قبل | بعد |
|---|---|
| اللوحة تُركّب `CountryEvents` (مكوّن التبويبات الثلاثة) وتُطفئ قائمته بـ`hideList` | اللوحة تملك `MonitorControls.tsx`؛ و`CountryEvents` ملفُّ التبويبات الثلاثة **بلا أي شرط** |

⛔ **والخاصيّة (`prop`) كانت النصف الأضعف، وDASH-6 هو الدليل**: `hideList` منع **القائمة** من
التسرّب، ثم تسرّب **الثيم كلّه** من فوقه في `96b7f29`. مكوّنٌ مشترك يفاوض على كل قرار؛ ومكوّنٌ
مملوك لا يفاوض على شيء.

⚠ **وحُذف زرٌّ ميّت لا نُقِل**: `CountryEvents` يرسم «عرض الكل (n)» يقلّب `showAll`؛ ومع
`hideList` كان يتحكّم بقائمة غير مرسومة. مقيساً على اللوحة الحيّة قبل البند: النقر غيّر عدد
البطاقات **من 0 إلى 0** وأعاد تسمية نفسه «أعلى 10 فقط» — زرٌّ يُبلِغ أنه فعل شيئاً ولم يفعل.
العنوان بقي (يسمّي `EntityRows`)، والزرّ لا.

## 3. سطح الهبوط — والفرق الذي منع نسخ القاعدة كما هي

DASH-4b قال «كل سطح يأخذ العرض كاملاً» ثم أطلق `map` و`focus` فقط، وترك `entity` — **الوضع
الافتراضي** — يمنح ٤٠٪ للخريطة. فكان التغيير غائباً حيث يُنظر أوّلاً.

| المقياس | 1440 قبل → بعد | 1900 قبل → بعد |
|---|---|---|
| عمود المحتوى | 804 → **1392** (+73٪) | 920 → **1852** (+101٪) |
| مسار الحارة | 636 → **1224** (+92٪) | 752 → **1684** (+124٪) |
| مقعد الخريطة | 576 → **0** | 760 → **0** |
| صفّ البحث | 804 → **920** (مسقوف) | 920 → **920** |

⛔ **ونسخ `.shell.focus-surface` حرفياً كان سيُرجِع عطب UI-1.** سطح `focus` آمنٌ مع
`minmax(0,1fr)` لأن `.ed-claims` يسقف نفسه بـ78ch؛ أوسعُ طفلٍ في سطح الهبوط هو **حقل البحث**،
و`--surface-max` موجود لأن العالم بلا مقعد قاس «حقل بحث 1,864px وجدولاً 1,862px» (تاريخ الرمز
في `tokens.css`). ⇒ العمود يُطلَق، و`.search-row` يحتفظ بـ`--surface-max`. الصفوف تأخذ العرض،
وصندوق النصّ لا.

## 4. الأرضية المحايدة — مقايضة معلَنة لا مكسب صافٍ

قيم THEME-3 **مُعادة الاستعمال حرفياً** من `4dede99` (لا مُعاد اشتقاقها)، تحت
`[data-theme="dark"] .shell.monitor` فقط.

⚠ **أرضيّتان في تطبيق واحد، وهذا ثمنٌ يُدفع لا فائدة تُجنى.** بحث التجربة يُجمع على أن أشدّ
الأضرار هو **التناقض داخل المنتج الواحد**، وهذا البند يقبله مقابل إزالة درزة الخريطة
(1.23:1 → 1.01:1) على السطح الذي تكون الخريطة فيه أكبر وأكثر نظراً. ولو أراد المالك أرضية
واحدة فالحلّ **حذف الكتلة**، لا توسيعها.

⛔ **وانحرافٌ واحد مقصود عن نصّ البند**، وهو يستحقّ التسمية: البند يقول «لا تعرّف رمزاً للوحة
تحت `[data-theme]` أبداً». الأداة مشروطة بالثيم **ومقيَّدة بالسطح**: `data-theme` يُثبَّت على
`<html>` قبل أوّل رسم **ويمكن أن يكون `light`**، و`.shell.monitor` وحده (0,2,0) يتغلّب على
`[data-theme="light"]` (0,1,0) — فكان سيُجبر التبويب على الظلمة عند قارئٍ نهاريّ، وهو عطبٌ
أسوأ من الذي نتجنّبه. مقيس في الثيمين: **ظلام/لوحة `#0b0b0a` · ظلام/جوّي `#002623` ·
نهار/لوحة و نهار/جوّي `#f4f1ec`** — أي أن المقايضة قائمة في الظلام وحده.

## 5. القبول
`build` + البوابات الستّ **خضراء**، و`verify:scope` **6/6 بلا `--bless`** — والبصمة تحمل
الأرضية الآن، فالخضرة نتيجة لا صمت. `pytest` بلا تغيير.

⚠ **بوسة واحدة (`--bless`) جرت، وسببها ليس تغيّر التبويبات**: البصمة اكتسبت حقلَين
(`ink`/`tileText`) لم يكونا في الأساس. أُثبت برمجياً قبل البوسة أن **صفر** حقلٍ قائم تغيّر
وأن المضاف حقلان فقط.
