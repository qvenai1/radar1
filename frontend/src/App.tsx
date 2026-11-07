/**
 * App — central state and wiring.
 *
 * Views: LIVE (WS feed on the map), MARITIME, SATELLITES, MONITOR, STATS.
 * The live feed keeps running in the background while another view is open, so
 * returning to live is instant and honest.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LiveFeed, api } from "./api/client";
import type { FeedStatus } from "./api/client";
import { vesselKey } from "./api/types";
import type {
  AircraftEnrichment, GnssEvent, LivePayload, MilStateVector, PhotoInfo,
  SatelliteElement, SatelliteEnrichment, SatelliteGroup, StateVector, Vessel,
  ReentryEvent, ZoneCell, VesselSummary, VesselTrackPoint, AircraftSummary, SatelliteSummary,
  AirportSummary, LandingCandidate, AreaSearchResponse,
  Alert, AlertsResponse, AoiCountry, DarkVessel, SourceVintage,
} from "./api/types";
import AlertBar, { findEmergencies } from "./components/AlertBar";
import MapView from "./components/MapView";
import MaritimeSidebar from "./components/MaritimeSidebar";
import HullComposite from "./components/HullComposite";
import GapCandidates from "./components/GapCandidates";
import Rendezvous from "./components/Rendezvous";
import IdentityChanges from "./components/IdentityChanges";
import CrossSourcePanel from "./components/CrossSourcePanel";
import DarkVessels from "./components/DarkVessels";
import VesselList from "./components/VesselList";
import VesselStrip from "./components/VesselStrip";
import SatelliteSidebar from "./components/SatelliteSidebar";
import SatelliteList from "./components/SatelliteList";
import ReentryWatch from "./components/ReentryWatch";
import SatelliteStrip from "./components/SatelliteStrip";
import SearchBar, { EMPTY_FILTERS, filtersActive } from "./components/SearchBar";
import type { Filters } from "./components/SearchBar";
import type { SearchEntity } from "./components/SearchPanel";
import DashboardPanel from "./components/DashboardPanel";
import type { PanelMode } from "./components/DashboardPanel";
import DomainOverlay from "./components/DomainOverlay";
import type { EntitySel } from "./components/EntityPayload";
import EntityPayload from "./components/EntityPayload";
import SeatFreshness from "./components/SeatFreshness";
import NavBar from "./components/NavBar";
import MonitorDetail from "./components/MonitorDetail";
import { makeSatRec, groundTrack } from "./lib/orbit";
import DomainDetail from "./components/DomainDetail";
import { routeQuery } from "./components/MonitorConsole";
import type { DetailDomain } from "./components/DomainDetail";
import ContactList from "./components/ContactList";
import FlightStrip from "./components/FlightStrip";
import StatsView from "./components/StatsView";
import { M_TO_FT, ageSeconds as ageText } from "./lib/format";
import { T } from "./lib/strings";
import { currentMapTheme, toggleMapTheme } from "./lib/theme";
import type { Theme } from "./lib/theme";

/**
 * ⚠ Adding a member here is a CORRECTNESS obligation, not a declaration. The sidebar
 * chain below ends `: isMonitor ? null : <Sidebar …>`, so any View without an explicit
 * branch falls through and renders the live-aircraft sidebar — a contact list and strip
 * behind a view that has nothing to do with aircraft. Every member needs, at minimum:
 * a branch in that chain, a `notAir` entry if the map must carry no aircraft, and a
 * shell class if its layout differs.
 *
 * ⛔ `"home"` WAS REMOVED (DASH-2g′). The owner's decision: the landing surface stops
 * being a tab and the country card becomes a fixture of the three DOMAIN tabs instead.
 * Removing a member is the same correctness obligation in reverse — every one of its
 * branches came out with it (the sidebar-chain link, the `notAir` entry, the shell class
 * and the fetch gate), because a leftover `view === "home"` test is now dead code that
 * reads as live intent.
 */
type View = "live" | "maritime" | "satellites" | "monitor" | "stats";

/**
 * DASH-2g′ — the tabs the country card and the entity panel are pinned to: the three
 * DOMAINS. Declared once and used by the fetch gate, the selection-clear effect and the
 * render, so those three can never drift apart — the drift that would show as a card that
 * renders but never fills, or a fetch that runs where nothing displays it.
 *
 * ⛔ `monitor`/`stats` are deliberately absent: the monitor's `.workspace` is an opaque
 * inset:0 curtain, so a floating panel there would be painted over — mounted, invisible,
 * and still fetching. Not a layout preference; a correctness one.
 */
const CARD_VIEWS: View[] = ["live", "maritime", "satellites"];

const HISTORY_WINDOW_S = 3600;
// stable empties so a non-aircraft tab hands MapView no aircraft without
// allocating fresh arrays/sets each render
const EMPTY_STATES: StateVector[] = [];
const EMPTY_KEYS = new Set<string>();
const EMPTY_SATS: SatelliteElement[] = [];
const EMPTY_GNSS: GnssEvent[] = [];
const EMPTY_ZONES: ZoneCell[] = [];
const EMPTY_DARK: DarkVessel[] = [];

export default function App() {
  // Basemap day/night — decoupled from the UI theme (which stays fixed).
  const [mapTheme, setMapTheme] = useState<Theme>(currentMapTheme());
  /* DASH-2g′ — the opening tab stays جوّي, now that الرئيسية is gone and something has to
   * be the landing surface. Deliberate, on a measured property: the aircraft feed is the
   * only UNGATED one. `LiveFeed` runs for every view, whereas vessels are emptied by
   * `view !== "maritime"` and satellites by `view !== "satellites"` (kept that way on
   * purpose — DASH-2-pre-ب measured that widening them pulls ~2,800 rows to show one). So
   * جوّي is the one tab guaranteed to open populated rather than waiting on its first
   * fetch, and the country card now floats over it either way. */
  const [view, setView] = useState<View>("live");
  const [payload, setPayload] = useState<LivePayload | null>(null);
  const [status, setStatus] = useState<FeedStatus>({ transport: "connecting", live: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [matched, setMatched] = useState<Set<string> | null>(null);
  // [lat, lng, baro_altitude|null] — altitude colors the selected trail
  const [historyTrail, setHistoryTrail] = useState<[number, number, number | null][] | null>(null);
  const [enrichment, setEnrichment] = useState<AircraftEnrichment | null>(null);
  const [enrichLoading, setEnrichLoading] = useState(false);
  // military + ADS-B are ALWAYS merged into the air layer (no toggle) — sources
  // are a provenance badge, not a control the user flips.
  const [milStates, setMilStates] = useState<MilStateVector[]>([]);
  const [adsbStates, setAdsbStates] = useState<StateVector[]>([]);
  const [vessels, setVessels] = useState<Vessel[]>([]);
  const [seaAvailable, setSeaAvailable] = useState(false);
  const [seaAge, setSeaAge] = useState<number | null>(null);
  // UI-FRESH — the authoritative per-source freshness (MaritimeResponse.sources) + the
  // redefined worst-case data_vintage. The strip reads sources[], never the scalar.
  const [seaSources, setSeaSources] = useState<SourceVintage[]>([]);
  const [seaVintage, setSeaVintage] = useState<string | null>(null);
  // UI-FRESH — MarineTraffic is now an OPT-IN overlay (off by default), the same pattern
  // as the GNSS-anomaly and dark-vessel layers. The backend already defaults to
  // source=aisstream; the blend came from this client explicitly asking for "all".
  const [mtOn, setMtOn] = useState(() => localStorage.getItem("scope-mt") === "on");
  /* ⛔ "OFF" AND "NEVER CHOSE" ARE DIFFERENT STATES, and the effect below rewrites
     `scope-mt` on every render — so that key alone cannot tell them apart. This second
     marker records an EXPLICIT choice, which is what makes the auto-fallback safe: a
     preference the user actually set is never overridden, while an untouched default may
     fall back to whatever source still has data. */
  const mtExplicit = useRef(localStorage.getItem("scope-mt-explicit") === "1");
  // REDESIGN: the monitor no longer renders ambient layers, so D2's air/sea/space
  // layer toggles + the freshness strip's frozen-sea vintage are gone (moot). The
  // maritime/satellite TABS keep their own ambient rendering + freshness.
  const [selectedVessel, setSelectedVessel] = useState<string | null>(null);
  const [jammingOn, setJammingOn] = useState(() => localStorage.getItem("scope-gnss") === "on");
  const [gnssAnomalies, setGnssAnomalies] = useState<GnssEvent[]>([]);
  // GFW dark-vessels analysis layer (off by default — GFW is slow + rate-limited)
  const [darkOn, setDarkOn] = useState(() => localStorage.getItem("scope-dark") === "on");
  const [darkRegion, setDarkRegion] = useState<string>(
    () => localStorage.getItem("scope-dark-region") || "all",
  );
  const [darkVessels, setDarkVessels] = useState<DarkVessel[]>([]);
  const [darkAvailable, setDarkAvailable] = useState(false);
  const [darkReason, setDarkReason] = useState<string | null>(null);
  // UI-1: the GFW response's caveat — a sibling of the three fields above, from the
  // SAME response App already owns. DarkVessels used to discard it and show only the
  // Arabic constant; it now feeds the shared Caveat control (drift baseline + original).
  const [darkCaveat, setDarkCaveat] = useState<string | null>(null);
  const [darkLoading, setDarkLoading] = useState(false);
  const [zoneVariant, setZoneVariant] = useState<"broadcast" | "affected">(
    () => (localStorage.getItem("scope-gnss-variant") === "affected" ? "affected" : "broadcast"),
  );
  const [zones, setZones] = useState<ZoneCell[]>([]);
  const [vesselQuery, setVesselQuery] = useState("");
  const [vesselPhoto, setVesselPhoto] = useState<PhotoInfo | null>(null);
  const [vesselLicense, setVesselLicense] = useState<string | null>(null);
  const [vesselPhotoLoading, setVesselPhotoLoading] = useState(false);
  const vesselPhotoSeq = useRef(0);
  const [satGroups, setSatGroups] = useState<SatelliteGroup[]>([]);
  const [satGroup, setSatGroup] = useState("stations");
  const [satElements, setSatElements] = useState<SatelliteElement[]>([]);
  const [satEnrichment, setSatEnrichment] = useState<SatelliteEnrichment | null>(null);
  const satEnrichSeq = useRef(0);
  const [reentry, setReentry] = useState<ReentryEvent[]>([]);
  const [satFetchedAt, setSatFetchedAt] = useState<string | null>(null);
  const [selectedSat, setSelectedSat] = useState<number | null>(null);

  /* ---------- search/monitoring tab (E1 — vessel) ---------- */
  const [searchEntity, setSearchEntity] = useState<SearchEntity>("vessel");
  const [searchQuery, setSearchQuery] = useState("");

  /* ⛔ DASH-7 — ONE FIELD, ROUTED BY THE SHAPE OF WHAT IS TYPED. The fourth proposal replaces
   * the entity segment with a single query whose hint reads «يُوجَّه حسب صيغة المُعرّف», so an
   * MMSI, an IMO, a NORAD number, an ICAO24 and an ICAO location indicator each pick their own
   * endpoint. ⚠ It only ever moves the entity when the string IS one of those shapes: a NAME
   * leaves the current entity alone, so the field never silently changes what is being
   * searched under the analyst's hands. */
  const routed = routeQuery(searchQuery);
  useEffect(() => { if (routed) setSearchEntity(routed); }, [routed]);
  const routedTo = routed ? T.console.routed[routed] ?? null : null;

  /* DASH-7 — the 📁 counter on the command line. One number, reloaded when the surface changes
   * or an item is kept, because the case file is COLLECTIVE (DASH-4a) and another analyst's
   * addition must show up here without a page reload. */
  const [caseCount, setCaseCount] = useState(0);
  const reloadCaseCount = useCallback(() => {
    api.caseItems().then((r) => setCaseCount(r.length)).catch(() => { /* the counter is not load-bearing */ });
  }, []);
  const [searchResults, setSearchResults] = useState<VesselSummary[]>([]);
  const [searchCount, setSearchCount] = useState<number | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchSelected, setSearchSelected] = useState<string | null>(null);
  const [searchExpanded, setSearchExpanded] = useState(false);
  const [searchTrack, setSearchTrack] = useState<VesselTrackPoint[] | null>(null);
  const [searchTrackLoading, setSearchTrackLoading] = useState(false);
  const [searchTrackHasMore, setSearchTrackHasMore] = useState(false);
  // E2 aircraft branch — separate result/track state (different summary + track
  // shape than vessel); searchSelected + searchExpanded are shared across entities.
  const [acResults, setAcResults] = useState<AircraftSummary[]>([]);
  const [acTrail, setAcTrail] = useState<[number, number, number | null][] | null>(null);
  const [acTrackLoading, setAcTrackLoading] = useState(false);
  const [acTrackHasMore, setAcTrackHasMore] = useState(false);
  // E3 satellite branch — results + computed ground-track (no stored fixes).
  const [satResults, setSatResults] = useState<SatelliteSummary[]>([]);
  const [satTrack, setSatTrack] = useState<[number, number][] | null>(null);
  // E4 airport branch — one-to-many: airport list → the selected airport's landing
  // candidates → drill a candidate to that aircraft's track (reuses E2 /history).
  const [airportResults, setAirportResults] = useState<AirportSummary[]>([]);
  const [airportSel, setAirportSel] = useState<AirportSummary | null>(null);
  const [landings, setLandings] = useState<LandingCandidate[]>([]);
  const [landingSelected, setLandingSelected] = useState<string | null>(null);
  const [landingTrail, setLandingTrail] = useState<[number, number, number | null][] | null>(null);
  const [landingLoading, setLandingLoading] = useState(false);
  const searchSeq2 = useRef(0);
  const trackSeq = useRef(0);
  const airportSelSeq = useRef(0);
  // ENHANCE — the ONE shared entity-search time window (hours), wired to each
  // endpoint's existing time param: airport passes it as `hours` (landings beyond the
  // default 24h up to MAX_WINDOW_HOURS); vessel/aircraft pass it to their track span;
  // satellite has no stored fixes (the picker is disabled there). Default 24h.
  const [searchWindowH, setSearchWindowH] = useState(24);
  // D4 area-search — a MODE (not a fifth entity): draw a box → multi-domain
  // crossings; drilling a result reuses the per-domain track layers.
  // Which monitor surface is showing: entity search or area search. (DASH-2c′'s
  // transitional third member `"home"` is long gone; DASH-2g′ then removed the home View
  // itself, so this mode has no landing branch to grow back into.)
  const [panelMode, setPanelMode] = useState<PanelMode>("mon");
  /* DASH-2g′ — which domain the لوحة detail surface is showing. Independent of
   * `panelMode`: that picks entity-search / area-search / detail, this picks which
   * domain you browse once in detail. Defaults to air for the same reason the opening
   * tab does — its feed is the ungated one, so the surface opens populated. */
  const [detailDomain, setDetailDomain] = useState<DetailDomain>("air");

  /* ⚠ DASH-2g′ — WHY THESE GATES GREW, and why this is not the widening 2f forbade.
   * The vessel and satellite feeds are fetched only where they are DISPLAYED. Before this
   * item that meant "on their own tab"; commit 2 moved their lists into لوحة's detail
   * surface, so a gate that still said `view === "maritime"` would have left those lists
   * permanently empty — a list that renders and can never fill.
   *
   * The rule being honoured is the one 2f/2f′ measured: never pull a fleet to show ONE
   * entity (that is what the single-entity endpoints are for, and why the left panel
   * fetches by key). Here the surface shows the WHOLE fleet, which is precisely the case
   * the fetch exists to serve. The gates are still closed everywhere the data is not
   * rendered — including the domain tabs' own map, which is unchanged. */
  const seaFeedOn = view === "maritime" || (view === "monitor" && panelMode === "detail" && detailDomain === "sea");
  const spaceFeedOn = view === "satellites" || (view === "monitor" && panelMode === "detail" && detailDomain === "space");

  const [drawMode, setDrawMode] = useState(false);
  const [areaBox, setAreaBox] = useState<[number, number, number, number] | null>(null);
  const [areaWindowH, setAreaWindowH] = useState(2);
  const [areaAir, setAreaAir] = useState(true);
  const [areaSea, setAreaSea] = useState(true);
  const [areaSpace, setAreaSpace] = useState(true);
  const [areaResult, setAreaResult] = useState<AreaSearchResponse | null>(null);
  const [areaLoading, setAreaLoading] = useState(false);
  const [areaSelected, setAreaSelected] = useState<{ domain: "air" | "sea" | "space"; id: string } | null>(null);
  /* ⛔ UI-2 — `mapSummoned` / `summonMap` / `dismissMap` DELETED, not disabled. They were
   * overlay-reveal: the monitor covered the map and hid itself to expose it, which measured as
   * 0 % or 100 % of the width on every surface and never a share in between. The map now has a
   * bounded seat beside the workspace, so there is nothing to summon and nothing to dismiss.
   *
   * ⚠ Deleting them also closes a state no user could reach or leave: `panelMode` could change
   * while the map was summoned (measured), yet the mode buttons live INSIDE the hidden workspace,
   * so `.map-back` was the only exit. That button, its rule and its string are gone too.
   *
   * ⛔ `revealTick` SURVIVES, and it is not vestigial: MapView has no ResizeObserver — its only
   * resize signal is `invalidateKey`, which calls `map.invalidateSize()`. The seat means the map's
   * BOX changes whenever the view enters or leaves the monitor (seated ⇄ full-bleed), so Leaflet
   * still has to be told. The trigger moved from "the map was revealed" to "the view changed",
   * which is now the only thing that resizes it. */
  const [revealTick, setRevealTick] = useState(0);
  useEffect(() => { setDrawMode(false); setRevealTick((t) => t + 1); }, [view]);
  /* ⛔ DASH-6b — `panelMode` RESIZES THE MAP TOO NOW, and it has to. The comment above says
   * the view is "the only thing that resizes it"; that stopped being true when `entity-surface`
   * gave the map `display: none`. Leaflet measures 0×0 while hidden, so returning to الخريطة
   * would paint a map that believes it has no size until something else nudges it.
   * ⚠ Separate effect, not extra deps on the one above: that one also clears `drawMode`, and
   * clearing the area-draw state on every mode change would cancel a box mid-draw. */
  useEffect(() => { setRevealTick((t) => t + 1); }, [panelMode]);
  useEffect(() => { if (view === "monitor") reloadCaseCount(); }, [view, panelMode, reloadCaseCount]);
  // D5 — open an alert card: route to that entity's search-by-id and summon the map.
  // The pending flag makes the debounced search auto-select the first match, so the
  // summoned map focuses the entity (the SAME overlay-reveal the search cards use).
  const [areaAirTrail, setAreaAirTrail] = useState<[number, number, number | null][] | null>(null);
  const [areaSeaTrack, setAreaSeaTrack] = useState<VesselTrackPoint[] | null>(null);
  const [areaSpaceTrack, setAreaSpaceTrack] = useState<[number, number][] | null>(null);
  const [areaSpaceEl, setAreaSpaceEl] = useState<SatelliteElement | null>(null);
  const areaSeq = useRef(0);
  const areaTrackSeq = useRef(0);

  /* DASH-2e — the country card reads its OWN feed: one country, ordered by importance. It
   * is a different query from the monitor's global severity feed, so it gets its own state
   * rather than overloading `alertData`. Default SY: the project's primary AOI and the
   * country every DASH measurement was taken against.
   * DASH-2g′ renamed these off `home*`: the card is no longer the home's, it belongs to
   * all three domain tabs. A name that still said "home" would be the stale-claim trap. */
  const [cardCountry, setCardCountry] = useState<AoiCountry>("SY");
  const [cardWindowH, setCardWindowH] = useState(24);
  const [countryData, setCountryData] = useState<AlertsResponse | null>(null);
  /** A ref and not state: the effect below OWNS the fetch, and exposing it as state would make
   *  every consumer re-render whenever the poller was re-created. */
  const alertsRefetch = useRef<null | (() => void)>(null);
  const [countryLoading, setCountryLoading] = useState(false);
  /* DASH-2f — which entity the LEFT panel is showing. A card click opens the left panel
   * instead of routing away, which is the whole point of the two-panel layout: the analyst
   * stays on the map. EntityPayload owns the fetch, so App holds only the selection.
   * DASH-2g′: this now lives on the three domain tabs, so the `home*` name is gone. */
  const [panelEntity, setPanelEntity] = useState<EntitySel | null>(null);
  /** ⛔ DASH-4b-3 — the alert the `focus` surface renders. Held as the WHOLE alert, not a key:
   *  the feed is stateless and rebuilt on every poll, so a key would have to be re-resolved
   *  against a list that may no longer contain it — and an entity the analyst is reading must
   *  not vanish because a vessel stopped transmitting mid-session. */
  const [focusAlert, setFocusAlert] = useState<Alert | null>(null);
  const openPanelEntity = useCallback((a: Alert) => {
    setFocusAlert(a);
    setPanelEntity({
      kind: a.entity_ref.kind,
      liveKey: a.entity_ref.live_key,
      altKeys: a.entity_ref.alt_keys ?? [],
      label: a.entity_ref.label,
    });
  }, []);

  /** ⛔ THE FOCUSED ALERT, PREFERRING THE LIVE ONE. `focusAlert` is the object captured when
   *  the analyst opened it; without this, a triage write refetched the feed and the entity
   *  surface still showed the state it had at click time — measured: the badge stayed empty
   *  after a successful write.
   *  ⚠ AND THE HELD COPY IS THE FALLBACK, NOT THE SOURCE. The feed is stateless and rebuilt on
   *  every poll, so an entity can leave it mid-session (a vessel stops transmitting). Keying
   *  ONLY off the live list would make the surface the analyst is reading vanish under them;
   *  keying only off the held copy freezes it. Live when present, held when not. */
  const liveFocusAlert = useMemo(() => {
    if (!focusAlert) return null;
    const k = `${focusAlert.signal_type}:${focusAlert.entity_ref.id}`;
    return (countryData?.alerts ?? []).find(
      (x) => `${x.signal_type}:${x.entity_ref.id}` === k) ?? focusAlert;
  }, [focusAlert, countryData]);

  /* The three map layers, readable at CLICK time without making the click handlers depend on
   * them. Refs and not deps: `vessels` and `mapStates` are replaced by a poll every 30 s and
   * every ~15 s, and a handler identity that churned with them would hand MapView a new
   * callback on every payload for no gain — the handler needs the CURRENT layer only at the
   * moment a glyph is clicked. (Same pattern as `milStatesRef` below.) */
  const vesselsRef = useRef<Vessel[]>(vessels);
  vesselsRef.current = vessels;
  const satElementsRef = useRef<SatelliteElement[]>(satElements);
  satElementsRef.current = satElements;
  // ⚠ assigned after its useMemo further down — `mapStates` does not exist yet at this point
  // in the render, and the handler below only ever reads it inside a click.
  const mapStatesRef = useRef<StateVector[]>([]);

  /* ---------- DASH-2g″ — the map opens the SAME panel the country card opens ----------
   *
   * ⛔ ONE PANEL, ONE STATE, ONE FETCH PATH. These write the same `panelEntity` the card
   * writes, and EntityPayload does the rest exactly as it already did. There is deliberately
   * no map-specific fetch and no second selection: two definitions of "the open entity"
   * drift apart, and the one that drifts is always the one nobody is looking at.
   *
   * They also keep calling the map's own `setSelected*`, which is a DIFFERENT fact and keeps
   * its own consequences (the aircraft trail, the pan-to, the `.sel` emphasis). The ring the
   * panel drives is derived separately, below.
   *
   * ⚠ The in-flight cancel this needs was already built: EntityPayload bumps a `seq` on every
   * `sel` change and drops any response that is not the newest, and each call here hands it a
   * NEW object, so a fast second click can never paint the first entity's payload under the
   * second one's name. Verified against the map source, not assumed to carry over.
   *
   * ⚠ A click on empty map calls all three with null (MapView's background handler), which
   * closes the panel. That is the same "nothing is selected" the map already meant. */
  const openMapAircraft = useCallback((icao24: string | null) => {
    setSelected(icao24);
    if (!icao24) { setPanelEntity(null); return; }
    const s = mapStatesRef.current.find((x) => x.icao24 === icao24) ?? null;
    setPanelEntity({
      kind: "aircraft",
      // the live snapshot IS keyed by icao24, so the map key and the payload key coincide
      liveKey: icao24, altKeys: [], label: s?.callsign?.trim() || icao24, mapKey: icao24,
    });
  }, []);
  const openMapVessel = useCallback((key: string | null) => {
    setSelectedVessel(key);
    if (!key) { setPanelEntity(null); return; }
    const v = vesselsRef.current.find((x) => vesselKey(x) === key) ?? null;
    setPanelEntity({
      kind: "vessel",
      /* ⚠ MEASURED, not defensive: the vessel layer is keyed by `vesselKey` (MMSI for AIS,
       * ship_id for MarineTraffic) while the payload is addressed by MMSI alone, and on the
       * live blend 2,616 of 3,106 hulls are MarineTraffic rows with `mmsi: null`. So this is
       * NULL for most of the layer, and honesty state 1 — «لا مفتاح حمولة لهذا الكيان» — is
       * the correct, common answer for a MarineTraffic hull, not a failure.
       * ⛔ The ship_id is NOT substituted into `liveKey` to make the fetch "work": it is not
       * an MMSI, and /maritime/vessel would 404 on it and report «مفتاح غير معروف» — a
       * precise statement about a question we would have made up. */
      liveKey: v?.mmsi ?? null,
      altKeys: [], label: v?.name?.trim() || key, mapKey: key,
    });
  }, []);
  const openMapSat = useCallback((norad: number | null) => {
    setSelectedSat(norad);
    if (norad == null) { setPanelEntity(null); return; }
    const el = satElementsRef.current.find((s) => s.norad_id === norad) ?? null;
    setPanelEntity({
      kind: "satellite",
      liveKey: String(norad), altKeys: [], label: el?.name ?? String(norad), mapKey: String(norad),
    });
  }, []);
  /* ⛔ Closing the panel takes the map's selection with it, for the entity the panel was
   * showing. The mark and the payload are one act of selection from the map's side, and a
   * glyph left emphasised after its payload closed is the stale-claim trap this project
   * keeps shutting — the map would still be pointing at an answer that is no longer on
   * screen. The consequences that ride on the selection (the aircraft trail) go with it,
   * which is correct: they belonged to the selection, not to the map. */
  const closePanelEntity = useCallback(() => {
    if (panelEntity?.kind === "aircraft") setSelected(null);
    else if (panelEntity?.kind === "vessel") setSelectedVessel(null);
    else if (panelEntity?.kind === "satellite") setSelectedSat(null);
    setPanelEntity(null);
  }, [panelEntity]);
  const feedRef = useRef<LiveFeed | null>(null);
  const searchSeq = useRef(0);
  const enrichSeq = useRef(0);
  // deep link: ?icao=… selects the aircraft once it appears in a payload
  const pendingSelectRef = useRef<string | null>(
    new URLSearchParams(window.location.search).get("icao")?.toLowerCase() ?? null,
  );
  // current military hexes; the ref lets the history effect read membership
  // without re-firing on every 30 s military poll
  const milSet = useMemo(() => new Set(milStates.map((s) => s.icao24)), [milStates]);
  const milStatesRef = useRef<Set<string>>(milSet);
  milStatesRef.current = milSet;
  // same pattern for the ADS-B Exchange overlay
  const adsbSet = useMemo(() => new Set(adsbStates.map((s) => s.icao24)), [adsbStates]);
  const adsbStatesRef = useRef<Set<string>>(adsbSet);
  adsbStatesRef.current = adsbSet;

  /* ---------- live feed lifecycle ---------- */
  useEffect(() => {
    const feed = new LiveFeed(
      (p, s) => {
        setPayload(p);
        setStatus(s);
      },
      (s) => setStatus(s),
    );
    feedRef.current = feed;
    feed.start();
    return () => feed.stop();
  }, []);

  /* ---------- military traffic — ALWAYS merged into the air layer ----------
   * No longer a toggle: an aircraft is an aircraft; the source (airplanes.live)
   * is a provenance detail, merged by icao24 into the one air layer and shown as
   * a badge. Polled whenever the air (جوّي) tab is open. */
  useEffect(() => {
    if (view !== "live") {
      setMilStates([]);
      return;
    }
    let cancelled = false;
    const tick = () =>
      api
        .military()
        .then((m) => {
          if (!cancelled) setMilStates(m.available ? m.states : []);
        })
        .catch(() => {
          if (!cancelled) setMilStates([]);
        });
    void tick();
    const t = window.setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [view]);

  /* ---------- ADS-B Exchange traffic — ALWAYS merged into the air layer ----------
   * DB-backed (/adsb): latest adsbexchange snapshot, AOI-bounded + geo-classified.
   * Same always-on merge as military. */
  useEffect(() => {
    if (view !== "live") {
      setAdsbStates([]);
      return;
    }
    let cancelled = false;
    const tick = () =>
      api
        .adsb()
        .then((m) => {
          if (!cancelled) setAdsbStates(m.available ? m.states : []);
        })
        .catch(() => {
          if (!cancelled) setAdsbStates([]);
        });
    void tick();
    const t = window.setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [view]);

  /* ---------- maritime poll (maritime tab) ----------
   * UI-FRESH: the source is now the analyst's choice. Default 'aisstream' — the live
   * feed alone. 'all' additionally serves the MarineTraffic store, which is static and
   * days stale, so it is opt-in rather than blended in by default. */
  useEffect(() => {
    localStorage.setItem("scope-mt", mtOn ? "on" : "off");
    if (!seaFeedOn) {
      setVessels([]);
      setSeaAvailable(false);
      setSeaSources([]);
      setSeaVintage(null);
      return;
    }
    let cancelled = false;
    const tick = () =>
      api
        .maritime(mtOn ? "all" : "aisstream")
        .then((m) => {
          if (cancelled) return;
          setVessels(m.available ? m.states : []);
          setSeaAvailable(m.available);
          setSeaAge(m.age_seconds);
          setSeaSources(m.sources ?? []);
          setSeaVintage(m.data_vintage);
        })
        .catch(() => {
          if (!cancelled) {
            setVessels([]);
            setSeaAvailable(false);
            setSeaSources([]);
          }
        });
    void tick();
    const t = window.setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
    // mtOn changes the requested source, so it must re-fetch
  }, [seaFeedOn, mtOn]);

  /* ⛔ AUTO-FALLBACK — show what IS available when the primary source has nothing.
     The AIS feed went silent on 2026-08-05 while the MarineTraffic scrape kept running, so
     the maritime map rendered EMPTY over a live second source for eleven days. An empty map
     is not an honest default when data exists; it just looks broken.
     ⚠ Only ever fires when the user has expressed NO preference (mtExplicit), and only in
     the direction of showing more — it never switches a layer off. It also cannot loop:
     enabling it makes the next response available, which stops the condition.
     ⚠ The map's dateline badge is what keeps this honest — it says outright that scraped
     positions are NOT live, whatever their age. */
  useEffect(() => {
    if (mtExplicit.current || mtOn || !seaFeedOn) return;
    if (seaAvailable) return;                 // the primary source is serving; leave it
    setMtOn(true);
  }, [seaAvailable, seaFeedOn, mtOn]);

  /* ---------- GNSS-anomaly overlay (maritime tab, toggleable) ----------
   * REST on-demand: the episode set changes slowly (recomputed by the G4a runner),
   * so a single fetch when toggled on is enough — no polling, no WS. Off by default;
   * an analyst opts into the anomaly overlay. */
  useEffect(() => {
    localStorage.setItem("scope-gnss", jammingOn ? "on" : "off");
    // maritime tab only (GNSS is a sea-layer overlay; the monitor has no ambient)
    if (!jammingOn || view !== "maritime") {
      setGnssAnomalies([]);
      return;
    }
    let cancelled = false;
    api
      .maritimeGnssAnomalies()
      .then((r) => {
        if (!cancelled) setGnssAnomalies(r.available ? r.anomalies : []);
      })
      .catch(() => {
        if (!cancelled) setGnssAnomalies([]);
      });
    return () => {
      cancelled = true;
    };
  }, [jammingOn, view]);

  /* ---------- jamming-zone choropleth (same overlay; variant sub-choice) ---------- */
  useEffect(() => {
    localStorage.setItem("scope-gnss-variant", zoneVariant);
    if (!jammingOn || view !== "maritime") {
      setZones([]);
      return;
    }
    let cancelled = false;
    api
      .maritimeJammingZones({ variant: zoneVariant })
      .then((r) => {
        if (!cancelled) setZones(r.cells ?? []);
      })
      .catch(() => {
        if (!cancelled) setZones([]);
      });
    return () => {
      cancelled = true;
    };
  }, [jammingOn, view, zoneVariant]);

  /* ---------- GFW dark-vessels (analysis layer; fetch on enable, per region) ---------- */
  useEffect(() => {
    localStorage.setItem("scope-dark", darkOn ? "on" : "off");
    localStorage.setItem("scope-dark-region", darkRegion);
    if (!darkOn || view !== "maritime") {
      setDarkVessels([]);
      setDarkAvailable(false);
      setDarkReason(null);
      setDarkCaveat(null);
      return;
    }
    let cancelled = false;
    setDarkLoading(true);
    api
      .maritimeDarkVessels({ region: darkRegion })
      .then((r) => {
        if (cancelled) return;
        setDarkVessels(r.vessels ?? []);
        setDarkAvailable(r.available);
        setDarkReason(r.reason);
        setDarkCaveat(r.caveat ?? null);
      })
      .catch(() => {
        if (cancelled) return;
        setDarkVessels([]);
        setDarkAvailable(false);
        setDarkReason("GFW API unreachable");
        setDarkCaveat(null);
      })
      .finally(() => {
        if (!cancelled) setDarkLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [darkOn, darkRegion, view]);

  /* ---------- vessel photo enrichment (best-effort Commons, on selection) ---------- */
  useEffect(() => {
    const seq = ++vesselPhotoSeq.current;
    setVesselPhoto(null);
    setVesselLicense(null);
    if (!selectedVessel) {
      setVesselPhotoLoading(false);
      return;
    }
    const v = vessels.find((x) => vesselKey(x) === selectedVessel);
    if (!v?.mmsi) {
      // MarineTraffic vessels are ship_id-keyed with no MMSI — the photo/detail
      // endpoints are MMSI-keyed, so there is nothing to fetch; the live snapshot
      // popup carries what MarineTraffic provides.
      setVesselPhotoLoading(false);
      return;
    }
    setVesselPhotoLoading(true);
    api
      .vessel(v.mmsi, { imo: v?.imo || undefined, name: v?.name || undefined })
      .then((r) => {
        if (seq !== vesselPhotoSeq.current) return;
        setVesselPhoto(r.photo);
        setVesselLicense(r.license);
      })
      .catch(() => {})
      .finally(() => {
        if (seq === vesselPhotoSeq.current) setVesselPhotoLoading(false);
      });
    // one fetch per selection; server caches. imo/name come from the current
    // vessel snapshot and don't change mid-voyage.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVessel]);

  /* ---------- satellite tab: group list + elements (TLEs change slowly,
   * so fetch on enter/group-change, no polling) ---------- */
  useEffect(() => {
    // satellites tab only (the monitor renders no ambient satellite layer)
    if (!spaceFeedOn) {
      setSatElements([]);
      return;
    }
    let cancelled = false;
    api.satelliteGroups().then((g) => {
      if (cancelled) return;
      setSatGroups(g.groups);
      // if the default group isn't cached, fall back to the first available
      if (g.groups.length && !g.groups.some((x) => x.group === satGroup)) {
        setSatGroup(g.groups[0].group);
      }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spaceFeedOn]);

  useEffect(() => {
    if (!spaceFeedOn) return;
    let cancelled = false;
    api.satellites(satGroup).then((r) => {
      if (cancelled) return;
      setSatElements(r.available ? r.elements : []);
      setSatFetchedAt(r.fetched_at);
      setSelectedSat(null);
    }).catch(() => {
      if (!cancelled) setSatElements([]);
    });
    return () => { cancelled = true; };
  }, [spaceFeedOn, satGroup]);

  /* ---------- satellite SATCAT identity (on selection; server-cached, local) ---------- */
  useEffect(() => {
    const seq = ++satEnrichSeq.current;
    setSatEnrichment(null);
    if (selectedSat == null) return;
    api
      .satellite(selectedSat)
      .then((r) => {
        if (seq === satEnrichSeq.current) setSatEnrichment(r.available ? r : null);
      })
      .catch(() => {});
    // one fetch per selection; identity is static, server-cached.
  }, [selectedSat]);

  /* ---------- reentry watch: AOI-relevant predicted reentries (satellite tab) ---------- */
  useEffect(() => {
    if (!spaceFeedOn) {
      setReentry([]);
      return;
    }
    let cancelled = false;
    api.reentry("aoi")
      .then((r) => { if (!cancelled) setReentry(r.available ? r.events : []); })
      .catch(() => { if (!cancelled) setReentry([]); });
    return () => { cancelled = true; };
  }, [spaceFeedOn]);

  /* ---------- search tab: debounced entity lookup (branches on searchEntity) ----------
   * DB-backed (/search/{vessel,aircraft}), debounced 300 ms, latest-wins. Adding
   * aircraft (E2) was exactly this branch + the SearchEntity union + the entities
   * array — SearchPanel (the shell) was NOT modified. */
  useEffect(() => {
    if (view !== "monitor") return;
    const q = searchQuery.trim();
    if (!q) {
      setSearchResults([]);
      setAcResults([]);
      setSatResults([]);
      setAirportResults([]);
      setAirportSel(null);
      setLandings([]);
      setSearchCount(null);
      setSearchLoading(false);
      return;
    }
    const seq = ++searchSeq2.current;
    setSearchLoading(true);
    const t = window.setTimeout(() => {
      const run =
        searchEntity === "vessel"
          ? api.searchVessel(q).then((r) => {
              if (seq === searchSeq2.current) {
                setSearchResults(r.matches); setSearchCount(r.count);
              }
            })
          : searchEntity === "aircraft"
            ? api.searchAircraft(q).then((r) => {
                if (seq === searchSeq2.current) {
                  setAcResults(r.matches); setSearchCount(r.count);
                }
              })
            : searchEntity === "satellite"
              ? api.searchSatellite(q).then((r) => {
                  if (seq === searchSeq2.current) {
                    setSatResults(r.matches); setSearchCount(r.count);
                  }
                })
              // airport: the endpoint returns the airport LIST + the top match's
              // landings; auto-select the top so its candidates show immediately.
              // ENHANCE — the shared window widens/narrows the landing search (hours).
              : api.searchAirport(q, { hours: searchWindowH }).then((r) => {
                  if (seq !== searchSeq2.current) return;
                  setAirportResults(r.matches);
                  setSearchCount(r.count);
                  setAirportSel(r.airport);
                  setLandings(r.candidates);
                  setSearchSelected(r.airport?.icao ?? null);
                });
      run
        .catch(() => {
          if (seq !== searchSeq2.current) return;
          if (searchEntity === "vessel") setSearchResults([]);
          else if (searchEntity === "aircraft") setAcResults([]);
          else if (searchEntity === "satellite") setSatResults([]);
          else { setAirportResults([]); setAirportSel(null); setLandings([]); }
          setSearchCount(0);
        })
        .finally(() => { if (seq === searchSeq2.current) setSearchLoading(false); });
    }, 300);
    return () => window.clearTimeout(t);
  }, [searchQuery, searchEntity, view, searchWindowH]);


  /* ---------- DASH-2e: the country card's feed ----------
   * ONE country, ordered by importance. `sort=importance` is requested EXPLICITLY here;
   * the global default stays `severity` (the honest threat order) exactly as DASH-1
   * committed. The backend does the sorting and echoes `sorted_by`, which the card reads
   * before ranking anything. */
  /* ⚠ DASH-2g′ — THE GATE THAT HAD TO MOVE WITH THE CARD, and the one regression this item
   * could most easily have shipped. It read `view !== "home"`, so with the home tab deleted
   * it would have matched NOTHING: `countryData` would be null on every surface and the
   * card — now pinned to all three domain tabs — would render its empty state everywhere,
   * permanently, with no error to notice. The gate now names the three tabs the card
   * actually lives on. Verified by fetching on each of the three, not assumed.
   * ⛔ DASH-IDLE — AND NOW ALSO «لوحة». The monitor's idle state mounts the SAME card
   * (DashboardPanel's empty-query slot), so the gate includes `monitor` — one fetch, one
   * `countryData`, three overlay consumers plus the idle consumer. Still excluded: `stats`
   * (the charts surface renders no card). */
  useEffect(() => {
    if (!CARD_VIEWS.includes(view) && view !== "monitor") { setCountryData(null); return; }
    let cancelled = false;
    const tick = () => {
      setCountryLoading(true);
      api.alerts({ country: cardCountry, sort: "importance", window_hours: cardWindowH })
        .then((r) => { if (!cancelled) setCountryData(r); })
        .catch(() => { /* keep the last good card on a transient error */ })
        .finally(() => { if (!cancelled) setCountryLoading(false); });
    };
    void tick();
    const t = window.setInterval(tick, 60_000);
    /* ⛔ DASH-4b-4 — A WRITE MUST NOT WAIT FOR THE NEXT 60 s TICK. Triage state is COLLECTIVE,
     * so the refetch is a full reload rather than a local patch: another analyst may have
     * changed something else inside the same minute, and patching one row optimistically would
     * show this browser a screen nobody else has. */
    alertsRefetch.current = tick;
    return () => { cancelled = true; window.clearInterval(t); alertsRefetch.current = null; };
  }, [view, cardCountry, cardWindowH]);

  /* leaving the domain tabs drops the entity selection: a payload from a surface the
   * analyst has left would come back on return as if it were current. */
  useEffect(() => { if (!CARD_VIEWS.includes(view)) setPanelEntity(null); }, [view]);

  /* ---------- search tab: switching entity (or leaving) clears results/selection ---------- */
  useEffect(() => {
    setSearchSelected(null);
    setSearchExpanded(false);
    setSearchTrack(null);
    setAcTrail(null);
    setSatTrack(null);
    setSearchResults([]);
    setAcResults([]);
    setSatResults([]);
    setAirportResults([]);
    setAirportSel(null);
    setLandings([]);
    setLandingSelected(null);
    setLandingTrail(null);
    setSearchCount(null);
    if (view !== "monitor") setSearchQuery("");
  }, [view, searchEntity]);

  /* ---------- search tab: a new selection collapses the previous track ---------- */
  useEffect(() => {
    setSearchExpanded(false);
    setSearchTrack(null);
    setSearchTrackHasMore(false);
    setAcTrackHasMore(false);
    setSatTrack(null);
    // selecting a new airport drops the previously-drilled landing aircraft
    setLandingSelected(null);
    setLandingTrail(null);
  }, [searchSelected]);

  /* ---------- airport: fetch a NON-top airport's landings on re-select ----------
   * The debounced search already loaded the top match's candidates; picking a
   * different airport from the list re-derives that one's landings. */
  useEffect(() => {
    if (searchEntity !== "airport" || !searchSelected) return;
    if (airportSel && searchSelected === airportSel.icao) return; // top already loaded
    const seq = ++airportSelSeq.current;
    api
      .searchAirport(searchQuery.trim(), { icao: searchSelected, hours: searchWindowH })
      .then((r) => {
        if (seq !== airportSelSeq.current) return;
        setAirportSel(r.airport);
        setLandings(r.candidates);
      })
      .catch(() => { if (seq === airportSelSeq.current) { setAirportSel(null); setLandings([]); } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchEntity, searchSelected, searchWindowH]);

  /* ---------- airport: drill a landing candidate to its aircraft track ----------
   * Reuses E2's /history over the landing window; the trail renders in the same
   * altitude-coloured aircraftTrail map layer. */
  useEffect(() => {
    if (searchEntity !== "airport" || !landingSelected) { setLandingTrail(null); return; }
    const c = landings.find((x) => x.icao24 === landingSelected);
    if (!c) { setLandingTrail(null); return; }
    const to = Math.floor(Date.parse(c.landing_ts) / 1000);
    const from = to - 6 * 3600;                        // the 6 h leading into the landing
    const seq = ++trackSeq.current;
    setLandingLoading(true);
    setLandingTrail(null);
    api
      .history(landingSelected, { from, to, limit: 5000, source: c.source ?? undefined })
      .then((h) => {
        if (seq !== trackSeq.current) return;
        const pts = h.points
          .filter((p) => p.latitude != null && p.longitude != null)
          .map((p) =>
            [p.latitude as number, p.longitude as number, p.baro_altitude] as
              [number, number, number | null],
          );
        setLandingTrail(pts.length ? pts : null);
      })
      .catch(() => { if (seq === trackSeq.current) setLandingTrail(null); })
      .finally(() => { if (seq === trackSeq.current) setLandingLoading(false); });
  }, [searchEntity, landingSelected, landings]);

  /* ---------- D4 area-search: the box query (box + window + domain filter) ---------- */
  useEffect(() => {
    if (view !== "monitor" || panelMode !== "map" || !areaBox) return;
    const domains = [areaAir && "air", areaSea && "sea", areaSpace && "space"].filter(Boolean).join(",");
    if (!domains) { setAreaResult(null); return; }
    const seq = ++areaSeq.current;
    setAreaLoading(true);
    const since = new Date(Date.now() - areaWindowH * 3600 * 1000).toISOString();
    api
      .searchArea({ lat1: areaBox[0], lon1: areaBox[1], lat2: areaBox[2], lon2: areaBox[3] }, { since, domains })
      .then((r) => { if (seq === areaSeq.current) setAreaResult(r); })
      .catch(() => { if (seq === areaSeq.current) setAreaResult(null); })
      .finally(() => { if (seq === areaSeq.current) setAreaLoading(false); });
  }, [view, panelMode, areaBox, areaWindowH, areaAir, areaSea, areaSpace]);

  /* ---------- D4 area-search: drill a result to its track (reuses the domain layers) ---------- */
  useEffect(() => {
    setAreaAirTrail(null); setAreaSeaTrack(null); setAreaSpaceTrack(null); setAreaSpaceEl(null);
    if (view !== "monitor" || panelMode !== "map" || !areaSelected) return;
    const { domain, id } = areaSelected;
    const seq = ++areaTrackSeq.current;
    if (domain === "air") {
      const entry = areaResult?.air.find((e) => e.id === id);
      const to = entry?.ts ? Math.floor(Date.parse(entry.ts) / 1000) : Math.floor(Date.now() / 1000);
      api.history(id, { from: to - 6 * 3600, to: to + 600, limit: 5000 })
        .then((h) => {
          if (seq !== areaTrackSeq.current) return;
          const pts = h.points.filter((p) => p.latitude != null && p.longitude != null)
            .map((p) => [p.latitude as number, p.longitude as number, p.baro_altitude] as [number, number, number | null]);
          setAreaAirTrail(pts.length ? pts : null);
        }).catch(() => {});
    } else if (domain === "sea") {
      api.vesselTrack(id).then((r) => { if (seq === areaTrackSeq.current) setAreaSeaTrack(r.points); }).catch(() => {});
    } else {
      api.searchSatellite(id).then((r) => {
        if (seq !== areaTrackSeq.current) return;
        const m = r.matches.find((x) => String(x.norad_id) === id);
        if (!m || !m.tle1 || !m.tle2) return;
        const rec = makeSatRec({ norad_id: m.norad_id, name: m.object_name ?? "", tle1: m.tle1, tle2: m.tle2, epoch: m.tle_epoch });
        if (!rec) return;
        const span = rec.periodMin && rec.periodMin > 0 ? rec.periodMin : 100;
        setAreaSpaceTrack(groundTrack(rec, new Date(), span, 120));
        setAreaSpaceEl({ norad_id: m.norad_id, name: m.object_name ?? String(m.norad_id), tle1: m.tle1, tle2: m.tle2, epoch: m.tle_epoch });
      }).catch(() => {});
    }
  }, [view, panelMode, areaSelected, areaResult]);

  /* ---------- D4 area-search: reset on leaving area mode / the monitor ---------- */
  useEffect(() => {
    if (view === "monitor" && panelMode === "map") return;
    setDrawMode(false);
    setAreaSelected(null);
  }, [view, panelMode]);

  /* ---------- vessel track: pull the full track ON DEMAND (expand) ----------
   * The expensive part — one vessel's whole fix sequence — is fetched only when
   * the analyst expands a selected result, never for the whole result list. */
  useEffect(() => {
    if (searchEntity !== "vessel" || !searchExpanded || !searchSelected) return;
    const seq = ++trackSeq.current;
    setSearchTrackLoading(true);
    setSearchTrack(null);
    // ENHANCE — the shared window bounds the track (was the full frozen span), anchored
    // at the vessel's last fix so it reads back `window` hours from the freshest data.
    const m = searchResults.find((v) => v.mmsi === searchSelected);
    const untilMs = m && m.last_ts ? Date.parse(m.last_ts) : Date.now();
    const since = new Date(untilMs - searchWindowH * 3600 * 1000).toISOString();
    const until = m && m.last_ts ? m.last_ts : undefined;
    api
      .vesselTrack(searchSelected, { since, until })
      .then((r) => {
        if (seq !== trackSeq.current) return;
        setSearchTrack(r.points);
        setSearchTrackHasMore(r.has_more);
      })
      .catch(() => {
        if (seq === trackSeq.current) { setSearchTrack([]); setSearchTrackHasMore(false); }
      })
      .finally(() => {
        if (seq === trackSeq.current) setSearchTrackLoading(false);
      });
  }, [searchEntity, searchExpanded, searchSelected, searchResults, searchWindowH]);

  /* ---------- aircraft focus: pre-expand map anchor (last-fix point) ----------
   * A single-point "trail" places the endpoint marker + pans there before the
   * full track is pulled; expanding replaces it (below). */
  useEffect(() => {
    if (searchEntity !== "aircraft" || searchExpanded) return;
    const m = acResults.find((x) => x.icao24 === searchSelected);
    setAcTrail(m && m.last_lat != null && m.last_lon != null
      ? [[m.last_lat, m.last_lon, m.baro_altitude]]
      : null);
  }, [searchEntity, searchExpanded, searchSelected, acResults]);

  /* ---------- aircraft track: pull ON DEMAND via /history (expand) ----------
   * LIVE-vs-pull DECISION: a POINT-IN-TIME pull of the 24 h ending at the
   * aircraft's last fix — NOT a live-tailing track. A self-refreshing track is
   * scope creep for entity-search; live monitoring belongs in Phase C. Re-expanding
   * re-pulls the latest. Reuses the existing /history endpoint (no new work). */
  useEffect(() => {
    if (searchEntity !== "aircraft" || !searchExpanded || !searchSelected) return;
    const m = acResults.find((x) => x.icao24 === searchSelected);
    if (!m || !m.last_ts) { setAcTrail(null); return; }
    const to = Math.floor(Date.parse(m.last_ts) / 1000);
    // ENHANCE — the shared window sets the track span (was a fixed 24h ending at the
    // last fix); /history's own 7d hard cap bounds it.
    const from = to - searchWindowH * 3600;
    const seq = ++trackSeq.current;
    setAcTrackLoading(true);
    setAcTrail(null);
    api
      .history(searchSelected, { from, to, limit: 10000, source: m.source ?? undefined })
      .then((h) => {
        if (seq !== trackSeq.current) return;
        const pts = h.points
          .filter((p) => p.latitude != null && p.longitude != null)
          .map((p) =>
            [p.latitude as number, p.longitude as number, p.baro_altitude] as
              [number, number, number | null],
          );
        setAcTrail(pts.length ? pts : null);
        setAcTrackHasMore(h.has_more);
      })
      .catch(() => { if (seq === trackSeq.current) { setAcTrail(null); setAcTrackHasMore(false); } })
      .finally(() => { if (seq === trackSeq.current) setAcTrackLoading(false); });
  }, [searchEntity, searchExpanded, searchSelected, acResults, searchWindowH]);

  /* ---------- satellite ground-track: COMPUTED client-side (expand) ----------
   * No stored fixes — the "track" is SGP4-propagated over ONE orbital period from
   * the carried TLE (lib/orbit.groundTrack), the same client-side physics the live
   * satellites tab uses (no server propagation endpoint). Instant, so no loading
   * state. For a GEO the one-period loop is a tiny near-stationary band (honest). */
  useEffect(() => {
    if (searchEntity !== "satellite" || !searchExpanded || !searchSelected) return;
    const m = satResults.find((x) => String(x.norad_id) === searchSelected);
    if (!m || !m.tle1 || !m.tle2) { setSatTrack(null); return; }
    const rec = makeSatRec({ norad_id: m.norad_id, name: m.object_name ?? "", tle1: m.tle1, tle2: m.tle2, epoch: m.tle_epoch });
    if (!rec) { setSatTrack(null); return; }
    const spanMin = rec.periodMin && rec.periodMin > 0 ? rec.periodMin : 100;
    setSatTrack(groundTrack(rec, new Date(), spanMin, 120));
  }, [searchEntity, searchExpanded, searchSelected, satResults]);

  /* ---------- deep link: apply pending ?icao= once the aircraft appears
   * in either the OpenSky payload or the military overlay ---------- */
  useEffect(() => {
    const pending = pendingSelectRef.current;
    if (!pending) return;
    const present =
      (payload?.states.some((s) => s.icao24 === pending) ?? false) ||
      milStates.some((s) => s.icao24 === pending) ||
      adsbStates.some((s) => s.icao24 === pending);
    if (present) {
      setSelected(pending);
      pendingSelectRef.current = null;
    }
  }, [payload, milStates, adsbStates]);

  /* ---------- deep link: keep the URL in sync with the selection ---------- */
  useEffect(() => {
    const url = new URL(window.location.href);
    if (selected) url.searchParams.set("icao", selected);
    else url.searchParams.delete("icao");
    window.history.replaceState(null, "", url);
  }, [selected]);

  /* ---------- server-side search of the current snapshot ---------- */
  useEffect(() => {
    if (!filtersActive(filters)) {
      setMatched(null);
      return;
    }
    const seq = ++searchSeq.current;
    const t = window.setTimeout(() => {
      const minAlt = filters.minAltFt === "" ? undefined : Number(filters.minAltFt) / M_TO_FT;
      const maxAlt = filters.maxAltFt === "" ? undefined : Number(filters.maxAltFt) / M_TO_FT;
      api
        .search({
          q: filters.q.trim() || undefined,
          min_alt: minAlt,
          max_alt: maxAlt,
          on_ground: filters.onGround === "any" ? undefined : filters.onGround === "ground",
          limit: 1000,
        })
        .then((res) => {
          if (seq === searchSeq.current) setMatched(new Set(res.states.map((s) => s.icao24)));
        })
        .catch(() => {
          /* filter fetch failed — keep last result; feed status shows errors */
        });
    }, 250);
    return () => window.clearTimeout(t);
  }, [filters, view, payload?.snapshot_time]);

  /* ---------- history trail for the selected aircraft ---------- */
  useEffect(() => {
    if (!selected) {
      setHistoryTrail(null);
      return;
    }
    let cancelled = false;
    const to = Math.floor(Date.now() / 1000);
    // pull the trail from the source the selected aircraft belongs to
    const source = milStatesRef.current.has(selected)
      ? "airplanes_live"
      : adsbStatesRef.current.has(selected)
        ? "adsbexchange"
        : "opensky";
    api
      .history(selected, { from: to - HISTORY_WINDOW_S, to, limit: 2000, source })
      .then((h) => {
        if (cancelled) return;
        const pts = h.points
          .filter((p) => p.latitude != null && p.longitude != null)
          .map(
            (p) =>
              [p.latitude as number, p.longitude as number, p.baro_altitude] as
                [number, number, number | null],
          );
        setHistoryTrail(pts.length > 1 ? pts : null);
      })
      .catch(() => {
        if (!cancelled) setHistoryTrail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, view]);

  /* ---------- enrichment (photo + standing route) for the selection ---------- */
  useEffect(() => {
    const seq = ++enrichSeq.current;
    if (!selected) {
      setEnrichment(null);
      setEnrichLoading(false);
      return;
    }
    // callsign from whichever payload currently holds the aircraft
    const all = payload?.states ?? [];
    const cs = all.find((s) => s.icao24 === selected)?.callsign;
    setEnrichLoading(true);
    api
      .aircraft(selected, cs)
      .then((e) => {
        if (seq === enrichSeq.current) setEnrichment(e);
      })
      .catch(() => {
        if (seq === enrichSeq.current) setEnrichment(null);
      })
      .finally(() => {
        if (seq === enrichSeq.current) setEnrichLoading(false);
      });
    // deliberately NOT keyed on payload: one fetch per selection is enough —
    // the server caches, and airframe/route don't change mid-flight
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  /* ---------- derived display state ---------- */
  const displayStates: StateVector[] = useMemo(() => {
    const all = payload?.states ?? [];
    if (!matched) return all;
    return all.filter((s) => matched.has(s.icao24));
  }, [payload, matched]);

  // military + ADS-B overlays merge into the map/list AFTER filters
  // (deliberately unfiltered — it is the point of the overlays); dedup by hex,
  // OpenSky wins, then military, then adsbexchange.
  const mapStates: StateVector[] = useMemo(() => {
    if (milStates.length === 0 && adsbStates.length === 0) return displayStates;
    const have = new Set(displayStates.map((s) => s.icao24));
    const merged: StateVector[] = [...displayStates];
    for (const s of milStates) if (!have.has(s.icao24)) { have.add(s.icao24); merged.push(s); }
    for (const s of adsbStates) if (!have.has(s.icao24)) { have.add(s.icao24); merged.push(s); }
    return merged;
  }, [view, displayStates, milStates, adsbStates]);
  // DASH-2g″ — see the ref's declaration: the map's click handler reads the live air layer
  // here rather than depending on it.
  mapStatesRef.current = mapStates;

  const selectedState = useMemo(
    () => mapStates.find((s) => s.icao24 === selected) ?? null,
    [mapStates, selected],
  );
  // which feeds see the selected aircraft — provenance for the strip badge, not a control
  const selectedSources = useMemo(() => {
    if (!selected) return [];
    const srcs: string[] = [];
    if (payload?.states.some((s) => s.icao24 === selected)) srcs.push("opensky");
    if (milSet.has(selected)) srcs.push("military");
    if (adsbSet.has(selected)) srcs.push("adsbexchange");
    return srcs;
  }, [selected, payload, milSet, adsbSet]);
  // emergencies come from the FULL live payload — search filters never hide them
  const emergencies = useMemo(
    () => findEmergencies(payload?.states ?? []),
    [payload],
  );

  const onToggleTheme = useCallback(() => setMapTheme(toggleMapTheme()), []);

  /* ---------- maritime derived ---------- */
  const isMaritime = view === "maritime";
  const filteredVessels = useMemo(() => {
    const q = vesselQuery.trim().toLowerCase();
    if (!q) return vessels;
    return vessels.filter(
      (v) =>
        (v.mmsi || "").includes(q) ||
        (v.ship_id || "").includes(q) ||
        (v.name || "").toLowerCase().includes(q) ||
        (v.imo || "").includes(q),
    );
  }, [vessels, vesselQuery]);
  const selectedVesselObj = useMemo(
    () => vessels.find((v) => vesselKey(v) === selectedVessel) ?? null,
    [vessels, selectedVessel],
  );

  /* ---------- DASH-2g″: the open panel, expressed in the MAP's key spaces ----------
   *
   * ⛔ SOURCE-AGNOSTIC BY CONSTRUCTION. It is derived from `panelEntity` and from nothing
   * else, so the ring marks whatever the panel is showing whether that came from a glyph or
   * from a country card. Deriving it instead from "the last map click" would make the mark
   * disagree with the panel the moment a card moved the panel — the map would be pointing at
   * one hull while the payload beside it described another.
   *
   * The vessel branch is the only translation, and it is needed in both directions: the
   * layer is keyed by `vesselKey` (MMSI for AIS, `ship_id` for MarineTraffic) and the panel
   * addresses payloads by MMSI. A map click already knows the layer key and says so in
   * `mapKey`; a card knows only the MMSI, so the hull is looked up in the layer — and when
   * it is not on the layer at all there is simply nothing to ring, which is honest.
   */
  const panelPick = useMemo(() => {
    if (!panelEntity) return null;
    if (panelEntity.kind !== "vessel") {
      return panelEntity.liveKey ? { kind: panelEntity.kind, key: panelEntity.liveKey } : null;
    }
    if (panelEntity.mapKey) return { kind: "vessel" as const, key: panelEntity.mapKey };
    const v = panelEntity.liveKey
      ? vessels.find((x) => x.mmsi === panelEntity.liveKey) ?? null
      : null;
    return v ? { kind: "vessel" as const, key: vesselKey(v) } : null;
  }, [panelEntity, vessels]);

  /* ---------- satellite derived ---------- */
  const isSat = view === "satellites";
  const selectedSatObj = useMemo(
    () => satElements.find((s) => s.norad_id === selectedSat) ?? null,
    [satElements, selectedSat],
  );

  /* ---------- search derived ---------- */
  const isMonitor = view === "monitor";
  // DASH-2g′: the country card + entity panel float over the map on the three domain tabs
  const hasCard = CARD_VIEWS.includes(view);
  // the map carries no aircraft on these tabs — a sea/space/search surface has nothing to
  // do with the live-aircraft layer, and it would be an unasked-for extra overlay
  const notAir = isMaritime || isSat || isMonitor;
  const selectedSummary = useMemo(
    () => searchResults.find((v) => v.mmsi === searchSelected) ?? null,
    [searchResults, searchSelected],
  );
  const selectedAcSummary = useMemo(
    () => acResults.find((a) => a.icao24 === searchSelected) ?? null,
    [acResults, searchSelected],
  );
  const selectedSatSummary = useMemo(
    () => satResults.find((s) => String(s.norad_id) === searchSelected) ?? null,
    [satResults, searchSelected],
  );
  // Reuse the existing violet satellite layer for the selected result's live
  // marker: one SatelliteElement drives the existing 1 Hz-propagated glyph + panTo,
  // so map integration adds only the ground-track polyline (below).
  const satElementForMap = useMemo<SatelliteElement | null>(() => {
    const s = selectedSatSummary;
    if (!s || !s.tle1 || !s.tle2) return null;
    return { norad_id: s.norad_id, name: s.object_name ?? String(s.norad_id), tle1: s.tle1, tle2: s.tle2, epoch: s.tle_epoch };
  }, [selectedSatSummary]);
  // Reuse the existing keyed vessel-marker layer for the selected result's last
  // fix: one synthetic Vessel drives the marker + panTo, so map integration adds
  // only the track polyline (below), not a second marker path.
  const searchFocusVessel = useMemo<Vessel | null>(() => {
    const s = selectedSummary;
    if (!s || s.last_lat == null || s.last_lon == null) return null;
    return {
      mmsi: s.mmsi, lat: s.last_lat, lon: s.last_lon, speed: s.last_speed,
      course: null, heading: null, nav_status: null, timestamp: s.last_ts,
      imo: s.imo, name: s.name, callsign: s.callsign,
      ship_type_code: s.ship_type_code, ship_type: s.ship_type,
      destination: null, draught: null, source: "frozen", data_origin: "terrestrial",
    };
  }, [selectedSummary]);

  /* ⛔ DASH-6b — BUILT ONCE, MOUNTED IN ONE OF TWO PLACES. The bar is identical on every
   * monitor surface; only WHERE it can live differs, because `entity` no longer leaves the
   * seat column free. Constructing it here keeps the props in one place — two call sites
   * drifting apart is how a "same bar" quietly stops being the same bar. */
  const seatFreshness = isMonitor ? (
    <SeatFreshness
      air={{ status, snapshotTs: payload?.ts ?? null }}
      alertDomains={countryData?.domains ?? null}
    />
  ) : null;

  return (
    <div
      /* ⛔ DASH-7 — ONE SURFACE CLASS, AND THE MAP HAS NO SEAT ANY MORE. The fourth proposal's
         thesis is «سطح واحد كامل، لا قسمة دائمة»: the console owns the whole region and the map
         is a SURFACE, not a permanent tenant of 40 % of it. `surf-map` releases the map to full
         bleed and collapses the console; every other surface hides the map entirely rather than
         painting an opaque console over a live Leaflet canvas — which is both the overlay-reveal
         defect UI-2 removed AND, measured in DASH-6b, a licence problem: the workspace covered
         `div.custom-map-attribution`.
         ⚠ The class is on the SHELL, not on `.map-host`, because both regions change together. */
      className={`shell${view === "stats" ? " stats-open" : ""}${isMonitor ? " monitor" : ""}`
        + (isMonitor ? ` surf-${panelMode}` : "")}
    >
      <main className="main">
        {/* ⛔ THE MAP'S DATELINE. A visitor seeing ships on a map reads them as live; the
            hollow "stale" marker glyph is an analyst's cue, not a first-read one. This
            states the age in words, and says outright when the positions are NOT live.
            Maritime view only — the air and satellite views carry their own freshness. */}
        {isMaritime && (() => {
          /* ⛔ LIVENESS COMES FROM THE CONTRIBUTING SOURCES, NOT FROM `available`.
             The first draft read `seaAvailable`, and with ?source=all the endpoint returns
             available=true whenever ANY source has rows — so the badge would have announced
             «مواضع حيّة» over 938 vessels while BOTH sources reported stale=true (AIS
             938,193 s old, MarineTraffic 401 s from a periodic scrape). That is precisely
             the false impression this badge exists to prevent, produced by the badge itself.
             A position is live only if the source that supplied it says so. */
          const contributing = seaSources.filter((x) => x.rows > 0);
          /* ⛔ AND "LIVE" IS A PROPERTY OF THE SOURCE, NOT ONLY OF ITS AGE. MarineTraffic is
             a PERIODIC SCRAPE (measured 2.2 min median between fixes for one vessel), so a
             fresh scrape is still not a live broadcast. Caught by watching the badge: at
             122 s MT falls under the staleness threshold and the badge announced «مواضع
             حيّة» over 961 scraped positions — the exact impression this element exists to
             prevent, at the one moment it looks most convincing.
             The backend already draws this line: /maritime/vessel returns `last_known` and
             never `live` for a ship_id, for this same reason. */
          const LIVE_CAPABLE = new Set(["aisstream"]);
          const live = contributing.length > 0
            && contributing.every((x) => !x.stale && LIVE_CAPABLE.has(x.source));
          return (
          <div className={`map-freshness${live ? "" : " stale"}`} role="status">
            {filteredVessels.length === 0
              ? T.vessel.mapAgeNone
              : (live ? T.vessel.mapAgeLive : T.vessel.mapAgeStale)(ageText(seaAge))}
            {contributing.length > 0 && (
              <span className="src">
                {T.vessel.mapAgeSrc(contributing.map((x) => x.source).join(" · "))}
              </span>
            )}
          </div>
          );
        })()}
        <MapView
          states={notAir ? EMPTY_STATES : mapStates}
          milSet={notAir ? EMPTY_KEYS : milSet}
          adsbSet={notAir ? EMPTY_KEYS : adsbSet}
          vessels={
            isMonitor
              ? (panelMode === "mon" && searchEntity === "vessel" && searchFocusVessel ? [searchFocusVessel] : [])
              : isMaritime ? filteredVessels : []
          }
          selectedVessel={isMonitor ? (panelMode === "mon" && searchEntity === "vessel" ? searchSelected : null) : selectedVessel}
          vesselTrack={
            isMonitor ? (panelMode === "map" ? areaSeaTrack : searchEntity === "vessel" ? searchTrack : null) : null
          }
          aircraftTrail={
            isMonitor
              ? (panelMode === "map" ? areaAirTrail
                  : searchEntity === "aircraft" ? acTrail
                    : searchEntity === "airport" ? landingTrail : null)
              : null
          }
          airport={
            isMonitor && panelMode === "mon" && searchEntity === "airport" && airportSel
              ? { lat: airportSel.lat, lon: airportSel.lon, label: airportSel.icao }
              : null
          }
          drawMode={isMonitor && panelMode === "map" && drawMode}
          onBoxDrawn={(b) => {
            // ⛔ UI-2 — no `setMapSummoned(false)`: finishing a box used to send the analyst back
            // to the results by hiding the map. The results were never hidden, so the box is drawn
            // and the crossings appear beside it.
            setAreaBox(b); setDrawMode(false); setAreaSelected(null);
          }}
          areaBox={isMonitor && panelMode === "map" ? areaBox : null}
          gnssAnomalies={isMaritime && jammingOn ? gnssAnomalies : EMPTY_GNSS}
          zones={isMaritime && jammingOn ? zones : EMPTY_ZONES}
          darkVessels={isMaritime && darkOn ? darkVessels : EMPTY_DARK}
          satellites={
            isSat
              ? satElements
              : isMonitor
                ? (panelMode === "map" && areaSpaceEl ? [areaSpaceEl]
                   : panelMode === "mon" && searchEntity === "satellite" && satElementForMap ? [satElementForMap] : [])
                : EMPTY_SATS
          }
          selectedSat={
            isMonitor && panelMode === "mon" && searchEntity === "satellite"
              ? (searchSelected ? Number(searchSelected) : null)
              : selectedSat
          }
          satelliteTrack={
            isMonitor ? (panelMode === "map" ? areaSpaceTrack : searchEntity === "satellite" ? satTrack : null) : null
          }
          snapshotTime={notAir ? null : (payload?.snapshot_time ?? null)}
          bbox={notAir ? null : (payload?.bbox ?? null)}
          theme={mapTheme}
          selected={notAir ? null : selected}
          historyTrail={notAir ? null : historyTrail}
          route={notAir ? null : (enrichment?.route ?? null)}
          onSelect={
            /* DASH-2g″ — on the three domain tabs a glyph click also opens the left panel;
               the monitor keeps routing its clicks to the search selection, which has its
               own surface and no such panel. */
            isMonitor && panelMode === "mon" && searchEntity === "aircraft"
              ? setSearchSelected : hasCard ? openMapAircraft : setSelected
          }
          onSelectVessel={
            isMonitor && panelMode === "mon" && searchEntity === "vessel"
              ? setSearchSelected : hasCard ? openMapVessel : setSelectedVessel
          }
          onSelectSat={
            isMonitor && panelMode === "mon" && searchEntity === "satellite"
              ? (n) => setSearchSelected(n == null ? null : String(n))
              : hasCard ? openMapSat : setSelectedSat
          }
          panelPick={hasCard ? panelPick : null}
          invalidateKey={revealTick}
        />

        {/* DASH-2g′ — the country card and the entity panel are now a FIXTURE of the three
            domain tabs, not a destination of their own. Same two floating panels 2d″ built
            and 2e/2f filled; only where they mount changed. The map stays the hero on every
            one of the three, and a card click opens the LEFT panel rather than routing away. */}
        {hasCard && (
          <DomainOverlay
            data={countryData}
            loading={countryLoading}
            onOpenAlert={openPanelEntity}
            country={cardCountry}
            onCountry={setCardCountry}
            windowH={cardWindowH}
            onWindow={setCardWindowH}
            entity={panelEntity}
            onCloseEntity={closePanelEntity}
            activeDomain={view === "live" ? "air" : view === "maritime" ? "sea" : "space"}
          />
        )}

        {/* ⛔ UI-2 stopped passing `onOpen` and `hidden`; ⛔ UI-3 DELETED them from DashboardPanel.
            Opening a card used to summon the map over the results — the map is seated now, so it is
            only a selection.
            ⚠ This note previously said both props "remain in DashboardPanel's signature because that
            file is one of the seven REBRAND-CLOSE files". They no longer do: the props were removed
            in a commit carrying that change ALONE, with the file's rebrand line left unstaged. The
            correction is recorded rather than overwritten, because a comment that survives the thing
            it describes is this project's most repeated defect. */}
        {isMonitor && (
          <DashboardPanel
            entity={searchEntity}
            onEntity={setSearchEntity}
            query={searchQuery}
            onQuery={setSearchQuery}
            loading={searchLoading}
            count={searchCount}
            selected={searchSelected}
            onSelect={setSearchSelected}
            vesselResults={searchResults}
            aircraftResults={acResults}
            satelliteResults={satResults}
            airportResults={airportResults}
            windowH={searchWindowH}
            onWindow={setSearchWindowH}
            mode={panelMode}
            onMode={setPanelMode}
            dashView={view}
            onDashView={setView}
            alertsData={countryData}
            alertsLoading={countryLoading}
            alertCountry={cardCountry}
            onAlertCountry={setCardCountry}
            alertWindowH={cardWindowH}
            onAlertWindow={setCardWindowH}
            onOpenAlert={(a) => { openPanelEntity(a); setPanelMode("ent"); }}
            focusAlert={liveFocusAlert}
            onTriaged={() => { alertsRefetch.current?.(); reloadCaseCount(); }}
            freshness={seatFreshness}
            routedTo={routedTo}
            caseCount={caseCount}
            onDetailDomain={setDetailDomain}
            detail={
              <DomainDetail
                domain={detailDomain}
                onDomain={setDetailDomain}
                payload={payload}
                displayStates={displayStates}
                status={status}
                air={
                  <>
                    <AlertBar emergencies={emergencies} onSelect={setSelected} />
                    <SearchBar
                      filters={filters}
                      onChange={setFilters}
                      showing={displayStates.length}
                      total={payload?.count ?? 0}
                    />
                    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 140, gap: 6 }}>
                      <div className="section-label">{T.list.section}</div>
                      <ContactList states={mapStates} milSet={milSet} adsbSet={adsbSet} selected={selected} onSelect={setSelected} />
                    </div>
                    <div>
                      <div className="section-label">{T.strip.section}</div>
                      <FlightStrip
                        state={selectedState}
                        enrichment={enrichment}
                        enrichLoading={enrichLoading}
                        sources={selectedSources}
                        onClear={() => setSelected(null)}
                      />
                    </div>
                  </>
                }
                sea={
                    <MaritimeSidebar
                      vessels={filteredVessels}
                      available={seaAvailable}
                      ageSeconds={seaAge}
                      sources={seaSources}
                      dataVintage={seaVintage}
                      mtOn={mtOn}
                      onMt={() => {
                        // An explicit choice: from here on the fallback stays out of it.
                        mtExplicit.current = true;
                        localStorage.setItem("scope-mt-explicit", "1");
                        setMtOn((v) => !v);
                      }}
                    >
                      <div>
                        <div className="section-label">{T.vessel.searchSection}</div>
                        <div className="search-row">
                          <input
                            className="input"
                            type="text"
                            placeholder={T.vessel.searchPlaceholder}
                            value={vesselQuery}
                            onChange={(e) => setVesselQuery(e.target.value)}
                            aria-label={T.vessel.searchAria}
                          />
                        </div>
                        <div className="filter-note" aria-live="polite">
                          {vesselQuery.trim()
                            ? T.vessel.showing(filteredVessels.length, vessels.length)
                            : ""}
                        </div>
                      </div>
                      {/* UI-2 — the LIVE picture first, in the grammar the live tab established:
                          tiles → feed → search → the list → the selected item's detail → analysis.
                          These two used to sit BELOW the 1,875px analysis stack, which put the
                          vessel list 2.55 screens down and pushed a selected vessel's own detail
                          off-screen at the moment of selecting it. */}
                      <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 140, gap: 6 }}>
                        <div className="section-label">{T.vessel.listSection}</div>
                        <VesselList vessels={filteredVessels} selected={selectedVessel} onSelect={setSelectedVessel} />
                      </div>
                      <div>
                        <div className="section-label">{T.vessel.stripSection}</div>
                        <VesselStrip
                          vessel={selectedVesselObj}
                          photo={vesselPhoto}
                          license={vesselLicense}
                          photoLoading={vesselPhotoLoading}
                          onClear={() => setSelectedVessel(null)}
                        />
                      </div>
                      {/* التحليل — derived findings, kept clearly SEPARATE from the live vessel
                          feed above. GNSS is a map layer; the rest are finding panels, each
                          collapsed by default (UI-2 step 2) so analysis is opt-in. */}
                      <div className="analysis-group">
                        <div className="section-label">{T.vessel.analysisSection}</div>
                        <div className="analysis-layer">
                          <button
                            className={`layer-btn${jammingOn ? " on" : ""}`}
                            aria-pressed={jammingOn}
                            onClick={() => setJammingOn((v) => !v)}
                            title={T.gnss.caveat}
                          >
                            <span className="layer-dot" aria-hidden="true" />
                            {T.gnss.toggle}
                            {jammingOn && gnssAnomalies.length > 0 ? ` · ${gnssAnomalies.length}` : ""}
                          </button>
                          {jammingOn && (
                            <div className="seg nav-seg" role="group" aria-label={T.gnss.toggle} title={T.gnss.caveat}>
                              {(["broadcast", "affected"] as const).map((v) => (
                                <button key={v} aria-pressed={zoneVariant === v} onClick={() => setZoneVariant(v)}>
                                  {T.gnss.variant[v]}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                        {/* GFW dark-vessels — satellite AIS, fills the sparse-region blind spot */}
                        <div className="analysis-layer">
                          <button
                            className={`layer-btn dark${darkOn ? " on" : ""}`}
                            aria-pressed={darkOn}
                            onClick={() => setDarkOn((v) => !v)}
                          >
                            <span className="layer-dot" aria-hidden="true" />
                            {T.vessel.darkLayer}
                            {darkOn && darkVessels.length > 0 ? ` · ${darkVessels.length}` : ""}
                          </button>
                          {darkOn && (
                            <DarkVessels
                              vessels={darkVessels}
                              available={darkAvailable}
                              reason={darkReason}
                              loading={darkLoading}
                              region={darkRegion}
                              onRegion={setDarkRegion}
                              caveat={darkCaveat}
                            />
                          )}
                        </div>
                        <HullComposite />
                        <CrossSourcePanel />
                        <GapCandidates />
                        <Rendezvous />
                        <IdentityChanges />
                      </div>
                    </MaritimeSidebar>
                  /* ⚠ The monitor is an explicit link in this chain as a CORRECTNESS requirement, not a
                     tidy-up: without it, a View lacking a branch falls through to <Sidebar> below and
                     renders the live-aircraft contact list and strip behind a view with nothing to do
                     with aircraft. The monitor renders no sidebar — it owns the full shell width.
                     DASH-2g′ removed the `isHome` link here along with the View itself. */
                }
                space={
                  <SatelliteSidebar
                    satellites={satElements}
                    groups={satGroups}
                    group={satGroup}
                    fetchedAt={satFetchedAt}
                    onGroup={setSatGroup}
                  >
                    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 140, gap: 6 }}>
                      <div className="section-label">{T.satellite.listSection}</div>
                      <SatelliteList satellites={satElements} selected={selectedSat} onSelect={setSelectedSat} />
                    </div>
                    <div>
                      <div className="section-label">{T.satellite.stripSection}</div>
                      <SatelliteStrip
                        element={selectedSatObj}
                        enrichment={satEnrichment}
                        onClear={() => setSelectedSat(null)}
                      />
                    </div>
                    <ReentryWatch events={reentry} />
                  </SatelliteSidebar>
                }
              />
            }
            area={{
              drawing: drawMode,
              // ⛔ UI-2 — no summon: the map is seated, so drawing starts on a map already visible.
              // ⚠ THIS IS WHAT MAKES `areaPrompt` TRUE WITHOUT EDITING IT: «ارسم صندوقاً على
              // الخريطة» was displayed while the map was 0 % of the width; it now describes what
              // the analyst can see. Copy that needed rewriting would have meant the seat was wrong.
              onDraw: () => { setPanelMode("map"); setDrawMode(true); },
              hasBox: areaBox !== null,
              windowH: areaWindowH,
              onWindow: setAreaWindowH,
              air: areaAir, sea: areaSea, space: areaSpace,
              onAir: () => setAreaAir((v) => !v),
              onSea: () => setAreaSea((v) => !v),
              onSpace: () => setAreaSpace((v) => !v),
              result: areaResult,
              loading: areaLoading,
              selected: areaSelected,
              onSelect: (domain, id) => {
                const next = areaSelected && areaSelected.id === id && areaSelected.domain === domain ? null : { domain, id };
                setAreaSelected(next);
                // ⛔ UI-2 — selecting a crossing used to summon the map to show its track; the
                // seat already shows it, so selection is selection and nothing changes layout.
              },
            }}
          />
        )}

        {/* ⛔ UI-2 — the draw state's guidance and its cancel, over the seat's top edge (never its
            bottom: the Leaflet attribution lives there and it is a licence condition). This is the
            gap the measurement found — draw mode showed a bare map with no instruction and no way
            out but leaving the map entirely. */}
        {isMonitor && panelMode === "map" && drawMode && (
          <div className="draw-hint" role="status">
            <span>{T.dashboard.drawHint}</span>
            <button onClick={() => setDrawMode(false)}>{T.dashboard.drawCancel}</button>
          </div>
        )}

        {/* ⛔ UI-2 — gated on a SELECTION, not on a revealed map. `MonitorDetail` always renders a
            panel, so gating it on `panelMode === "mon"` alone would float an empty card whenever
            the analyst is merely browsing. `searchSelected` is the condition that used to be
            implied by having summoned the map. */}
        {isMonitor && (
          <>
            {panelMode === "mon" && searchSelected !== null && (
              <MonitorDetail
                entity={searchEntity}
                expanded={searchExpanded}
                onToggleTrack={() => setSearchExpanded((v) => !v)}
                onClear={() => setSearchSelected(null)}
                vesselSummary={selectedSummary}
                vesselTrack={searchTrack}
                vesselTrackLoading={searchTrackLoading}
                vesselTrackHasMore={searchTrackHasMore}
                aircraftSummary={selectedAcSummary}
                aircraftTrail={acTrail}
                aircraftTrackLoading={acTrackLoading}
                aircraftTrackHasMore={acTrackHasMore}
                satelliteSummary={selectedSatSummary}
                airportSummary={airportSel}
                landings={landings}
                landingLoading={landingLoading}
                landingSelected={landingSelected}
                onSelectLanding={setLandingSelected}
              />
            )}
          </>
        )}

        {/* ⛔ DASH-IDLE — the monitor carries the SAME entity payload panel the domain tabs
            carry, but only when an alert (or map glyph) actually opened one: unlike
            DomainOverlay, no permanent «لم يُختر كيان» placeholder floats here, because this
            surface has a workspace column that would sit beside a permanently-empty panel.
            It is positioned over the map seat by `.workspace ~ .home-panel.entity` in app.css
            — the .map-detail recipe, so the Leaflet attribution underneath stays visible. */}
        {isMonitor && panelEntity !== null && (
          <EntityPayload sel={panelEntity} onClose={closePanelEntity} />
        )}

        {/* ⛔ DASH-BAND — the content that claims the band under the square seat. The token's
            own rule was «content first, height second»: --below-map-h stays 0 until real
            content exists. This bar is that content — per-domain freshness from state this
            component already holds (no fetch, no new endpoint): the air feed's live verdict,
            and the alert feed's per-domain vintage for sea/space, whose own feeds are gated
            off on this surface (see SeatFreshness's header for the measured honesty reason).
            Rendered ONLY on the monitor: the domain tabs run their map full-bleed with no
            band, and stats unmounts the map.
            ⛔ DASH-6b — AND NOT ON `entity`, WHICH NO LONGER HAS A BAND TO CLAIM. This bar is
            `position: absolute` inside the seat's column; releasing that surface put the
            opaque workspace over it, and G9 caught it immediately — `span` ink covered by
            `.dash-head-row`, `.section-label`, `button.er-head` and `.card-grid` at BOTH gate
            widths. Hiding it was refused for the reason SeatFreshness's own header gives:
            the air LIVE verdict exists nowhere else on that surface (`.dash-secondary` prints
            a per-domain vintage, not a liveness word), and freshness is not optional on a
            live surface. ⇒ It moves INTO the content column there, in flow, where nothing can
            cover it — same component, same props, same data, one mount either way. */}
        {isMonitor ? null : seatFreshness}

        {view === "stats" && <StatsView onDashView={setView} />}

        {/* ⛔ DASH-NAV — the navbar no longer carries any dashboard control. The رصد/إحصاءات
            pair moved into the page (DashboardPanel's head row and StatsView's controls):
            one surface, one hierarchy, one place. The theme button is all that remains
            here as a contextual child. */}


        <NavBar view={view} onSelect={setView}>
          {/* source toggles are gone — feeds merge per domain; analysis layers
              (GNSS, …) live in the domain's «تحليل» group, not the nav bar.
              ⛔ DASH-NAV — and the dashboard's رصد/إحصاءات pair is gone from here too:
              it lives on the surfaces it switches (see DashViewToggle). */}
          <button
            className="nav-theme"
            onClick={onToggleTheme}
            aria-label={mapTheme === "dark" ? T.theme.toLight : T.theme.toDark}
            title={mapTheme === "dark" ? T.theme.toLight : T.theme.toDark}
          >
            {mapTheme === "dark" ? T.theme.lightLabel : T.theme.darkLabel}
          </button>
        </NavBar>

      </main>
    </div>
  );
}
