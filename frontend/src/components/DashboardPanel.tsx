/**
 * DashboardPanel — the monitoring workspace's sidebar (D1).
 *
 * This is the growth-containment decision from MONITOR-pre Q1: the dashboard's
 * regions live HERE, not inlined in App.tsx's sidebar ternary (App is already the
 * ~1000-line integration point). D1 hosts ONLY the re-hosted E1–E3 search region;
 * the D2 (layer-control + per-domain freshness strip) and D5 (alerts) regions are
 * reserved below as slots so those phases fill them without restructuring.
 *
 * State ownership (MONITOR-pre Q2, option a): App still owns all search state —
 * it remains the MapView-prop owner, matching the existing idiom where sidebar
 * components receive state + onSelect callbacks. This panel is presentational: it
 * receives props and renders exactly what the old `isSearch` branch rendered. The
 * per-entity components moved here VERBATIM (they never knew their mount point).
 * Since then the redesign replaced the list side with the shared `ResultCards` grid
 * and moved the detail side onto the summoned map (`MonitorDetail`); the orphaned
 * `*SearchList` components were deleted in UI-3.
 *
 * DASH-2c′ — this panel is now the SEARCH SURFACE ONLY. The resting dashboard used to
 * live in the results slot below (a ternary on an empty query); it is a separate
 * destination now and this file no longer imports `DashboardHome` or carries its props.
 * The results area is therefore unconditional: reaching this panel means the analyst asked
 * to search.
 *
 * ⚠ That destination has since moved twice: 2d″ gave it a NavBar tab, and DASH-2g′ removed
 * the tab entirely — its country card is now pinned over the map on the three domain tabs
 * (`DomainOverlay`, formerly `HomePanel`). Nothing here depends on it either way.
 *
 * ⛔ DASH-IDLE — THE PARAGRAPH ABOVE IS HISTORY AGAIN. Measured at 1440×900 before this
 * item, the empty-query branch rendered NULL below the search field and left ~836×~700px
 * of blank workspace — the only tab whose default state displayed nothing. The owner
 * reversed that decision: the idle state answers «ماذا يحدث الآن؟» instead, fed by the same
 * App-owned alerts fetch — recorded here so nobody "restores" the null thinking it a bug.
 *
 * ⚠ DASH-6a — WHAT IT MOUNTS CHANGED, THE DECISION DID NOT. It mounted `CountryEvents`, the
 * domain tabs' own overlay card, with a `hideList` prop. DASH-6a moved it to a
 * `MonitorControls` this tab owned; DASH-7 dissolved that too — the console owns the chrome
 * and the stage owns the content, so the sharing that leaked twice (`8b6b42c`, `96b7f29`)
 * has no component left to travel through.
 */

import type { ReactNode } from "react";
import type { Alert, AlertsResponse, AoiCountry } from "../api/types";
import MonitorConsole from "./MonitorConsole";
import type { Surface } from "./MonitorConsole";
import CaseFile from "./CaseFile";
import EntityDetail from "./EntityDetail";
import EntityRows from "./EntityRows";
import DashViewToggle from "./DashViewToggle";
import type { SearchEntity } from "./SearchPanel";
import type {
  AircraftSummary, AirportSummary, SatelliteSummary, VesselSummary,
} from "../api/types";
import AreaPanel from "./AreaPanel";
import type { AreaState } from "./AreaPanel";
import ResultCards from "./ResultCards";
import type { CardBadge, CardItem } from "./ResultCards";
import TimeWindowPicker from "./TimeWindowPicker";
import { T } from "../lib/strings";

/**
 * Which surface the monitor workspace is showing: entity search or area search.
 *
 * DASH-2c′ briefly carried a third member, `"home"`, to reach the landing surface before
 * it had a NavBar tab; 2d″ gave it one, so the member is gone. DASH-2g′ then removed the
 * home View as well.
 *
 * ⛔ `"detail"` (DASH-2g′ commit 2) is NOT that member coming back. It is where the three
 * domains' own detail now lives, after the owner moved it off the map tabs: the entity
 * lists, the aircraft filters and the four inventory tiles. The distinction that matters —
 * `entity`/`area` ASK a question (a query, a drawn box), `detail` BROWSES what is already
 * streaming. That is why it carries no query input of its own; the search field it inherited
 * is the aircraft FILTER bar, which narrows a live list rather than issuing a lookup.
 */
/**
 * ⛔ DASH-7 — FIVE SURFACES BECAME THREE, by the owner's ruling «التصميم هو الحكم» on the
 * fourth proposal. The switcher shows `mon` · `ent` · `map` and nothing else:
 *   · `entity` + `detail` → `mon`   both BROWSE what is already streaming
 *   · `area`             → `map`    the box is drawn ON the map, where "where" is asked
 *   · `focus`            → `ent`    the entity, whole, at 78ch
 * ⚠ `case` and `detail` SURVIVE AS STAGES, not as switcher buttons, because deleting them
 * would delete shipped features the proposal simply does not draw: DASH-5's case file (its
 * door is the 📁 counter, which the mock draws) and DomainDetail (its door is the status
 * line's domain counts, which the mock also draws). Three buttons; five destinations; no
 * feature loses its door and no surface is invented.
 */
export type PanelMode = "mon" | "ent" | "map" | "case" | "detail";

/**
 * ⛔ DASH-4b — `"map"` IS A SURFACE, NOT A FIFTH QUESTION, and adding it here is the whole
 * geometric change. Until now the map had a permanent SEAT: `.map-host:has(~ .workspace)`
 * gave it `--map-seat` of the width on every monitor surface, and `.workspace` set
 * `inset-inline-end` to leave room for it. Both regions were therefore always too small.
 *
 * ⛔ AND THE MEASUREMENT THAT KILLED THE SEAT IS ONE THIS PROJECT ALREADY HELD. Cockburn,
 * Karlson & Bederson (ACM Computing Surveys 41(1), 2008) report Baudisch et al.: a
 * focus+context screen was faster by up to 56 % than overview+detail, and in a DYNAMIC task
 * the split's attention cost made performance significantly worse. Continuous monitoring is
 * a dynamic activity. Three successive dashboard proposals split the screen anyway.
 * ⚠ Measured cost of the seat on the content side: the analysis column was 300px, so
 * `why`/`caveat` fell to ~30 characters a line and a 24-cell pattern lane rendered at 7px a
 * cell — under the 24px Heer, Kong & Agrawala (CHI 2009) measured as optimal, in the band
 * where they found estimation "times decrease as charts get smaller, though accuracy also
 * decreases". Smaller felt faster while being wronger.
 *
 * ⚠ WHAT THIS IS NOT: it is not the old overlay-reveal returning. That hid the workspace to
 * expose a map nobody had asked for. This is an explicit destination the analyst selects,
 * and the surface they leave keeps its state.
 */
/* ⛔ DASH-7 — `PANEL_MODES`, `MODE_LABEL` and `ModeSeg` ARE GONE, not moved. The surface
 * switcher is `MonitorConsole`'s, on the command line, where the fourth proposal puts it —
 * beside the query and the scope pills rather than on a row of its own. A second switcher
 * here would be the "one surface, two hierarchies" drift DASH-NAV removed once already. */

// the airport window's ceiling (backend MAX_WINDOW_HOURS = 168 = 7d, also the
// /history hard cap) — the unified max for the custom input.
const WINDOW_MAX_HOURS = 168;

// --- per-entity summary → card mapping (presentation only; engines untouched) ---
function vesselCards(items: VesselSummary[]): CardItem[] {
  return items.map((v) => {
    const badges: CardBadge[] = [];
    if (v.ship_type) badges.push({ label: T.vessel.types[v.ship_type] ?? v.ship_type });
    if (v.n_pattern_types > 0) badges.push({ label: `${v.n_pattern_types} ${T.entitySearch.colPatterns}`, tone: "high" });
    if (v.last_position_quality && v.last_position_quality !== "plausible")
      badges.push({ label: T.entitySearch.quality[v.last_position_quality] ?? v.last_position_quality, tone: "warn" });
    // IMO-2: a questionable IMO spine — a caveat on the lead (never hides it).
    if (v.imo_quality && v.imo_quality !== "valid")
      badges.push({ label: T.spine[v.imo_quality], tone: v.imo_quality === "contested" ? "high" : "warn" });
    return { id: v.mmsi, title: v.name || `#${v.mmsi}`, sub: v.flag_country || "—", badges };
  });
}
function aircraftCards(items: AircraftSummary[]): CardItem[] {
  return items.map((a) => {
    const badges: CardBadge[] = [
      { label: a.on_ground ? T.entitySearch.air.onGround : T.entitySearch.air.airborne },
    ];
    if (a.country) badges.push({ label: a.country, tone: "info" });
    else if (a.near_country) badges.push({ label: a.near_country });
    if (a.position_quality === "suspect") badges.push({ label: T.strip.quality.suspect ?? "suspect", tone: "warn" });
    return {
      id: a.icao24,
      title: (a.callsign && a.callsign.trim()) || a.icao24,
      sub: a.aircraft_type || a.operator || a.registration || "—",
      badges,
    };
  });
}
function satelliteCards(items: SatelliteSummary[]): CardItem[] {
  return items.map((s) => {
    const badges: CardBadge[] = [];
    if (s.object_type_label) badges.push({ label: s.object_type_label });
    if (s.users) badges.push({ label: s.users, tone: /military/i.test(s.users) ? "warn" : undefined });
    if (s.is_geostationary) badges.push({ label: "GEO" });
    return { id: String(s.norad_id), title: s.object_name || `#${s.norad_id}`, sub: s.orbit_class || "—", badges };
  });
}
function airportCards(items: AirportSummary[]): CardItem[] {
  return items.map((ap) => {
    const badges: CardBadge[] = [];
    if (ap.iata) badges.push({ label: ap.iata });
    if (ap.type) badges.push({ label: ap.type });
    return {
      id: ap.icao,
      title: ap.name || ap.icao,
      sub: [ap.municipality, ap.country].filter(Boolean).join(" · ") || ap.icao,
      badges,
    };
  });
}

interface Props {
  // --- multi-entity search control (SearchPanel) ---
  entity: SearchEntity;
  onEntity: (e: SearchEntity) => void;
  query: string;
  onQuery: (q: string) => void;
  loading: boolean;
  count: number | null;
  // --- selection, shared across entities (owned by App). ⚠ CORRECTED by UI-3: this said opening
  //     a card SUMMONS the map and that the detail RIDES ON it. Neither is true since UI-2 — the
  //     map has a bounded seat that is always present, so opening a card only selects, and
  //     `MonitorDetail` floats beside the seat over this workspace rather than on the map. What
  //     still holds: this workspace holds the search and the result cards, not the detail. ---
  selected: string | null;
  onSelect: (id: string | null) => void;
  // --- per-entity result summaries (mapped to cards) ---
  vesselResults: VesselSummary[];
  aircraftResults: AircraftSummary[];
  satelliteResults: SatelliteSummary[];
  airportResults: AirportSummary[];
  // --- D4 entity ⇄ area mode; DASH-2g′ added `detail` (the domains' own surface) ---
  mode: PanelMode;
  onMode: (m: PanelMode) => void;
  area: AreaState;
  /** DASH-2g′ — the domain detail, composed by App (it owns every feed this reads). Rendered
   *  ONLY in `detail` mode, as an explicit positive test for the same reason SearchPanel is:
   *  a future fourth mode inherits no surface it was not given deliberately. */
  detail: ReactNode;
  /** DASH-7 — what the query field routed itself to, in Arabic; null = a name search. */
  routedTo: string | null;
  /** DASH-7 — how many items the shared case file holds, for the 📁 counter. */
  caseCount: number;
  /** DASH-7 — which domain تفصيلي should browse, set by the status line's counts. */
  onDetailDomain?: (d: "air" | "sea" | "space") => void;
  /** ⛔ DASH-6b — the freshness bar, composed by App, rendered IN FLOW on the `entity`
   *  surface only. That surface released the map's seat, and `SeatFreshness` is
   *  `position: absolute` inside the seat's column — the opaque workspace covered it and G9
   *  caught the occluded ink at both gate widths. Hiding it was refused: the air LIVE verdict
   *  is on no other part of this surface. Null on every other mode, where the absolutely
   *  positioned mount in App still applies — exactly one of the two ever renders. */
  freshness?: ReactNode;
  /** DASH-4b-3 — the alert the `focus` surface shows. Null renders an honest empty state
   *  rather than an invented one. */
  focusAlert?: Alert | null;
  /** Refetch after a triage write — the state is collective, so the caller reloads. */
  onTriaged?: () => void;
  // --- DASH-IDLE: the country card's live feed, shown in the idle state below. App owns
  //     the fetch and the country/window state (the SAME instances the three domain tabs'
  //     overlay reads), so the monitor's idle answers from one source of truth. ---
  alertsData: AlertsResponse | null;
  alertsLoading: boolean;
  alertCountry: AoiCountry;
  onAlertCountry: (c: AoiCountry) => void;
  alertWindowH: number;
  onAlertWindow: (h: number) => void;
  /** Opens the clicked alert's entity payload. On the monitor this floats over the map
   *  seat (see `.workspace ~ .home-panel.entity` in app.css); on the domain tabs the same
   *  callback drives DomainOverlay's panel. */
  onOpenAlert: (a: Alert) => void;
  /** DASH-NAV — the surface's own رصد/إحصاءات sub-mode, moved here from the navbar.
   *  Rendered on the head row beside the mode toggle; StatsView renders its own instance. */
  dashView: "monitor" | "stats";
  onDashView: (v: "monitor" | "stats") => void;
  // --- ENHANCE the shared entity-search time window (hours). Wired to airport `hours`
  //     and the vessel/aircraft track span; disabled for satellite (no stored fixes). ---
  windowH: number;
  onWindow: (h: number) => void;
  /* ⛔ `hidden` and `onOpen` DELETED by UI-3. They were the overlay-reveal seam: `hidden` painted
   * `.workspace.hidden` so the panel could disappear and expose the map, and `onOpen` let App
   * summon that map on a card-open. UI-2 gave the map a bounded seat, deleted `mapSummoned` /
   * `summonMap` / `dismissMap` and the `.workspace.hidden` rule, and stopped passing either prop —
   * so `hidden` was permanently `undefined` and its branch unreachable, and `onOpen ?? onSelect`
   * always took the fallback. ⛔ Removed rather than marked: UI-3's own history is 309 orphaned
   * lines and a `pendingAlertOpenRef` nobody could reach, both left behind by "keep it for now". */
}

/* ⛔ DASH-7 — `ENTITIES` IS GONE WITH THE SEGMENT IT FED. The four-entity selector was how the
 * analyst told the surface what they were searching; `routeQuery` reads it off the identifier
 * instead, so a control that only ever restated the shape of the string is one control fewer
 * on the command line. The type `SearchEntity` stays — App still owns which endpoint answers. */

export default function DashboardPanel(props: Props) {
  const {
    entity, query, onQuery,
    selected, onSelect,
    vesselResults, aircraftResults, satelliteResults, airportResults,
    mode, onMode, area, detail, freshness, routedTo, caseCount, onDetailDomain, focusAlert, onTriaged,
    windowH, onWindow,
    alertsData, alertCountry, onAlertCountry, alertWindowH, onAlertWindow,
    onOpenAlert,
    dashView, onDashView,
  } = props;
  // the shared window means something different per entity — its note says which:
  const windowNote =
    entity === "airport" ? T.timeWindow.airportNote(WINDOW_MAX_HOURS)
    : entity === "satellite" ? T.timeWindow.satelliteNote
    : T.timeWindow.trackNote;
  // the state machine's discriminant: an EMPTY query (in entity mode) is the
  // resting state → the dashboard; a non-empty query is the search-results state.
  const searching = query.trim() !== "";
  const cards =
    entity === "vessel" ? vesselCards(vesselResults)
    : entity === "aircraft" ? aircraftCards(aircraftResults)
    : entity === "satellite" ? satelliteCards(satelliteResults)
    : airportCards(airportResults);

  return (
    <div className="workspace">
      {/* ⛔ UI-2d — THE `.brand` BLOCK IS DELETED, not hidden and not tagged. It printed the project
          name a second time and `T.entitySearch.section` under it, while `NavBar` — which renders on
          every view, this one included — already carries the name. ⚠ It was justified once: the
          monitor was a surface with no bar above it, and the block was how the analyst knew where
          they were. `NavBar` took that job and the block outlived its reason.
          ⛔ Its sub-line was ALSO a duplicate of a different kind: `T.entitySearch.section` is the
          same string `SearchPanel` renders as its own `.section-label` eight lines below, so
          «بحث ومراقبة» appeared twice on this surface. The string stays live — SearchPanel is its
          real user; only the second printing goes. */}

      {/* ⛔ DASH-7 — ONE COMMAND LINE, and it replaces four scattered controls. The mode row,
          the search block, the seven-chip country row and the window row were four starting
          lines on one surface; the fourth proposal puts the query, the scope, the surface
          switcher and the case counter on a single line, and the counts on a single status
          line under it. `DashViewToggle` (رصد/إحصاءات) stays beside them — it is the app-level
          switch DASH-NAV moved into the page, and moving it back out would restore the two
          hierarchies this line exists to collapse. */}
      <MonitorConsole
          lead={<DashViewToggle view={dashView} onSelect={onDashView} />}
          query={query}
          onQuery={onQuery}
          routedTo={routedTo}
          surface={mode === "ent" ? "ent" : mode === "map" ? "map" : "mon"}
          onSurface={(sf: Surface) => onMode(sf)}
          country={alertCountry}
          onCountry={onAlertCountry}
          windowH={alertWindowH}
          onWindow={onAlertWindow}
          caseCount={caseCount}
          onOpenCase={() => onMode(mode === "case" ? "mon" : "case")}
          caseOpen={mode === "case"}
          onOpenDomain={(d) => { onDetailDomain?.(d); onMode("detail"); }}
          data={alertsData}
          freshness={freshness}
        />

      {/* ⛔ THE STAGE — `flex: 1`, one surface at a time, never a permanent split. Every
          branch below fills it whole; none of them reserves width for another region. */}
      <div className="mon-stage">
      {mode === "mon" ? (
        /* ⛔ DASH-7 — THE MONITORING SURFACE IS THE ROWS, AND NOTHING ELSE. `SearchPanel` and
         * `MonitorControls` used to render here: an entity segment, a query field, a hint
         * line, seven country chips, a window row and three 150px tiles — six control regions
         * stacked ABOVE the thing the analyst came to read. All six moved onto the command
         * line and the status line, which is the fourth proposal's whole argument: the
         * controls are chrome, the entities are the content, and chrome does not get the top
         * third of a monitoring surface.
         * ⚠ `MonitorControls.tsx` is now unused and is DELETED, not left orphaned — DASH-6a
         * built it to end the sharing with the domain tabs, and that job is done by this file
         * owning its own console instead. `CountryEvents` stays untouched on those tabs. */
        searching ? (
          /* --- the query answered: one grid, the same width as the rows it replaces --- */
          <div className="mon-results">
            <TimeWindowPicker
              value={windowH}
              onChange={onWindow}
              note={windowNote}
              disabled={entity === "satellite"}
              max={WINDOW_MAX_HOURS}
            />
            <ResultCards
              items={cards}
              selected={selected}
              onOpen={onSelect}
              empty={T.entitySearch.empty}
              aria={T.entitySearch.listAria}
            />
          </div>
        ) : (
          /* --- the resting state: one row per entity, one lane per pattern --- */
          <EntityRows alerts={alertsData?.alerts ?? []} onOpen={(a) => onOpenAlert?.(a)} />
        )
      ) : mode === "detail" ? (
        /* ⛔ DASH-7 — `تفصيلي` KEPT ITS CONTENT AND LOST ITS BUTTON. The three domains' own
         * detail — entity lists, aircraft filters, inventory tiles — is the only thing that
         * browses a LIVE stream inside this tab, and DomainDetail has no other mount in the
         * app (verified: one import, one call site). The fourth proposal draws no button for
         * it, so it is reached from the status line's domain counts instead of being deleted
         * to make the switcher's arithmetic come out at three. */
        detail
      ) : mode === "case" ? (
        /* DASH-5 — the shared case file: what has been kept for the report. Reached from the
         * 📁 counter on the command line, which the proposal draws. */
        <CaseFile />
      ) : mode === "ent" ? (
        /* ⛔ DASH-4b-3 / DASH-7 — THE ENTITY GETS THE WHOLE WIDTH, and the fourth proposal
         * keeps that: `why` and `caveat` at ~78ch, the lanes under them, the facts beside.
         * The back control returns to المراقبة, which is now the surface it came from. */
        <EntityDetail alert={focusAlert ?? null} onBack={() => onMode("mon")} onTriaged={onTriaged} />
      ) : mode === "map" ? (
        /* ⛔ DASH-7 — THE MAP SURFACE ABSORBED «بحث منطقة», and renders only its RESULTS here.
         * The single `MapView` under `.main` is the map; `.shell.surf-map` releases it to full
         * bleed and collapses the console, so a second Leaflet instance is never mounted.
         * ⚠ The area panel appears only once a box has been drawn — before that the surface is
         * the map itself, which is the whole point of folding the two together: the question
         * "where" is asked ON the thing that answers it. */
        area.hasBox ? <AreaPanel area={area} /> : null
      ) : null}
      </div>
    </div>
  );
}
