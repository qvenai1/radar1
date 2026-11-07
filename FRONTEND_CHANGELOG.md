# Frontend Changelog

## 2026-09-01 — caseKey: server-published identity first, corrected mirror second

`caseKey` had silently drifted from `notify.identity.dedup_key` on
`maritime_single` (missing `:summary`) — exactly the failure its own ⛔ comment
predicted. Now: `a.dedup_key` from the server (v1 ADDITIVE, drift-free by
construction) wins whenever present; the hand mirror survives only as a
fallback for older servers, corrected byte-for-byte (including Python's
`str(id or "")` null normalisation and the unknown-family `:ts` fallback), and
pinned by `caseKey.test.ts` ↔ `tests/test_case_key_parity.py` shared vectors —
the same anchor discipline as the orbital parity test.

## 2026-09-01 — First test infrastructure: vitest + the orbital-parity anchor

The frontend had ZERO test infrastructure — no runner, no test files — which is
how "the same orbital math in two languages, kept in parity by comment"
(tech-stack review finding 2) could exist. vitest is now wired (`npm test`),
and the first two test files are the two guards that most needed to exist:

- **`lib/orbit.test.ts` — the parity anchor.** satellite.js propagates the SAME
  ISS TLE at the SAME three epochs as
  `tests/test_passes.py::test_subpoint_matches_reference`, against the SAME
  skyfield-verified (lat, lon) values, within the same 0.01°. Both languages are
  now pinned to one independent reference — a stored pass window and a rendered
  ground track can no longer drift apart silently. ⛔ The TLE + reference
  constants must stay byte-identical to the Python test.
- **`lib/escapeHtml.test.ts`** — `esc()` moved out of MapView into
  `lib/escapeHtml.ts` (importable = testable) and its contract is proven:
  scripted vessel names neutralized, all five metacharacters, Arabic/RTL text
  untouched.

## 2026-09-01 — Stored-XSS closed: every data-derived string entering Leaflet HTML is now escaped

Leaflet's `bindPopup`/`bindTooltip` take HTML strings, and the popups interpolated
vessel `name`/`mmsi`/`flag` directly — fields that arrive from **spoofable AIS/GFW
feeds**, so a crafted vessel name executed in the analyst's browser (security review
finding 2, confirmed: zero escaping existed in the file).

- One `esc()` helper in `MapView.tsx` (HTML-entity escape of `& < > " '`), applied at
  every sink: `gnssPopupHtml` (name/mmsi + the `gnss_type` fallback), `zonePopupHtml`
  (variant fallback + window timestamp), the dark-vessel label (name/MMSI/flag), and
  both airport `bindTooltip` sites (gazetteer strings).
- ⛔ The rule going forward: **no data-derived value enters a popup/tooltip template
  without `esc()`** — only `T.*` i18n strings and arithmetic results are exempt.
  MapView is the only file with HTML sinks (`grep innerHTML|bindPopup|bindTooltip`
  over `frontend/src` — no other hits).

The watermark is gone and the basemap is byte-for-byte the cartography it always was. The
`API KEY REQUIRED` billboard is a policy on CARTO's RASTER CDN, not on their maps.

- **The finding that made this possible.** Every CARTO raster path — `basemaps.cartocdn.com`,
  the legacy `cartodb-basemaps-{a,b}.global.ssl.fastly.net`, `tiles.basemaps.cartocdn.com`,
  `dark_nolabels`, `@2x`, and with a Referer from the app's own origin — answers HTTP 200 with
  a defaced PNG. But the **vector** tiles those same styles are rendered from are still open:
  `tiles-{a..d}.basemaps.cartocdn.com/vectortiles/carto.streets/v1/{z}/{x}/{y}.mvt` returns 200
  with real protobuf. So `dark-matter-gl-style` and `voyager-gl-style` — the styles the two
  raster basemaps were rendered FROM — give back the identical map, clean, with no key.
- **Leaflet is still the engine.** `maplibre-gl-leaflet` renders the style into a canvas inside
  Leaflet's own tile pane. Every marker, trail, popup, layerGroup, pane and z-index is
  untouched Leaflet. What the changelog rejected in the past was MapLibre as the map ENGINE.
- **⛔ Matching the raster took measurement, not eyeballing.** CARTO's GL styles and their own
  raster tilesets have drifted apart. Each value below was found by sampling the same pixels of
  the same viewport in both, and is forced back via `STYLE_OVERRIDES`:

  | feature | raster (original) | GL style | how it looked |
  |---|---|---|---|
  | land / background | `#090909` | `#0e0e0e` | whole map slightly lifted |
  | water | `#262626` | `#2C353C` | every sea a pale blue patch |
  | country border | `#2b2c2c` | `#5b5d5d` | ~2x too bright |
  | country label | `#7b7c7c` neutral | `#8a9aa3` | brighter, blue-tinted |
  | internal state borders | near-invisible | `rgba(103,103,114)` | dotted lines filling deserts |

  Verified after: land `#090909` and water `#262626` are **exact**; border and labels within
  one step.
- **⚠ An aggregate measurement lied about this first.** A brightness histogram over a land
  region reported the ORIGINAL as the brighter map — because the watermark is white text and
  dominated the statistic. It was measuring the defacement, not the cartography. Cropping the
  same region from both and looking at it showed the truth at once.
- **⛔ Without WebGL the app did not degrade — it DIED.** Measured with WebGL disabled: the
  MapLibre constructor threw, the exception unwound through React and nothing mounted — no
  `.main`, no `.map-host`, zero markers. A blank radar screen. `hasWebGL()` is now checked
  before construction and the constructor is wrapped, falling back to the raster tiles
  (watermarked, but a working application). The project's own layout gate caught this by
  failing closed with "no .main".
- **⛔ And an ordering bug that failed silently:** `getMaplibreMap()` returns undefined before
  `addTo()`, because the plugin builds its map in `onAdd`. With optional chaining every handler
  became a no-op — the map rendered, the console was clean, and both the failure counter and
  the style overrides were dead.
- **Attribution** — the old string carried only the department line while the tiles were built
  on OpenStreetMap data under ODbL, so the licence condition was unmet. The credit now rides on
  both paths: from the style's own source metadata on the vector path, and explicitly on the
  raster fallback.
- Verified live at `http://10.0.0.119:8000`: with WebGL, only vector + style requests, 583
  markers, pane z-order unchanged; without WebGL, raster fallback and 585 markers with the app
  fully functional. Zero >=400, zero page errors, both themes, zoom/pan/toggle. tsc 0; layout,
  layout-system, dataviz and contracts gates pass.

## 2026-08-31 — REVERTED: the basemap is back on CARTO, watermark and all (owner's call)

The whole map change below was rolled back at the owner's explicit request: they wanted the
original engine and palette, and judged every replacement visibly different from it. The four
map files are restored byte-identical to their pre-change state (`9a0d641^`).

⚠ **THE «API KEY REQUIRED» WATERMARK IS THEREFORE BACK, AND THAT IS THE INTENDED OUTCOME OF
THIS COMMIT, NOT A REGRESSION.** It is inseparable from the original look: measured
2026-08-31, EVERY CARTO endpoint serves the same defaced tile — `basemaps.cartocdn.com`, the
legacy `cartodb-basemaps-{a,b}.global.ssl.fastly.net`, `tiles.basemaps.cartocdn.com`,
`dark_nolabels`, and the `@2x` variants. All answer HTTP 200 with a valid PNG carrying the
watermark. There is no keyless CARTO left to point at.

**What the replacements got wrong, kept as the record of why they were rejected:**
- Esri Canvas Dark Gray is a different cartography — flat mid-grey where dark_all is
  black-with-bright-labels — and its Base ships almost no city labels (they live in a separate
  Reference service). Adding that overlay restored the cities but not the palette.
- OSM's standard tiles are not an option at all: they return HTTP 200 with an
  "Access blocked — App is not following the tile usage policy" image.

**The only route to the original look WITHOUT the watermark is a CARTO API key**, which is
the owner's to create (carto.com/basemaps/apikey — the URL the watermark itself prints). Once
one exists, `TILE_URL` in `lib/theme.ts` takes it as a query parameter and nothing else in
this file's history needs to come back.

⛔ **One finding survives the revert as knowledge, deliberately NOT re-applied here:**
`--tile-filter` has never taken effect. Leaflet 1.9.4 ships `.leaflet-tile { filter: inherit }`
at the same specificity and later in the cascade, so `app.css`'s `.leaflet-tile { filter:
var(--tile-filter) }` always loses — computed `filter` on a live tile is `none`, and every
value in `tokens.css` for it is decorative. It was left exactly as found, because "the
original state" is what was asked for and the map has always rendered unfiltered.

## 2026-08-31 — The basemap said «API KEY REQUIRED»: CARTO now defaces anonymous tiles

The watermark tiled the whole viewport, once per tile, over the radar map.

- **Cause, and it is not an outage.** `{s}.basemaps.cartocdn.com/dark_all` and
  `/rastertiles/voyager` still answer **HTTP 200 with a valid 256x256 PNG** — CARTO simply
  paints `API KEY REQUIRED / carto.com/basemaps/apikey` diagonally into the image. Nothing
  failed, so nothing could be caught: MapView raises `tileWarning` after three `tileerror`
  events, and a 200 carrying a decodable image is a success by every signal Leaflet has.
- **⛔ OSM standard was the same trap, not the escape.** `tile.openstreetmap.org` returned
  **HTTP 200 with an image reading "Access blocked — App is not following the tile usage
  policy of OpenStreetMap's volunteer-run servers"**. Swapping to it would have replaced one
  watermark with another and looked perfect in every status code. Each candidate was
  therefore fetched **and looked at**, not merely status-checked.
- **Fix** — both basemaps move to Esri's Canvas services (no key, no registration, no
  referrer allow-list): `World_Dark_Gray_Base` for night, `World_Light_Gray_Base` for day.
  ⚠ ArcGIS MapServer tiles are `{z}/{y}/{x}`, row-then-column — reversed from the usual
  `{z}/{x}/{y}`; get it wrong and every tile is a valid image of the wrong place, which is
  again not an error anything can catch. z0-z23 per `?f=pjson`, past the app's `maxZoom: 12`.
- **Attribution — a licence condition that was NOT being met.** The old string carried only
  the department logo and name, while the tiles were built on OpenStreetMap data (ODbL). The
  credit now ships beside it, taken verbatim from Esri's own `copyrightText`, `dir="ltr"`
  isolated inside the RTL control.
- **⛔ The swap shipped a visibly PALER map, and that was a regression, not a side effect.**
  Esri Dark Gray is raw `#3e3e40` against CARTO dark_all's `#101010` — **4.8x brighter**, a
  number measured before the swap and then not acted on. Two things were wrong underneath:
  - **`--tile-filter` had NEVER applied.** Leaflet 1.9.4's own sheet carries
    `.leaflet-tile { filter: inherit }` at the same specificity and later in the cascade, so
    `.leaflet-tile { filter: var(--tile-filter) }` always lost — computed `filter` on a live
    tile was `none`. The selector is now `.leaflet-container .leaflet-tile` (0,2,0).
  - **⛔ `.leaflet-tile-container` is NOT the right hook, despite looking like it.** Filtering
    the container makes the filter apply **twice** — the container renders filtered and each
    tile then applies the inherited value to itself. Verified in the page: container-filtered
    leaves BOTH computing the full filter; the tile selector leaves the container at `none`.
    An earlier "it crushes to near-black" reading here was that double application, not the
    filter itself.
- **Dark filter retuned `brightness(.38) contrast(1.06)` → `brightness(.70) contrast(1.20)`,**
  the owner's pick off a ladder rendered in the running page (none / .90-1.10 / .80-1.15 /
  .70-1.20 / .55-1.30). ⚠ The bracket is real in both directions: unfiltered was reported as
  pale, `.55/1.3` as excessively dark, and by `.45` the coastlines and sea/land edges wash out
  entirely. The light filter now binds for the first time too and was checked in the same pass.
- **⛔ And the basemap alone was NOT the same map — the label layer had to come back.** Esri's
  Canvas Base is deliberately minimal ("a neutral background with minimal colors, labels, and
  features" — its own service description) and carries little beyond country and sea names.
  CARTO's `dark_all` was ONE layer that already included cities, roads and boundaries, so the
  swap silently dropped **every city label on the map** — Jerusalem, Gaza, Amman, Aleppo,
  Cairo, Kuwait, Riyadh, Doha, Muscat, Tabuk, Tripoli, Benghazi, Alexandria, Luxor, Aden. The
  owner spotted it as "something is different from the original"; no automated signal saw a
  thing, because 200s, tiles-loaded and console-clean were all still true. `World_*_Reference`
  is now added as a second tile layer at `zIndex: 2` over the basemap's `1`, in the same pane
  so it takes the same `--tile-filter` and stays below every marker and vector. Attribution
  rides on the basemap only — both layers carry the same `copyrightText` and Leaflet would
  print it twice.

- Verified in a real browser at `http://10.0.0.119:8000` (Chromium via Playwright): only
  `services.arcgisonline.com` contacted, **40/40 dark and 24/24 light tiles loaded, zero
  >=400, zero console messages**, 479 aircraft markers still on the map, pane z-order
  unchanged (tile 200 < overlay 400 < marker 600 < tooltip 650 < popup 700), zoom/pan and
  the day/night toggle both working. tsc 0; layout, layout-system, dataviz and contracts
  gates pass.

## 2026-07-26 — Dark-vessels: an on-land «مواقع مشبوهة/مُزوّرة» (suspect/spoofed) sub-layer

The land markers the user flagged were two different things. NON-vessel MMSIs (AtoN /
SAR / base stations) genuinely sit on land — noise, dropped by the backend. But a SHIP
station reporting an on-land position is a spoofed / erroneous AIS fix — a signal. So
instead of hiding it, we surface it as its own class.

- **Map** — an on-land dark event now renders as an ORANGE DASHED ring (`--glyph-darkland`,
  new token in both themes), visually distinct from the violet at-sea dot and from the
  GNSS-crimson / MT-amber layers it can co-render with. Its popup names it «موقع بري —
  إحداثيات مشبوهة/مُزوّرة».
- **`DarkVessels` panel** — an inline note («منها N بموقع على اليابسة …») when any suspect
  event is present, and a «موقع بري» tag + orange dot on each on-land row.
- Honesty preserved: NOT a verdict — a coarse (~1.8 km) land mask can flag a coastal-edge
  berth; stated in the caveat. Identity is never colour-alone (text tags + note).
- Wiring: `on_land` on `DarkVessel`, `suspect` on `DarkVesselsResponse`, `T.dark.*`
  strings. Verified live (screenshot): region 'all' = 42 events, 21 at-sea (violet) +
  21 on-land suspect (orange dashed). tsc 0.

## 2026-07-26 — GFW dark-vessels: an analysis layer in «التحليل»

Surface the GFW satellite-AIS dark-vessel signal (`GET /api/v1/maritime/dark-vessels`)
where it belongs after the redesign — as an ANALYSIS layer, not a source.

- **`DarkVessels`** panel in the maritime «التحليل» group: a «سفن صامتة» layer toggle
  (violet, off by default since GFW is slow + rate-limited — fetch on enable), a region
  selector (الكل / الخليج / الأحمر), and a list of events (name / flag / duration /
  «متعمّد» tag). The honest caveat is always shown: a LEAD, not proof.
- **Map markers** — a distinct violet ring at each gap position (where a vessel went
  silent), brighter/larger when GFW flags it intentional, with a click popup. Violet
  echoes the satellite origin and never co-renders with the satellite tab.
- Wiring: `DarkVessel`/`DarkVesselsResponse` types + `api.maritimeDarkVessels()` +
  App state/effect (per-region fetch, localStorage) + MapView `darkVessels` layer.
  Verified live: 44 real dark vessels across the Gulf / Red Sea / Arabian Sea. tsc 0,
  build green.

## 2026-07-25 — Navigation redesign: organised by DOMAIN, not by data source

The top bar mixed three axes — domain, mode, and SOURCE — so backend feeds leaked
into the UI as toggles (عسكري / ADS-B / GNSS). Reorganised around the ENTITY: the
user picks a domain, never a feed.

- **`NavBar`** — a floating pill bar (matches a reference the user supplied), 4
  DOMAIN tabs only: **لوحة · جوّي · بحري · فضائي** (icon + label, active-pill
  highlight), brand + theme. No source toggles anywhere. `stats` is now a SUB-mode of
  the dashboard (a رصد/إحصاءات switch, لوحة stays active), not a tab; `replay` removed.
- **Air sources MERGED** — OpenSky + military (airplanes.live) + ADS-B Exchange are
  ALWAYS polled (no toggle) and deduped by icao24 into ONE aircraft layer (map + list),
  558 aircraft live. Military keeps its distinct glyph (`--glyph-mil`); the source is a
  **provenance badge** on the flight strip (OpenSky / عسكري / ADS-B), not a control.
  Removed `milOn`/`adsbOn` state + their localStorage.
- **Feeds vs analysis, separated** — GNSS left the nav; the maritime sidebar now has a
  labelled **«التحليل»** group (a GNSS map-layer toggle + the finding panels: composite,
  AIS×MarineTraffic cross-ref, gaps, rendezvous, identity) — clearly distinct from the
  vessel feed (search / list / strip).
- tsc 0, production build green; verified with real screenshots per phase (nav on each
  tab, the 558-aircraft merge + provenance badge, the analysis group). Replay logic
  kept dormant (gated on the now-unreachable `replay` view) for easy restore.

## 2026-07-25 — AIS × MarineTraffic cross-source panel

Surface the derived cross-source view (`GET /api/v1/maritime/crossref`) in the بحري
tab, so the two maritime sources' agreement is visible.

- **`CrossSourcePanel`** — a self-contained VISUAL analysis panel (beside
  HullComposite / Rendezvous / GapCandidates), hand-rolled in the project's chart
  idiom. A headline **% of the MarineTraffic fleet corroborated by AIS**, a
  **coverage proportion bar** (cyan matched vs amber MarineTraffic-only — the two
  source hues, 2px surface gap), a **confidence bar** (strong/probable/weak), and a
  reserved **status callout** (⚠ + label) for dimension discrepancies — never colour
  alone. Below, the linked pairs with a confidence dot + evidence (Δkm · Δh · flag).
  Fetches once on mount; loading / empty / error degrade to a status line; the honest
  "lead, not proof" caveat is always shown. Verified rendered (headless Chrome, 0
  console errors) — integrates with the dark instrument aesthetic.
- Types `CrossMatch`/`CrossrefSummary`/`CrossrefResponse` + `api.maritimeCrossref()`
  + Arabic strings + theme-aware panel CSS. tsc 0, build green.

## 2026-07-25 — MarineTraffic as a second live maritime source on the map

Surface the new independent MarineTraffic source (backend migrations 032/033,
`GET /api/v1/maritime?source=all`) alongside AISStream in the بحري tab — one
merged fleet, each vessel tagged by its source.

- **Identity generalization:** `Vessel` gains `ship_id`/`flag`, `mmsi` relaxed to
  nullable (MarineTraffic is ship_id-keyed, usually no MMSI). A shared
  `vesselKey(v)` (MMSI for AIS, `mt:<ship_id>` for MarineTraffic) now keys the map
  markers, list rows, and selection — the two identity spaces share the layer
  without colliding. Fixed a latent crash: the vessel search filtered on
  `v.mmsi.includes(...)`, which would throw on a null MMSI — now guards both ids.
- **Map:** MarineTraffic vessels render in a distinct **amber** glyph
  (`--glyph-vessel-mt`) vs AIS cyan, so the independent source reads at a glance.
  Clicking a MarineTraffic vessel shows its live snapshot; the MMSI-keyed photo /
  track fetch is skipped (it has no MMSI) rather than firing a doomed request.
- **Detail/list:** `VesselStrip` shows a source-shaped identity cell (MMSI for AIS,
  MarineTraffic id otherwise) + the reported flag; an **MT** chip marks the source
  in both the strip and `VesselList`. `MaritimeSidebar` shows the live per-source
  split (AISStream N · MarineTraffic M).
- **Poll:** the بحري tab now requests `source=all`. Client `api.maritime(source?)`.
- Type-checks clean (tsc 0), production build green. Legal caveat unchanged
  (MarineTraffic ToS prohibit scraping; the source is always tagged).

## 2026-07-18 — reentry watch in the satellite tab (S2c)

Surface predicted reentries whose nominal impact point falls INSIDE/NEAR the
seven-country region (backend S2c, `GET /api/v1/reentry`).

- `ReentryWatch` — a compact panel in the أقمار sidebar listing AOI-relevant
  predicted reentries: object (SATCAT name, else NORAD), predicted UTC time with
  the ± window, and INSIDE/NEAR + country. Fetched (`region=aoi`) when the tab
  opens. Shows "no reentries" when empty.
- Honesty first: an explicit caveat line — the predicted point is a single
  nominal point with large uncertainty (SGP4/TIP), for alerting not precision.
- Wiring only: `ReentryEvent`/`ReentryResponse` types + `api.reentry()` client
  method + App state/effect + Arabic strings. No map markers yet — a fuller UI
  (impact markers with an uncertainty arc, decay countdown, per-object prediction
  history) is future work; the append-only table already stores the history.

## 2026-07-18 — satellite identity in the strip (S1 SATCAT)

Surface the new SATCAT identity (backend S1, `GET /api/v1/satellite/{norad_id}`)
in the أقمار tab, mirroring how the FlightStrip shows aircraft enrichment.

- `SatelliteStrip` now shows, below the live SGP4 physics, an identity row —
  country/organization (decoded `owner_name`, else raw code), object type
  (payload/rocket body/debris), launch date + site, international designator, and
  status (decay date if reentered, else ops status) — plus a `SATCAT · <snapshot>`
  provenance line. Empty facts are dropped, not shown as blanks (project-wide
  "nulls, not zeros").
- On selection the tab fetches enrichment once (`api.satellite(norad)`), with a
  latest-wins sequence guard and cleared on deselect — the same on-select pattern
  as vessel photos. Identity is static + server-cached, so no polling.
- Wiring only: `SatelliteEnrichment` type + `api.satellite()` client method +
  `App` state/effect + Arabic strings. The list stays on the frozen `/satellites`
  contract; identity shows on selection. No new UI paradigm.

### Decisions (one per line)

- Framework: Vite 7 + React 19 + TypeScript strict; no state library — React context/hooks carry the app's small state surface.
- Map: **Leaflet** over MapLibre GL — raster tiles are the one permitted external call and fail tile-by-tile; MapLibre would add a second hard external dependency (vector style/glyphs/sprites) and needs GPU headroom the target hardware may not have; divIcon HTML glyphs match the instrument aesthetic.
- API layer: one module owns wire types (`src/api/types.ts`, derived verbatim from the frozen contract) and one owns transport (`src/api/client.ts`); components never call `fetch`.
- Transport: `LiveFeed` class — WS first, REST `/live` polling at 30 s whenever WS is down, exponential backoff (1→30 s) reconnect, replace-wholesale on every message; replay pauses propagation but keeps the socket drained.
- Units: all metric→display conversion in `src/lib/format.ts` (M_TO_FT, MS_TO_KT, fpm); nothing else owns constants.
- Charts: hand-rolled SVG (stacked bars + line) — no chart library; time-linear x-axis so the API's non-zero-filled buckets show as honest gaps; altitude line breaks across missing buckets instead of bridging.
- Serving: FastAPI mounts `frontend/dist` via StaticFiles(html=True) after the API routers; `/api/latest` alias and the embedded HTML UI were removed; a 503 hint page appears if dist is missing.
- Playback: replay scrubber loads last-24 h `/snapshots` (limit 5000), fetches `/replay?at=` per scrub with a latest-wins sequence guard; playback advances every 700 ms.

### Token plan (dual theme)

- Dark "SCOPE" (ATC radar): bg `#0a0e0f`, panel `#10171a`, ink `#cfe8dd`, dim `#6a8079`, accent phosphor `#3dfc9a`, alert amber `#ffb627`, selection white; all-mono type (JetBrains Mono stack).
- Light "CHART" (aeronautical paper — a redesign, not an inversion): paper `#f2efe6`, panel `#faf8f2`, ink `#22303a`, dim `#66717c`, airway blue `#175fae`, terrain brown `#995f16`, VFR magenta `#b03a72` for selection; sans UI type with mono reserved for numeric readouts.
- Chart series validated with the dataviz six-checks script: dark `#1fa365`/`#b3831f` on `#10171a` — all PASS (worst CVD ΔE 30.3); light `#175fae`/`#995f16` on `#faf8f2` — all PASS (ΔE 93.4); the phosphor accent is deliberately NOT a chart series (outside the dark lightness band).
- Map tiles swap with theme (CARTO dark_all ↔ voyager) with a per-theme CSS filter; theme persisted in localStorage, default from prefers-color-scheme, applied pre-paint in index.html.
- Signature element: the **flight-progress strip** detail panel (segmented cells, colored edge bar) — electronic strip in dark, paper strip in light; everything else stays quiet.

### Map engine

- Interpolation: positions lerp from displayed→target over the measured push interval (EMA, clamped ≤30 s); rotation eases via shortest arc; `prefers-reduced-motion` ⇒ snap; replay ⇒ snap.
- Altitude encoding: glyph size 11→20 px + opacity 0.6→1 over 0–12 km baro; null altitude = mid size, full opacity.
- Trails: capped ring of 40 fixes per aircraft, dim history + bright 8-fix tail; pruned with the aircraft on wholesale replace; selected contact gets selection color, heavier tail, 5 NM range ring, and a dashed `/history` overlay (last hour).
- Tile failure: error counter → visible notice; glyphs render on canvas independent of tiles; total Leaflet init failure → static fallback panel, stats/list keep working.

### 2026-07-08 later — Middle East region switch

- Collector re-pointed to the Middle East box (12→42 N, 24→63.5 E, ~1185 deg² = 4 OpenSky credits/request) at 100 s cadence ≈ 3456 credits/day within the 4000/day budget; no schema/API/frontend contract changes — the bbox is a runtime parameter.
- Map engine tuned for the larger population (568 aircraft first snapshot): marker DOM updates throttled to 15 Hz, glide duration extended to cover the measured push interval (≤120 s) so motion stays continuous at slow cadences; default map view now centers the Middle East pre-bbox.
- Verified live: 568 markers rendered, rAF steady at ~61 fps, WS push within ~1 s of collection.
- Glyphs upgraded from ▲ to a top-view aircraft silhouette (Material "flight" SVG path, nose north so track rotation is unchanged): still one DOM element per aircraft, fill via `currentColor` so theme tokens apply untouched, glow via `paint-order: stroke` — deliberately NOT `filter: drop-shadow`, which would re-rasterize ~600 elements on every 15 Hz tick. Measured after the swap: 563 SVG aircraft at ~60 fps during live animation.

### 2026-07-08 latest — unified brand theme (from the user's other projects)

- Theme rebuilt on the shared identity extracted from `~/Documents/frontend` and `~/Documents/face1/.../frontend`: dark-navy `#030611`, off-white `#f4f1ec`, muted-gold `#d9ab4a`, teal `#236b69`, burgundy `#7a242f`; Cairo (UI) + Manrope (brand heading) + JetBrains Mono (data) — all bundled via @fontsource, no font CDN.
- Dark "SCOPE" = gold aircraft on deep navy with glass panels (backdrop blur on sidebar/overlays only — never on markers), brand gradient backdrop, 8–18 px radii, deep shadows. Light "CHART" = the same hues on the brand's off-white paper; burgundy carries selection.
- Chart series re-validated (six checks): dark `#ae8a39`/`#17a496` on `#0a0e1a` — all PASS (CVD ΔE 46.6); light `#8f6b1e`/`#008573` on `#fbf9f4` — all PASS (ΔE 44.6). Status colors adopt face1's tokens (live `#22c55e`, polling `#f59e0b`, error `#ef4444`).
- Verified after the swap: 574 aircraft live, 61 fps, zero page errors, both themes screenshotted.

### 2026-07-08 — Arabic UI + RTL

- All UI vocabulary moved to Arabic in one strings module (`src/lib/strings.ts` — the single source of text; components import `T`). Units localized in `format.ts` (قدم/عقدة/قدم-د). Data tokens (ICAO24, callsigns, digits, Z-timestamps, coordinates) stay Latin — they are data, not vocabulary.
- `<html lang="ar" dir="rtl">`; layout mirrors via CSS logical properties (border-inline, inset-inline, border-start/end-radius) with explicit LTR islands for the map, charts (chronological left→right), the replay slider, and every tabular-numeric readout (`direction:ltr; unicode-bidi:isolate`).
- Arabic typography: `letter-spacing: 0` on all Arabic-bearing labels (tracking breaks connected script), and Cairo inserted into the data font stack after JetBrains Mono so Arabic words inside data slots render in Cairo while digits stay mono (previously they fell to DejaVu Mono's disconnected Arabic glyphs).
- Verified both themes: RTL sidebar right, mirrored chrome, connected Arabic everywhere, 61 fps, zero page errors.

### 2026-07-08 — trails redesign (user decision: selected-only)

- Ambient per-aircraft trails REMOVED (they were client-accumulated → lost on refresh, and ~1100 canvas polylines of clutter at Middle East scale). The map now shows clean glyphs only.
- The SELECTED aircraft's trail is drawn from `/api/v1/history` (last hour, server truth): appears in full seconds after selection — including right after a page refresh — keeps growing live while selected (capped at 600 fixes), and disappears on deselect. In replay, the trail is fetched up to the replay instant.
- Trail is altitude-colored (brand ramp: teal low → gold high, per theme tokens), so climb/descent reads along the path; null-altitude segments fall back to the selection color.
- Net perf win: canvas holds ≤600 segments for one aircraft instead of ~1100 ambient polylines redrawn per snapshot.

### 2026-07-08 — selected-aircraft enrichment (photo + route)

- On selection, App fetches `/api/v1/aircraft/{hex}?callsign=` once
  (seq-guarded; server caches). Flight strip gains: real photo with
  photographer attribution linking to Planespotters (terms), a route row
  `STR ✈ SAW` (IATA codes as LTR islands, municipalities beneath, tooltip
  notes it is standing data), and التسجيل/الطراز/المشغّل cells.
- Map draws the standing route as a dashed `--select` line with two labeled
  airport dots (permanent `ap-tip` tooltips), same lifecycle as ring/trail.
- Degradation verified with real cases: registration-callsign private jets
  (no route in adsbdb) and photo-less airframes render telemetry-only strips
  with zero console errors; photo `onerror` hides its block.
- Verified live on PGT972: photo + credit + STR→SAW row + map dots, both
  themes screenshotted, 61 fps with selection active.

### 2026-07-09 — military overlay (airplanes.live)

- New top-bar toggle «عسكري» (persisted) polls `/api/v1/military` every 30 s in the live view; overlay aircraft merge onto the map/list AFTER search filters (unfiltered by design), deduped by hex with OpenSky winning.
- Distinct identity: burgundy `.mil` glyph (lifted to `#e0708c` on navy, `#99304a` on paper — a brand-family hue kept clear of the gold civilian, teal ground, and red emergency), `·ع` list tag, live count in the toggle label.
- Off in replay; degrades to empty when the collector is down. Verified both themes: 5–7 military contacts rendered, tags + counter correct, toggle clears cleanly, zero console errors (also fixed a pre-existing favicon 404 with an inline brand-gold plane data-URI).

### 2026-07-09 — military source unified into archive

- The history trail now routes by source: selecting a military aircraft (in `milSet`) fetches `/history?source=airplanes_live`, so archived military tracks render the same altitude-colored trail as OpenSky aircraft (previously military had no trail — live-only).
- Deep link `?icao=` now resolves against BOTH the OpenSky payload and the military overlay, so a military-only aircraft can be shared/restored by URL.
- Bonus confirmed in the gate: the enrichment endpoint (photo + type) works on military hexes too — a selected police/military airframe shows its real Planespotters photo. Verified with EM415 (Turkish police helicopter).

### 2026-07-11 — maritime AIS overlay on the map

- `GET /api/v1/maritime` (backend, file-based like military) → `Vessel[]`.
  Frontend renders it as a SEPARATE layer, not merged into the icao24-keyed
  aircraft list (vessels are MMSI-keyed, knots-native, ship-shaped): its own
  `بحري` toggle (persisted `scope-sea`, off by default), 30 s poll, off in
  replay. Aircraft selection/history/enrichment machinery is untouched.
- MapView: keyed vessel markers (move/add/prune, no rAF — vessels are slow),
  a top-view hull glyph rotated by heading→course, cyan sea token
  (`--glyph-vessel`, distinct from gold aircraft / burgundy military / teal
  ground), glow via paint-order (no filter). Click → Arabic RTL popup
  (name, MMSI/IMO as LTR islands, speed kt, course, draught, ship type) with
  the standing caveat «AIS أرضي (~40–60 ميلاً) وقابل للتزوير».
- Verified live, both themes: 333 vessels + 402 aircraft on one map, popup
  correct, zero console errors; vessels recolor with the theme via CSS tokens
  (no JS rework on toggle).

### 2026-07-12 — maritime as a dedicated tab + vessel sidebar + photos

- Maritime promoted from an overlay toggle to its own **view tab**
  (`live | بحري | replay | stats`). The old `بحري` overlay toggle is gone;
  the tab is a vessels-only workspace. In the maritime tab the map shows
  vessels only (aircraft/military layers cleared — see MapView fix below);
  `live` stays aircraft-only.
- New maritime sidebar (`MaritimeSidebar`): vessel KPIs (total / moving /
  stationary / distinct types), honest AIS feed status + age, a vessel
  search (client-side over name/MMSI/IMO), `VesselList` (name/type/speed,
  bidirectional selection ↔ map), and `VesselStrip` — the vessel detail
  panel with MMSI/IMO/callsign/type/speed(kt)/course/heading/draught/
  nav-status/last-fix and the standing AIS caveat.
- **Vessel photo** (`GET /api/v1/vessel/{mmsi}`, Wikimedia Commons,
  best-effort, sparse): shown in the strip with photographer + license +
  Commons link (attribution is a term of use). Fetched once per selection
  (seq-guarded), server-cached; degrades silently to no-photo.
- MapView: vessel selection (emphasized glyph + pan-to), click→select
  (drives the sidebar, popup dropped in favor of the richer strip). Fix: a
  new effect clears the aircraft layer when `states` is empty — the
  payload-apply effect early-returns on a null snapshot_time and so couldn't
  prune, which had left stale aircraft on the maritime map.
- Verified live both themes: tab switch cleanly swaps layers (maritime → 340
  vessels/0 aircraft; back to live → 491 aircraft/0 vessels); vessel select →
  sidebar detail + real Commons photo (THEOLOGOS P, CC BY-SA 3.0); zero
  console errors; 55 tests green.

### 2026-07-13 — satellite tab (CelesTrak + SGP4, client-side propagation)

- New view tab `أقمار` (live|بحري|أقمار|replay|stats). Its own sidebar:
  satellite KPIs (count / groups), a **CelesTrak group selector**
  (stations/GPS/Galileo/weather/science/military/geo…), TLE-freshness feed,
  a `SatelliteList` (name/NORAD/inclination, bidirectional ↔ map), and a
  `SatelliteStrip` detail that **self-propagates the selected satellite at
  1 Hz** (altitude/velocity/sub-point live; inclination/period static from
  the TLE; TLE-age + accuracy caveat).
- **Positions computed IN THE BROWSER** via satellite.js (SGP4) — `lib/orbit.ts`
  wraps twoline2satrec/propagate/eciToGeodetic. The backend only serves cached
  TLEs (`/api/v1/satellites`); the map animates with zero server load and no
  rate cap. This is scraplan's one piece of client-side physics, justified
  because a satellite position is deterministic from its elements (unlike an
  observed aircraft/vessel).
- MapView: satellite layer (violet 4-point-star glyph, distinct from gold
  aircraft / cyan vessels), 1 Hz propagation timer, selection emphasis +
  pan-to, click→select, cleared on tab switch (reuses the states-empty clear).
- Verified live both basemaps: 7 groups; 20/20 sampled markers move over
  2.5 s (local propagation working); select → live telemetry (CORAL: alt
  302 km, vel 7.73 km/s, incl 51.6°, period 91 min); **geo group = 572
  satellites at 61 fps**; zero console errors.

### Gate verification (headless Chrome against the real backend)

- Gate 1: SPA at `/` (200), `/api/latest` → 404; aircraft over WS ("live push (websocket)" shown); WebSocket blocked ⇒ app fell back to 30 s polling and rendered 148 aircraft from REST; theme toggle persisted (`localStorage scope-theme=light`); zero console/page errors; screenshots g1-dark/g1-light/g1-polling.
- Gate 2: 10 distinct glyph sizes across 183 aircraft (altitude encoding); 24/40 sampled markers moved between frames 2.5 s apart (interpolation); row-click selection filled the strip + `.sel` glyph; background click deselected; tile CDN blocked ⇒ warning shown, 183 contacts still live.
- Gate 2 memory (three soaks, ~27 min total): GC-forced baseline 4.7→5.8 MB over 8 min of continuous 15 s pushes (≈1 MB, V8-warmup scale) with DOM nodes FLAT (2140→2066); when pushes stop, heap returns to a flat 4.4 MB baseline — no unbounded accumulation tied to pushes; marker/trail pruning verified functionally (aircraft count constant across wholesale replaces).
- Gate 3: search "SWR" → "showing 18 of 162", map glyphs 162→18 (server-side `/search`); glyph click highlighted its list row (bidirectional); replay scrubbed 14:15Z→11:09Z with banner + paused feed, exit returned to live; stats charts rendered from the continuous aggregate in both themes; zero page errors.
