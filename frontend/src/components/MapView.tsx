/**
 * Leaflet map engine.
 *
 * Why Leaflet (not MapLibre GL): raster tiles are the app's ONE permitted
 * external call and degrade gracefully tile-by-tile; MapLibre would add a
 * second hard external dependency (vector style/fonts/sprites) and needs a
 * GPU the target hardware may not spare. Leaflet is ~42 KB, bundles fully
 * offline, and divIcon HTML glyphs match the instrument aesthetic exactly.
 *
 * Engine rules:
 *  - replace-wholesale per payload: any aircraft absent from the new payload
 *    is removed — no accumulation, no memory growth.
 *  - interpolation: positions lerp from displayed → target over the measured
 *    push interval (EMA, ≤120 s); rotation eases via shortest arc; marker
 *    DOM updates throttled to 15 Hz. prefers-reduced-motion ⇒ snap.
 *  - altitude encoding: glyph size 14→24 px and opacity 0.6→1 over 0–12 km.
 *  - TRAIL (selected only, by design): no ambient per-aircraft trails — the
 *    SELECTED aircraft's path is drawn from /api/v1/history (server truth,
 *    survives page refresh), colored by altitude (teal low → gold high, the
 *    brand ramp), keeps growing live while selected, and disappears on
 *    deselect. One aircraft's segments instead of ~1100 ambient polylines.
 *  - tile failures: counted; a notice overlays the map, the app never blanks.
 */

import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { BBox, DarkVessel, GnssEvent, RouteInfo, SatelliteElement, StateVector, Vessel, VesselTrackPoint, ZoneCell } from "../api/types";
import { vesselKey } from "../api/types";
import { esc } from "../lib/escapeHtml";
import type { Theme } from "../lib/theme";
import "maplibre-gl/dist/maplibre-gl.css";
import "@maplibre/maplibre-gl-leaflet";
import { TILE_URL, TILE_ATTRIBUTION, STYLE_OVERRIDES, RASTER_FALLBACK_URL, RASTER_FALLBACK_ATTRIBUTION, hasWebGL } from "../lib/theme";
import { makeSatRec, propagate } from "../lib/orbit";
import type { SatRec } from "../lib/orbit";
import { isStale } from "../lib/format";
import { T } from "../lib/strings";

// Satellite bus + gridded solar arrays + dish antenna — a rotation-agnostic
// point mark (never rotated), so it reads as a spacecraft rather than a star.
// One compound <path> (multiple sub-paths): the paint-order glow wraps each
// panel cell, giving the solar-array grid look for free. currentColor fill.
const SAT_PATH =
  "M1.4 9 H4.4 V15 H1.4 Z M5 9 H8 V15 H5 Z M16 9 H19 V15 H16 Z M19.6 9 H22.6 V15 H19.6 Z " +
  "M7.9 11.3 H9.8 V12.7 H7.9 Z M14.2 11.3 H16.1 V12.7 H14.2 Z " +
  "M10.6 8.8 H13.4 Q14.4 8.8 14.4 9.8 V14.2 Q14.4 15.2 13.4 15.2 H10.6 Q9.6 15.2 9.6 14.2 V9.8 Q9.6 8.8 10.6 8.8 Z " +
  "M11.6 5.6 H12.4 V8.8 H11.6 Z M10.6 5.6 H13.4 L12 4 Z";

function satIconHtml(sel: boolean): string {
  const sz = sel ? 20 : 12;
  const off = -sz / 2;
  return `<svg class="sg" viewBox="0 0 24 24" aria-hidden="true" style="width:${sz}px;height:${sz}px;margin:${off}px 0 0 ${off}px"><path d="${SAT_PATH}"/></svg>`;
}

const RANGE_RING_M = 9260; // 5 NM
const ALT_MAX_M = 12000;
const SEL_TRAIL_MAX = 600; // fixes kept for the selected aircraft's live tail
// Wide regions (Middle East box) carry several hundred aircraft: DOM marker
// updates are throttled to 15 Hz — visually smooth, ~4× cheaper than 60 fps.
const ANIM_TICK_MS = 1000 / 15;
const ANIM_DUR_MAX_MS = 120_000; // glide across slow poll cadences (4-credit boxes)

// Top-view airliner silhouette — rounded nose, swept wings, tailplane; nose
// pointing north so the existing track rotation applies unchanged. Sleeker
// proportions than the old Material glyph. One <svg> per aircraft; glow via
// paint-order stroke, NOT a drop-shadow filter.
const GLYPH_SVG =
  '<svg class="g" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1 C12.75 1 13.1 1.9 13.1 3.6 L13.1 8.3 L22 14 L22 16 L13.1 13 L13.1 18.6 L15.4 20.7 L15.4 22.1 L12 21.05 L8.6 22.1 L8.6 20.7 L10.9 18.6 L10.9 13 L2 16 L2 14 L10.9 8.3 L10.9 3.6 C10.9 1.9 11.25 1 12 1 Z"/></svg>';

// Top-view vessel hull: sharp pointed bow (north), curved sheer, rounded
// stern — a sleeker silhouette than the old boxy hull. Rotation is baked into
// the SVG transform per vessel (heading, else course, else north) so it
// applies before the element is in the DOM. Glow via paint-order — no filter.
const VESSEL_PATH =
  "M12 1 C13.5 3.3 14.7 6 14.7 10 L14.7 18.8 C14.7 21 14 22.6 12 22.6 C10 22.6 9.3 21 9.3 18.8 L9.3 10 C9.3 6 10.5 3.3 12 1 Z";

function vesselIconHtml(rot: number, sel: boolean, mt: boolean, stale: boolean): string {
  const sz = sel ? 22 : 15;
  const off = -sz / 2;
  // MarineTraffic vessels get the .mt class → a distinct amber (the path fills
  // with currentColor, so the CSS colour override tints it); AIS keeps the
  // default vessel hue. So the independent source reads at a glance.
  // UI-FRESH: .stale marks a position older than the per-row threshold — a hollow, dimmed
  // hull, so "this is not where the ship is now" reads from SHAPE, not colour alone
  // (a stale AIS row and a stale MT row are both marked, each keeping its source hue).
  return `<svg class="vg${mt ? " mt" : ""}${stale ? " stale" : ""}" viewBox="0 0 24 24" aria-hidden="true" style="width:${sz}px;height:${sz}px;margin:${off}px 0 0 ${off}px;transform:rotate(${rot}deg)"><path d="${VESSEL_PATH}"/></svg>`;
}

// GNSS-anomaly episode marker: ONE crimson hue (--glyph-gnss), subtype by
// intensity/shape so one legend colour reads as "GNSS anomaly" — individual =
// brightest/largest (the actionable lead), area = dimmer (a victim; many overlap at
// a decoy), glitch = faint dashed (noise / bad transmitter).
function gnssStyle(gnssType: string | null, color: string): L.CircleMarkerOptions {
  const base = { color, weight: 1.5, fillColor: color, interactive: true };
  if (gnssType === "area") return { ...base, radius: 5, opacity: 0.5, fillOpacity: 0.3 };
  if (gnssType === "glitch")
    return { ...base, radius: 3.5, opacity: 0.4, fillOpacity: 0.15, dashArray: "2 3" };
  return { ...base, radius: 6, opacity: 0.95, fillOpacity: 0.8 }; // individual
}

// Analyst "why is this flagged" popup — the quality evidence, honestly labelled.
function gnssPopupHtml(a: GnssEvent): string {
  const lbl = T.gnss.popup;
  const lines = [
    `<b>${esc(a.name ?? a.mmsi)}</b>`,
    `${lbl.type}: ${lbl.types[a.gnss_type ?? ""] ?? esc(a.gnss_type ?? "?")}`,
    `${lbl.fixes}: ${a.n_implausible_fixes ?? "?"}`,
    a.decoy_match ? lbl.decoy : "",
    a.implied_speed_max != null ? `${lbl.speed}: ${Math.round(a.implied_speed_max)} ${lbl.kn}` : "",
    a.inland_km_max != null ? `${lbl.inland}: ${Math.round(a.inland_km_max)} ${lbl.km}` : "",
    `<i class="gnss-caveat">${T.gnss.caveat}</i>`,   // lead, not proof — always visible
  ].filter(Boolean);
  return `<div class="gnss-pop">${lines.join("<br>")}</div>`;
}

// Choropleth cell fill: the crimson hue, opacity graduated by vessel count (kept low
// so the basemap + markers read through). Broadcast = solid edge (where positions
// LANDED), affected = dashed edge (where vessels actually WERE) — the two mean
// different things, so the edge distinguishes them at a glance.
function zoneStyle(vessels: number, maxV: number, variant: string, color: string): L.PathOptions {
  const f = Math.min(1, vessels / Math.max(1, maxV));
  return {
    color,
    weight: 1,
    opacity: 0.6,
    dashArray: variant === "affected" ? "5 5" : undefined,
    fillColor: color,
    fillOpacity: 0.1 + 0.4 * f,
    interactive: true,
  };
}

function zonePopupHtml(z: ZoneCell, variant: string): string {
  const lines = [
    `<b>${T.gnss.variant[variant as "broadcast" | "affected"] ?? esc(variant)}</b>`,
    `${T.gnss.zoneVessels}: ${z.distinct_vessels}`,
    `${T.gnss.window}: ${esc(z.window_start.slice(0, 16).replace("T", " "))}`,
    `<i class="gnss-caveat">${T.gnss.caveat}</i>`,
  ];
  return `<div class="gnss-pop">${lines.join("<br>")}</div>`;
}

type TrailPoint = [number, number, number | null];

interface Tracked {
  marker: L.Marker;
  glyphEl: HTMLElement | null;
  state: StateVector;
  fromLat: number;
  fromLng: number;
  fromTrack: number;
  toLat: number;
  toLng: number;
  toTrack: number;
  animStart: number;
  animDur: number;
}

interface Props {
  states: StateVector[];
  snapshotTime: number | null;
  bbox: BBox | null;
  theme: Theme;
  selected: string | null;
  /** fetched /history points for the selected aircraft (ASC) */
  historyTrail: TrailPoint[] | null;
  /** standing route of the selected aircraft — draws the origin→destination line */
  route: RouteInfo | null;
  /** hexes from the military overlay — get the .mil glyph treatment */
  milSet: Set<string>;
  /** hexes from the ADS-B Exchange overlay — get the .adsb glyph treatment */
  adsbSet: Set<string>;
  /** live maritime AIS overlay — rendered as a SEPARATE vessel layer
   * (MMSI-keyed, ship glyph); does not touch aircraft state */
  vessels: Vessel[];
  /** selected vessel MMSI (maritime tab) — emphasized + panned-to */
  selectedVessel: string | null;
  /** E1 search tab — the selected vessel's full track (ASC). Drawn as a
   * quality-coloured polyline: plausible runs in the vessel hue; suspect/
   * implausible runs crimson + dashed (the GNSS hue) so a spoofed/jammed track
   * segment is VISIBLE, never hidden. Null clears the layer (and skips the fit). */
  vesselTrack: VesselTrackPoint[] | null;
  /** E2 search tab — the selected aircraft's track ([lat,lon,baro_alt] ASC), or a
   * single last-fix point as a pre-expand anchor. Drawn altitude-coloured (the
   * SAME teal→gold ramp as the live history trail — air has no quality layer, so
   * NOT the maritime crimson) with an endpoint highlight; the map fits/pans to it.
   * Null clears the layer. */
  aircraftTrail: TrailPoint[] | null;
  /** E3 search tab — the selected satellite's ground-track ([lat,lon] ASC, one
   * orbital period, SGP4-propagated client-side). Drawn in the violet satellite hue
   * (--glyph-sat), split at antimeridian crossings so a wrap isn't a line across the
   * whole map. For a GEO this is a tiny near-stationary loop (honest, not a wild
   * line). The live glyph comes from the reused satellite layer. Null clears it. */
  satelliteTrack: [number, number][] | null;
  /** E4 search tab — the selected airport (a marker + ICAO label + pan-to). The
   * one map element the airport entity adds; the landed aircraft's track reuses
   * aircraftTrail. Null clears it. */
  airport: { lat: number; lon: number; label: string } | null;
  /** D4 area-search — draw mode: while true, a drag draws a live rectangle (map
   * dragging disabled), and mouseup calls onBoxDrawn([lat1,lon1,lat2,lon2]). */
  drawMode: boolean;
  onBoxDrawn: (box: [number, number, number, number]) => void;
  /** the finalized area box to render persistently (the current query box). */
  areaBox: [number, number, number, number] | null;
  /** GNSS-anomaly episodes (maritime tab, toggleable) — a SEPARATE crimson point
   * layer at each episode's first implausible fix (the decoy landing for decoy
   * episodes); click → quality-evidence popup. Empty unless the overlay is on. */
  gnssAnomalies: GnssEvent[];
  /** jamming-zone cells (same overlay concept as gnssAnomalies) — a crimson
   * choropleth (L.rectangle) drawn UNDER the markers. variant chooses broadcast
   * (decoy landing) vs affected (real positions). Empty unless the overlay is on. */
  zones: ZoneCell[];
  /** GFW dark-vessel events (analysis layer) — a violet marker at each gap
   *  position (where a vessel went silent). Empty unless the layer is on. */
  darkVessels: DarkVessel[];
  /** satellite elements (أقمار tab) — positions propagated locally (SGP4) */
  satellites: SatelliteElement[];
  /** selected satellite NORAD id — emphasized + panned-to */
  selectedSat: number | null;
  /**
   * DASH-2g″ — which entity the LEFT payload panel is currently showing, in THIS layer's
   * key space (icao24 · the vessel layer's composite key · NORAD as a string).
   *
   * ⛔ A SECOND, INDEPENDENT CHANNEL FROM `selected*`, and deliberately so. `selected*` is
   * the map's own selection and owns the map's own consequences — the trail, the track, the
   * pan-to. This says one narrower thing: "the open panel is describing THIS glyph." They
   * are set together by a map click and can legitimately differ afterwards (a country-card
   * click moves the panel without touching the map's selection), so a mark that meant both
   * at once would have to lie in one of the two cases.
   *
   * ⛔ IT IS DRAWN AS A RING — a shape the glyph does not otherwise have — NOT as a colour.
   * The project rule (UI-FRESH) is that a judgement needs a second, non-colour channel; the
   * three glyph families already spend colour on altitude, source and staleness, so another
   * hue here would be both invisible to a colour-blind reader and ambiguous against those.
   *
   * Null clears the ring everywhere, which is how closing the panel removes the mark.
   */
  panelPick: { kind: "aircraft" | "vessel" | "satellite"; key: string } | null;
  onSelect: (icao24: string | null) => void;
  onSelectVessel: (mmsi: string | null) => void;
  onSelectSat: (norad: number | null) => void;
  /** bumps when the monitor summons the (previously-covered) map, so Leaflet
   * re-measures its container after the overlay-reveal. */
  invalidateKey?: number;
}

// Read a theme token from the map host (which carries the basemap's data-theme)
// so JS-drawn colors — trails, route, ring, bbox — track the map, not the UI.
function cssVar(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

function glyphSize(alt: number | null): number {
  if (alt == null) return 18;
  const f = Math.min(Math.max(alt, 0), ALT_MAX_M) / ALT_MAX_M;
  return 14 + f * 10;
}

function glyphOpacity(alt: number | null): number {
  if (alt == null) return 1;
  const f = Math.min(Math.max(alt, 0), ALT_MAX_M) / ALT_MAX_M;
  return 0.6 + f * 0.4;
}

const EMERGENCY_SQUAWKS = new Set(["7500", "7600", "7700"]);

function shortestArc(from: number, to: number): number {
  let d = ((to - from) % 360) + 360;
  d %= 360;
  return d > 180 ? d - 360 : d;
}

/* Altitude → color: brand teal (low) → brand gold (high), read from theme
 * tokens so both themes stay coherent. */
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function makeAltColor(el: Element): (alt: number | null) => string {
  const lo = hexToRgb(cssVar(el, "--glyph-ground") || "#17a496");
  const hi = hexToRgb(cssVar(el, "--accent") || "#d9ab4a");
  return (alt) => {
    if (alt == null) return cssVar(el, "--trail-selected") || "#ffffff";
    const f = Math.min(Math.max(alt, 0), ALT_MAX_M) / ALT_MAX_M;
    const c = lo.map((l, i) => Math.round(l + (hi[i] - l) * f));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  };
}

export default function MapView({
  states, snapshotTime, bbox, theme, selected, historyTrail, route, milSet, adsbSet, vessels,
  selectedVessel, vesselTrack, aircraftTrail, gnssAnomalies, zones, darkVessels, satellites, selectedSat,
  satelliteTrack, airport, drawMode, onBoxDrawn, areaBox, panelPick,
  onSelect, onSelectVessel, onSelectSat, invalidateKey,
}: Props) {
  const onBoxDrawnRef = useRef(onBoxDrawn);
  onBoxDrawnRef.current = onBoxDrawn;
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);

  // Overlay-reveal (monitor): the map is mounted full-size under the workspace
  // overlay; when it's revealed we re-measure so Leaflet paints/interacts correctly.
  useEffect(() => {
    if (invalidateKey == null) return;
    const map = mapRef.current;
    if (map) {
      const t = setTimeout(() => map.invalidateSize(), 0);
      return () => clearTimeout(t);
    }
  }, [invalidateKey]);
  // The basemap layer. No longer an L.TileLayer: CARTO's raster CDN watermarks anonymous
  // traffic, so the basemap is now their VECTOR style rendered by maplibre-gl-leaflet into a
  // canvas inside Leaflet's tile pane. Still a plain L.Layer as far as this component cares.
  const tileRef = useRef<L.Layer | null>(null);
  const trackedRef = useRef<globalThis.Map<string, Tracked>>(new globalThis.Map());
  const bboxRectRef = useRef<L.Rectangle | null>(null);
  const bboxKeyRef = useRef<string>("");
  const ringRef = useRef<L.Circle | null>(null);
  const trailGroupRef = useRef<L.LayerGroup | null>(null);
  const trailPtsRef = useRef<TrailPoint[]>([]);
  const routeGroupRef = useRef<L.LayerGroup | null>(null);
  const searchTrackGroupRef = useRef<L.LayerGroup | null>(null);
  const acTrailGroupRef = useRef<L.LayerGroup | null>(null);
  const satTrackGroupRef = useRef<L.LayerGroup | null>(null);
  const airportGroupRef = useRef<L.LayerGroup | null>(null);
  const areaBoxGroupRef = useRef<L.LayerGroup | null>(null);   // D4 area-search box
  const vesselGroupRef = useRef<L.LayerGroup | null>(null);
  const vesselMarkersRef = useRef<globalThis.Map<string, L.Marker>>(new globalThis.Map());
  const zoneGroupRef = useRef<L.LayerGroup | null>(null);
  const zoneRectsRef = useRef<globalThis.Map<string, L.Rectangle>>(new globalThis.Map());
  const gnssGroupRef = useRef<L.LayerGroup | null>(null);
  const gnssMarkersRef = useRef<globalThis.Map<string, L.CircleMarker>>(new globalThis.Map());
  const darkGroupRef = useRef<L.LayerGroup | null>(null);
  const darkMarkersRef = useRef<globalThis.Map<string, L.CircleMarker>>(new globalThis.Map());
  const satGroupRef = useRef<L.LayerGroup | null>(null);
  const satMarkersRef = useRef<globalThis.Map<number, L.Marker>>(new globalThis.Map());
  const satRecsRef = useRef<SatRec[]>([]);
  const satTimerRef = useRef<number>(0);
  const rafRef = useRef<number>(0);
  const lastSnapRef = useRef<number | null>(null);
  const intervalEmaRef = useRef<number>(15_000);
  const tileFailsRef = useRef<number>(0);
  const [tileWarning, setTileWarning] = useState(false);
  const [mapReady, setMapReady] = useState(true);
  const selectedRef = useRef(selected);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onSelectVesselRef = useRef(onSelectVessel);
  onSelectVesselRef.current = onSelectVessel;
  const selectedVesselRef = useRef(selectedVessel);
  selectedVesselRef.current = selectedVessel;
  const onSelectSatRef = useRef(onSelectSat);
  onSelectSatRef.current = onSelectSat;
  const selectedSatRef = useRef(selectedSat);
  selectedSatRef.current = selectedSat;
  const milSetRef = useRef(milSet);
  milSetRef.current = milSet;
  const adsbSetRef = useRef(adsbSet);
  adsbSetRef.current = adsbSet;
  /* DASH-2g″ — read from the three per-layer paint sites, each of which runs on its own
   * clock (aircraft on the 15 Hz glyph pass, vessels on the 30 s poll, satellites on the
   * 1 Hz SGP4 tick). A ref, not a dep: those sites must NOT re-subscribe when the panel
   * changes, they only need the current answer the next time they paint. */
  const pickRef = useRef(panelPick);
  pickRef.current = panelPick;
  /** does this key, in this layer, carry the open panel's payload? */
  const isPick = (kind: "aircraft" | "vessel" | "satellite", key: string): boolean =>
    pickRef.current?.kind === kind && pickRef.current.key === key;

  // Theme tokens for map vectors are read from the host (carries the basemap's
  // data-theme), so trails/route/ring/bbox track the map — before mount, or if
  // the ref is momentarily null, fall back to the document (fixed UI theme).
  const themeEl = (): Element => hostRef.current ?? document.documentElement;
  const readVar = (name: string): string => cssVar(themeEl(), name);

  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- init once ---------- */
  useEffect(() => {
    if (!hostRef.current) return;
    let map: L.Map;
    try {
      map = L.map(hostRef.current, {
        zoomControl: true,
        attributionControl: true,
        preferCanvas: true,
      });
      map.attributionControl.setPrefix("");
      map.attributionControl.setPosition("bottomleft");
      // ⚠ Added HERE, not as a layer option: maplibre-gl-leaflet ignores Leaflet's
      // `attribution` option (measured — the string never reached the control), and the
      // basemap layer is rebuilt on every theme flip, which would re-add this line each time.
      // Registering it once against the map keeps exactly one copy. The "© CARTO, ©
      // OpenStreetMap contributors" credit beside it comes from the style's own source.
      map.attributionControl.addAttribution(TILE_ATTRIBUTION);
    } catch {
      setMapReady(false);
      return;
    }
    map.setView([27, 44], 5); // Middle East default; payload bbox re-fits on arrival
    map.on("click", () => {
      onSelectRef.current(null);
      onSelectVesselRef.current(null);
      onSelectSatRef.current(null);
    });
    mapRef.current = map;
    trailGroupRef.current = L.layerGroup().addTo(map);
    routeGroupRef.current = L.layerGroup().addTo(map);
    searchTrackGroupRef.current = L.layerGroup().addTo(map);  // under the vessel marker
    acTrailGroupRef.current = L.layerGroup().addTo(map);      // E2 aircraft search trail
    satTrackGroupRef.current = L.layerGroup().addTo(map);     // E3 satellite ground-track
    airportGroupRef.current = L.layerGroup().addTo(map);      // E4 airport marker
    areaBoxGroupRef.current = L.layerGroup().addTo(map);      // D4 area-search box
    vesselGroupRef.current = L.layerGroup().addTo(map);
    zoneGroupRef.current = L.layerGroup().addTo(map);   // choropleth UNDER the gnss markers
    gnssGroupRef.current = L.layerGroup().addTo(map);
    darkGroupRef.current = L.layerGroup().addTo(map);
    satGroupRef.current = L.layerGroup().addTo(map);
    return () => {
      cancelAnimationFrame(rafRef.current);
      window.clearInterval(satTimerRef.current);
      map.remove();
      mapRef.current = null;
      trackedRef.current.clear();
    };
  }, []);

  /* ---------- tiles per theme, with failure tracking ---------- */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    tileRef.current?.remove();
    tileFailsRef.current = 0;
    setTileWarning(false);
    // ⛔ WEBGL IS CHECKED FIRST, AND THE CONSTRUCTOR IS WRAPPED, BECAUSE MAPLIBRE'S FAILURE
    // MODE IS A THROW THAT TAKES THE WHOLE APP WITH IT. Measured with WebGL disabled: the
    // constructor threw, the exception unwound through React, and NOTHING mounted — no
    // .main, no .map-host, zero markers. A blank radar screen, not a missing basemap. Falling
    // back to the (watermarked) raster tiles keeps the application usable for a viewer whose
    // browser blocks WebGL.
    let layer: L.Layer;
    let usedVector = false;
    if (hasWebGL()) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        layer = (L as any).maplibreGL({
          style: TILE_URL[theme],
          // Match the raster layer this replaces: the app never zoomed past 12, and letting
          // MapLibre go deeper would silently change what the zoom control can reach.
          maxZoom: 12,
          interactive: false,
        }) as L.Layer;
        usedVector = true;
      } catch {
        layer = L.tileLayer(RASTER_FALLBACK_URL[theme], { maxZoom: 12, attribution: RASTER_FALLBACK_ATTRIBUTION });
      }
    } else {
      layer = L.tileLayer(RASTER_FALLBACK_URL[theme], { maxZoom: 12, attribution: RASTER_FALLBACK_ATTRIBUTION });
    }

    // ⛔ addTo() FIRST, THEN getMaplibreMap(). The plugin builds its internal MapLibre map in
    // onAdd, so before the layer is on the map getMaplibreMap() returns undefined — and with
    // optional chaining every handler below silently becomes a no-op. That is exactly how the
    // first attempt shipped: the map rendered, the console was clean, and both the failure
    // counter and the water repaint were dead. Order is the whole fix.
    layer.addTo(map);
    tileRef.current = layer;

    if (!usedVector) {
      // The raster path keeps the original tile-level failure counter, which still applies.
      const rl = layer as L.TileLayer;
      rl.on("tileerror", () => {
        tileFailsRef.current += 1;
        if (tileFailsRef.current >= 3) setTileWarning(true);
      });
      rl.on("tileload", () => {
        tileFailsRef.current = 0;
        setTileWarning(false);
      });
      return;
    }

    const gl = (layer as unknown as {
      getMaplibreMap?: () => {
        on: (e: string, cb: () => void) => void;
        getLayer?: (id: string) => unknown;
        setPaintProperty?: (id: string, prop: string, val: string) => void;
      };
    }).getMaplibreMap?.();

    // ⚠ THE OLD tileerror/tileload FAILURE COUNTER DOES NOT PORT to the vector path, and
    // silently dropping it would be worse than saying so. Those are L.TileLayer events; a
    // MapLibre canvas emits neither, so tileWarning is fed from MapLibre's own error event,
    // which covers a failed style, failed glyphs and failed tiles alike. The threshold stays
    // at 3 so one transient miss still does not raise the banner.
    gl?.on("error", () => {
      tileFailsRef.current += 1;
      if (tileFailsRef.current >= 3) setTileWarning(true);
    });
    gl?.on("idle", () => {
      tileFailsRef.current = 0;
      setTileWarning(false);
    });

    // Force the style back to the raster basemap's colours — see STYLE_OVERRIDES in
    // lib/theme.ts for every measured value. ⚠ Hooked to "styledata" rather than "load": the
    // style is cached across theme flips, so "load" may never fire again and the map would
    // keep CARTO's own palette. styledata fires on every style change and the writes are
    // idempotent, so running it repeatedly costs nothing.
    const applyOverrides = () => {
      for (const [layerId, prop, value] of STYLE_OVERRIDES[theme]) {
        try {
          if (gl?.getLayer?.(layerId)) gl.setPaintProperty?.(layerId, prop, value);
        } catch {
          /* a style revision that renames or drops a layer must not break the map — that one
           * property simply keeps CARTO's own value. Per-layer, so one rename cannot cost the
           * rest of the overrides. */
        }
      }
    };
    gl?.on("styledata", applyOverrides);
    applyOverrides();
    // theme changed: vectors hold literal colors — refresh them
    redrawTrail();
    redrawRoute();
    updateSelectionDecor();
    if (bboxRectRef.current) bboxRectRef.current.setStyle({ color: readVar("--accent") });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  /* ---------- bbox rectangle ---------- */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !bbox) return;
    const key = `${bbox.lamin},${bbox.lomin},${bbox.lamax},${bbox.lomax}`;
    if (key === bboxKeyRef.current) return;
    bboxKeyRef.current = key;
    bboxRectRef.current?.remove();
    bboxRectRef.current = L.rectangle(
      [[bbox.lamin, bbox.lomin], [bbox.lamax, bbox.lomax]],
      { color: readVar("--accent"), weight: 1, opacity: 0.35, fill: false, dashArray: "4 6" },
    ).addTo(map);
    map.fitBounds([[bbox.lamin, bbox.lomin], [bbox.lamax, bbox.lomax]], { padding: [24, 24] });
  }, [bbox]);

  /* ---------- clear the aircraft layer when there are no aircraft ----------
   * The payload-apply effect below early-returns on a null snapshot_time, so
   * it can't prune. When we switch to the maritime tab (states=[], snapshot
   * null) this drops every aircraft/military marker + its trail/route/ring so
   * the map shows vessels only. */
  useEffect(() => {
    if (states.length > 0) return;
    for (const [, t] of trackedRef.current) t.marker.remove();
    trackedRef.current.clear();
    trailGroupRef.current?.clearLayers();
    routeGroupRef.current?.clearLayers();
    trailPtsRef.current = [];
    ringRef.current?.remove();
    ringRef.current = null;
  }, [states]);

  /* ---------- payload apply (replace wholesale) ---------- */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || snapshotTime == null) return;

    const now = performance.now();
    if (lastSnapRef.current != null && snapshotTime > lastSnapRef.current) {
      const dt = (snapshotTime - lastSnapRef.current) * 1000;
      if (dt > 500 && dt < 300_000) {
        intervalEmaRef.current = intervalEmaRef.current * 0.5 + Math.min(dt, ANIM_DUR_MAX_MS) * 0.5;
      }
    }
    lastSnapRef.current = snapshotTime;
    const animDur = reducedMotion ? 0 : Math.min(intervalEmaRef.current, ANIM_DUR_MAX_MS);

    const tracked = trackedRef.current;
    const seen = new Set<string>();

    for (const s of states) {
      if (s.latitude == null || s.longitude == null) continue;
      seen.add(s.icao24);
      const existing = tracked.get(s.icao24);
      if (existing) {
        updateAircraft(existing, s, now, animDur);
      } else {
        tracked.set(s.icao24, createAircraft(map, s));
      }
      // live extension of the selected aircraft's history trail
      if (s.icao24 === selectedRef.current) {
        appendTrailFix([s.latitude, s.longitude, s.baro_altitude]);
      }
    }
    // prune everything not in this payload
    for (const [id, t] of tracked) {
      if (!seen.has(id)) {
        t.marker.remove();
        tracked.delete(id);
      }
    }
    ensureAnimLoop();
    updateSelectionDecor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [states, snapshotTime]);

  /* ---------- selection decor ---------- */
  useEffect(() => {
    selectedRef.current = selected;
    for (const [id, t] of trackedRef.current) {
      applyGlyph(t, id === selected);
    }
    if (!selected) {
      trailPtsRef.current = [];
      trailGroupRef.current?.clearLayers();
    }
    updateSelectionDecor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  /* ---------- history trail for the selected aircraft (server truth) ---------- */
  useEffect(() => {
    trailPtsRef.current = historyTrail ? [...historyTrail] : [];
    redrawTrail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyTrail]);

  /* ---------- standing route line (origin → destination) ---------- */
  useEffect(() => {
    redrawRoute();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, selected]);

  /* ---------- maritime overlay: keyed vessel markers ----------
   * A separate layer from aircraft: MMSI-keyed, ship glyph, click→popup.
   * Vessels don't interpolate (slow + 30 s cadence), so this is a simple
   * keyed update (move existing, add new, prune absent) — no rAF loop. */
  useEffect(() => {
    const group = vesselGroupRef.current;
    if (!group) return;
    const markers = vesselMarkersRef.current;
    const seen = new Set<string>();
    for (const v of vessels) {
      if (v.lat == null || v.lon == null) continue;
      // Composite key: MMSI for AIS, ship_id for MarineTraffic — the two identity
      // spaces share this layer without colliding.
      const key = vesselKey(v);
      seen.add(key);
      // rotation baked into the icon HTML so it applies even before the
      // element is in the DOM (heading, else course, else north)
      const sel = key === selectedVessel;
      const rot = Math.round(v.heading ?? v.course ?? 0);
      const icon = L.divIcon({
        // ⚠ the `pick` ring must be baked into the className here, not only applied by the
        // effect below: this poll REPLACES the icon every 30 s, so a class the effect had
        // set on the old element would vanish with it.
        className: `vessel-glyph${sel ? " sel" : ""}${isPick("vessel", key) ? " pick" : ""}`,
        iconSize: [0, 0],
        html: vesselIconHtml(rot, sel, v.source === "marinetraffic", isStale(v.timestamp)),
      });
      let m = markers.get(key);
      if (m) {
        m.setLatLng([v.lat, v.lon]);
        m.setIcon(icon);
      } else {
        m = L.marker([v.lat, v.lon], { icon, keyboard: false });
        m.on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          onSelectVesselRef.current(key);
        });
        m.addTo(group);
        markers.set(key, m);
      }
    }
    for (const [key, m] of markers) {
      if (!seen.has(key)) {
        group.removeLayer(m);
        markers.delete(key);
      }
    }
  }, [vessels, selectedVessel]);

  /* ---------- pan to a newly selected vessel (not on every poll) ---------- */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedVessel) return;
    const v = vessels.find((x) => vesselKey(x) === selectedVessel);
    if (v && v.lat != null && v.lon != null) {
      map.panTo([v.lat, v.lon], { animate: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVessel]);

  /* ---------- search tab: the selected vessel's full track ----------
   * Quality-coloured polyline over the fix sequence (E1). Contiguous same-class
   * segments coalesce into one polyline (cheap even for long tracks); a segment
   * is "bad" (crimson + dashed, the GNSS hue) when EITHER endpoint is suspect/
   * implausible, else the vessel hue — so a spoofed/jammed run is visible in the
   * track, not hidden. A coordinate gap breaks the run. On a new track the map
   * fits to its extent (centre/zoom to the whole voyage). */
  useEffect(() => {
    const group = searchTrackGroupRef.current;
    const map = mapRef.current;
    if (!group) return;
    group.clearLayers();
    if (!vesselTrack || vesselTrack.length === 0) return;
    const good = readVar("--glyph-vessel") || "#22d3ee";
    const bad = readVar("--glyph-gnss") || "#ff4d6d";
    const isBad = (p: VesselTrackPoint) =>
      p.position_quality === "suspect" || p.position_quality === "implausible";
    const valid = (p: VesselTrackPoint) => p.lat != null && p.lon != null;

    type Run = { bad: boolean; pts: [number, number][] };
    const runs: Run[] = [];
    let cur: Run | null = null;
    const bounds = L.latLngBounds([]);
    for (let i = 1; i < vesselTrack.length; i++) {
      const a = vesselTrack[i - 1], b = vesselTrack[i];
      if (!valid(a) || !valid(b)) { cur = null; continue; }
      const segBad = isBad(a) || isBad(b);
      const pa: [number, number] = [a.lat as number, a.lon as number];
      const pb: [number, number] = [b.lat as number, b.lon as number];
      bounds.extend(pa); bounds.extend(pb);
      if (cur && cur.bad === segBad) {
        cur.pts.push(pb);
      } else {
        cur = { bad: segBad, pts: [pa, pb] };
        runs.push(cur);
      }
    }
    if (runs.length === 0) {
      // single valid fix (no segment) — still fit to the lone point
      for (const p of vesselTrack) if (valid(p)) bounds.extend([p.lat as number, p.lon as number]);
    }
    for (const r of runs) {
      group.addLayer(
        L.polyline(r.pts, {
          color: r.bad ? bad : good,
          weight: r.bad ? 3 : 2.5,
          opacity: r.bad ? 0.95 : 0.8,
          dashArray: r.bad ? "5 5" : undefined,
          interactive: false,
        }),
      );
    }
    if (map && bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 11 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vesselTrack, theme]);

  /* ---------- search tab: the selected aircraft's track (E2) ----------
   * Altitude-coloured polyline (the SAME teal→gold ramp as the live history trail
   * — air has no position-quality layer, so NOT the maritime crimson) + an
   * endpoint highlight at the head. A single-point trail is the pre-expand anchor
   * (endpoint + panTo); a multi-point trail fits the map to the whole track. */
  useEffect(() => {
    const group = acTrailGroupRef.current;
    const map = mapRef.current;
    if (!group) return;
    group.clearLayers();
    if (!aircraftTrail || aircraftTrail.length === 0) return;
    const valid = aircraftTrail.filter((p) => p[0] != null && p[1] != null);
    if (valid.length === 0) return;
    const altColor = makeAltColor(themeEl());
    for (let i = 1; i < valid.length; i++) {
      const a = valid[i - 1], b = valid[i];
      group.addLayer(
        L.polyline([[a[0], a[1]], [b[0], b[1]]], {
          color: altColor(b[2] ?? a[2]), weight: 2.5, opacity: 0.85, interactive: false,
        }),
      );
    }
    const head = valid[valid.length - 1];
    const sel = readVar("--select");
    group.addLayer(
      L.circleMarker([head[0], head[1]], {
        radius: 6, color: sel, weight: 2, fillColor: sel, fillOpacity: 0.5, interactive: false,
      }),
    );
    if (map) {
      if (valid.length >= 2) {
        const b = L.latLngBounds(valid.map((p) => [p[0], p[1]] as [number, number]));
        if (b.isValid()) map.fitBounds(b, { padding: [40, 40], maxZoom: 11 });
      } else {
        map.panTo([head[0], head[1]], { animate: true });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aircraftTrail, theme]);

  /* ---------- search tab: the selected satellite's ground-track (E3) ----------
   * Violet (--glyph-sat) polyline of the SGP4 sub-point over one orbital period,
   * split at antimeridian crossings so a longitude wrap doesn't draw a line across
   * the whole map. A LEO traces a global sinusoid; a GEO is a tiny near-stationary
   * loop — both honest. The live glyph is the reused satellite layer; here we only
   * add the track + fit to it (maxZoom capped so a GEO loop isn't over-zoomed). */
  useEffect(() => {
    const group = satTrackGroupRef.current;
    const map = mapRef.current;
    if (!group) return;
    group.clearLayers();
    if (!satelliteTrack || satelliteTrack.length < 2) return;
    const color = readVar("--glyph-sat") || "#c4b5fd";
    const segs: [number, number][][] = [[satelliteTrack[0]]];
    for (let i = 1; i < satelliteTrack.length; i++) {
      const cur = satelliteTrack[i], prev = satelliteTrack[i - 1];
      if (Math.abs(cur[1] - prev[1]) > 180) segs.push([cur]);   // antimeridian wrap
      else segs[segs.length - 1].push(cur);
    }
    for (const s of segs) {
      if (s.length >= 2) {
        group.addLayer(
          L.polyline(s, { color, weight: 2, opacity: 0.8, dashArray: "4 4", interactive: false }),
        );
      }
    }
    const bounds = L.latLngBounds(satelliteTrack.map((p) => [p[0], p[1]] as [number, number]));
    if (map && bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 5 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [satelliteTrack, theme]);

  /* ---------- search tab: the selected airport marker (E4) ----------
   * A hollow accent ring + ICAO label at the airport; the landed aircraft's track
   * reuses aircraftTrail. Pans to the airport on change (the track's own fitBounds
   * takes over when a landing is drilled). */
  useEffect(() => {
    const group = airportGroupRef.current;
    const map = mapRef.current;
    if (!group) return;
    group.clearLayers();
    if (!airport) return;
    const color = readVar("--accent") || "#d9ab4a";
    group.addLayer(
      L.circleMarker([airport.lat, airport.lon], {
        radius: 8, color, weight: 2, opacity: 0.9, fill: false, interactive: false,
      }).bindTooltip(esc(airport.label), { permanent: true, direction: "top", offset: [0, -8], className: "ap-tip" }),
    );
    group.addLayer(
      L.circleMarker([airport.lat, airport.lon], {
        radius: 2, color, weight: 2, fillColor: color, fillOpacity: 1, interactive: false,
      }),
    );
    if (map) map.panTo([airport.lat, airport.lon], { animate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [airport, theme]);

  /* ---------- D4 area-search: draw-a-box (native events, no plugin) ----------
   * While drawMode is on, map dragging is disabled and a drag draws a live
   * L.rectangle; mouseup finalizes and reports [lat1,lon1,lat2,lon2]. This is the
   * whole draw tooling — the Leaflet-minimal discipline (no leaflet-draw). */
  useEffect(() => {
    const map = mapRef.current;
    const group = areaBoxGroupRef.current;
    if (!map || !group) return;
    if (!drawMode) return;
    map.dragging.disable();
    map.getContainer().style.cursor = "crosshair";
    let start: L.LatLng | null = null;
    let rect: L.Rectangle | null = null;
    const color = readVar("--accent") || "#d9ab4a";
    const down = (e: L.LeafletMouseEvent) => { start = e.latlng; group.clearLayers(); rect = null; };
    const move = (e: L.LeafletMouseEvent) => {
      if (!start) return;
      const b = L.latLngBounds(start, e.latlng);
      if (rect) rect.setBounds(b);
      else { rect = L.rectangle(b, { color, weight: 1.5, dashArray: "5 5", fillOpacity: 0.06, interactive: false }); rect.addTo(group); }
    };
    const up = (e: L.LeafletMouseEvent) => {
      if (!start) return;
      const b = L.latLngBounds(start, e.latlng);
      start = null;
      onBoxDrawnRef.current([b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]);
    };
    map.on("mousedown", down);
    map.on("mousemove", move);
    map.on("mouseup", up);
    return () => {
      map.off("mousedown", down); map.off("mousemove", move); map.off("mouseup", up);
      map.dragging.enable();
      map.getContainer().style.cursor = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawMode]);

  /* ---------- D4 area-search: the finalized query box (persistent) ---------- */
  useEffect(() => {
    const group = areaBoxGroupRef.current;
    const map = mapRef.current;
    if (!group) return;
    if (drawMode) return;            // while drawing, the live rect owns the group
    group.clearLayers();
    if (!areaBox) return;
    const color = readVar("--accent") || "#d9ab4a";
    const b = L.latLngBounds([areaBox[0], areaBox[1]], [areaBox[2], areaBox[3]]);
    L.rectangle(b, { color, weight: 1.5, dashArray: "5 5", fillOpacity: 0.05, interactive: false }).addTo(group);
    if (map && b.isValid()) map.fitBounds(b, { padding: [30, 30] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [areaBox, drawMode, theme]);

  /* ---------- jamming-zone choropleth: crimson L.rectangle cells ----------
   * Same overlay concept as the gnss markers, drawn UNDER them. The endpoint returns
   * per cell x window; collapse to ONE rectangle per cell at its PEAK vessel count
   * (cleanest choropleth), fill graduated by that peak. Keyed by cell; move/add/prune.
   * Broadcast = solid edge, affected = dashed — they mean different things. */
  useEffect(() => {
    const group = zoneGroupRef.current;
    if (!group) return;
    const rects = zoneRectsRef.current;
    const color = readVar("--glyph-gnss") || "#ff4d6d";
    // collapse per cell -> the peak-vessel window
    const byCell = new globalThis.Map<string, ZoneCell>();
    for (const z of zones) {
      const k = `${z.clat},${z.clon}`;
      const cur = byCell.get(k);
      if (!cur || z.distinct_vessels > cur.distinct_vessels) byCell.set(k, z);
    }
    let maxV = 1;
    for (const z of byCell.values()) maxV = Math.max(maxV, z.distinct_vessels);
    const seen = new Set<string>();
    for (const [k, z] of byCell) {
      seen.add(k);
      const bounds: L.LatLngBoundsExpression = [[z.lat_min, z.lon_min], [z.lat_max, z.lon_max]];
      const style = zoneStyle(z.distinct_vessels, maxV, z.variant, color);
      let r = rects.get(k);
      if (r) {
        r.setBounds(bounds);
        r.setStyle(style);
        r.setPopupContent(zonePopupHtml(z, z.variant));
      } else {
        r = L.rectangle(bounds, style).bindPopup(zonePopupHtml(z, z.variant));
        r.addTo(group);
        rects.set(k, r);
      }
    }
    for (const [k, r] of rects) {
      if (!seen.has(k)) {
        group.removeLayer(r);
        rects.delete(k);
      }
    }
  }, [zones]);

  /* ---------- GNSS-anomaly overlay: keyed crimson point markers ----------
   * Maritime-tab overlay (toggleable). Each episode is a circleMarker at its first
   * implausible fix (the decoy landing point for decoy episodes), coloured by the one
   * GNSS hue with subtype intensity. Keyed by mmsi+start_ts (a vessel has many
   * episodes); move/add/prune like the vessel layer. Click → quality-evidence popup.
   * Canvas-rendered (preferCanvas) so the dense Amman decoy cluster stays cheap. */
  useEffect(() => {
    const group = gnssGroupRef.current;
    if (!group) return;
    const markers = gnssMarkersRef.current;
    const color = readVar("--glyph-gnss") || "#ff4d6d";
    const seen = new Set<string>();
    for (const a of gnssAnomalies) {
      if (a.start_lat == null || a.start_lon == null) continue;
      const key = `${a.mmsi}|${a.start_ts}`;
      seen.add(key);
      const style = gnssStyle(a.gnss_type, color);
      let m = markers.get(key);
      if (m) {
        m.setLatLng([a.start_lat, a.start_lon]);
        m.setStyle(style);
        m.setRadius(style.radius as number);
      } else {
        m = L.circleMarker([a.start_lat, a.start_lon], style).bindPopup(gnssPopupHtml(a));
        m.addTo(group);
        markers.set(key, m);
      }
    }
    for (const [key, m] of markers) {
      if (!seen.has(key)) {
        group.removeLayer(m);
        markers.delete(key);
      }
    }
  }, [gnssAnomalies]);

  /* ---------- dark-vessel markers (GFW satellite AIS) ----------
   * A violet ring at each gap position (where a vessel went silent). Intentional
   * disabling reads brighter/larger. An ON-LAND (suspect/spoofed) position is drawn
   * in an orange DASHED ring instead — a distinct integrity signal, not a location.
   * Keyed by mmsi+start; a LEAD, not proof. */
  useEffect(() => {
    const group = darkGroupRef.current;
    if (!group) return;
    const markers = darkMarkersRef.current;
    const seaColor = readVar("--glyph-sat") || "#c4b5fd";
    const landColor = readVar("--glyph-darkland") || "#fb923c";
    const seen = new Set<string>();
    for (const d of darkVessels) {
      if (d.lat == null || d.lon == null) continue;
      const key = `${d.mmsi}|${d.start}`;
      seen.add(key);
      const color = d.on_land ? landColor : seaColor;
      const style: L.CircleMarkerOptions = {
        color, weight: d.on_land ? 2 : 1.5, fillColor: color, interactive: true,
        dashArray: d.on_land ? "3 3" : undefined,
        radius: d.intentional ? 6 : 4.5,
        opacity: d.on_land ? 0.95 : d.intentional ? 0.95 : 0.6,
        fillOpacity: d.on_land ? 0.2 : d.intentional ? 0.55 : 0.25,
      };
      const label = [
        `<b>${esc(d.name || `MMSI ${d.mmsi ?? "?"}`)}</b>`,
        d.flag ? `<span dir="ltr">${esc(d.flag)}</span>` : "",
        d.duration_h != null ? `${Math.round(d.duration_h)} h dark` : "",
        d.intentional ? "likely intentional" : "",
        d.on_land ? `<b style="color:${landColor}">${T.dark.onLandPop}</b>` : "",
        `<i>${T.dark.caveatShort}</i>`,
      ].filter(Boolean).join("<br>");
      let m = markers.get(key);
      if (m) {
        m.setLatLng([d.lat, d.lon]);
        m.setStyle(style);
        m.setRadius(style.radius as number);
      } else {
        m = L.circleMarker([d.lat, d.lon], style).bindPopup(`<div class="gnss-pop">${label}</div>`);
        m.addTo(group);
        markers.set(key, m);
      }
    }
    for (const [key, m] of markers) {
      if (!seen.has(key)) {
        group.removeLayer(m);
        markers.delete(key);
      }
    }
  }, [darkVessels]);

  /* ---------- satellite layer: SGP4-propagated locally at 1 Hz ----------
   * Positions are computed in the browser from TLEs — no server polling. The
   * satrec set rebuilds when elements change; a 1 Hz timer moves the markers
   * (curated groups are ≤ hundreds, so per-tick propagation is cheap). */
  useEffect(() => {
    const group = satGroupRef.current;
    if (!group) return;
    window.clearInterval(satTimerRef.current);
    satRecsRef.current = satellites.map(makeSatRec).filter((s): s is SatRec => s !== null);

    const markers = satMarkersRef.current;
    const live = new Set(satRecsRef.current.map((s) => s.norad));
    for (const [norad, m] of markers) {
      if (!live.has(norad)) {
        group.removeLayer(m);
        markers.delete(norad);
      }
    }
    if (satRecsRef.current.length === 0) return;

    const tick = () => {
      const now = new Date();
      for (const sat of satRecsRef.current) {
        const fix = propagate(sat, now);
        if (!fix) continue;
        const sel = sat.norad === selectedSatRef.current;
        const pick = isPick("satellite", String(sat.norad));
        let m = markers.get(sat.norad);
        if (m) {
          m.setLatLng([fix.lat, fix.lon]);
          const el = m.getElement();
          if (el) { el.classList.toggle("sel", sel); el.classList.toggle("pick", pick); }
        } else {
          m = L.marker([fix.lat, fix.lon], {
            icon: L.divIcon({ className: `satellite-glyph${sel ? " sel" : ""}${pick ? " pick" : ""}`,
                              iconSize: [0, 0], html: satIconHtml(sel) }),
            keyboard: false,
          });
          m.on("click", (e) => {
            L.DomEvent.stopPropagation(e);
            onSelectSatRef.current(sat.norad);
          });
          m.addTo(group);
          markers.set(sat.norad, m);
        }
      }
    };
    tick();
    satTimerRef.current = window.setInterval(tick, 1000);
    return () => window.clearInterval(satTimerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [satellites]);

  /* ---------- selected satellite: emphasize its glyph + pan-to ---------- */
  useEffect(() => {
    const markers = satMarkersRef.current;
    for (const [norad, m] of markers) {
      const sel = norad === selectedSat;
      const el = m.getElement();
      if (el) {
        el.classList.toggle("sel", sel);
        const g = el.querySelector<SVGElement>(".sg");
        if (g) g.outerHTML = satIconHtml(sel);
      }
    }
    if (selectedSat != null) {
      const m = markers.get(selectedSat);
      const map = mapRef.current;
      if (m && map) map.panTo(m.getLatLng(), { animate: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSat]);

  /* ---------- DASH-2g″: ring the glyph whose payload the left panel is showing ----------
   * Each layer already bakes `pick` into its own paint, which covers every repaint that
   * layer drives. This covers the other direction — the PANEL changing between those
   * repaints — so the ring lands on the click instead of up to 30 s later, on the poll.
   *
   * ⛔ It visits every marker in all three maps, not just the new one, because the job is
   * as much to REMOVE the ring from whatever wore it before. A mark that outlived the panel
   * it describes would claim the payload on screen belongs to the wrong glyph — and closing
   * the panel (panelPick → null) is exactly that case, handled by the same sweep. */
  useEffect(() => {
    for (const [id, t] of trackedRef.current) {
      t.marker.getElement()?.classList.toggle("pick", isPick("aircraft", id));
    }
    for (const [key, m] of vesselMarkersRef.current) {
      m.getElement()?.classList.toggle("pick", isPick("vessel", key));
    }
    for (const [norad, m] of satMarkersRef.current) {
      m.getElement()?.classList.toggle("pick", isPick("satellite", String(norad)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelPick]);

  /* ================= engine internals ================= */

  function createAircraft(map: L.Map, s: StateVector): Tracked {
    const icon = L.divIcon({ className: "ac-glyph", html: GLYPH_SVG, iconSize: [0, 0] });
    const marker = L.marker([s.latitude!, s.longitude!], { icon, keyboard: false });
    marker.on("click", (e) => {
      L.DomEvent.stopPropagation(e);
      onSelectRef.current(s.icao24);
    });
    marker.addTo(map);
    const t: Tracked = {
      marker,
      glyphEl: null,
      state: s,
      fromLat: s.latitude!, fromLng: s.longitude!, fromTrack: s.true_track ?? 0,
      toLat: s.latitude!, toLng: s.longitude!, toTrack: s.true_track ?? 0,
      animStart: 0, animDur: 0,
    };
    applyGlyph(t, s.icao24 === selectedRef.current);
    return t;
  }

  function updateAircraft(t: Tracked, s: StateVector, now: number, animDur: number): void {
    const cur = t.marker.getLatLng();
    t.fromLat = cur.lat;
    t.fromLng = cur.lng;
    t.fromTrack = displayedTrack(t, now);
    t.toLat = s.latitude!;
    t.toLng = s.longitude!;
    t.toTrack = s.true_track ?? t.toTrack;
    t.animStart = now;
    t.animDur = animDur;
    t.state = s;
    if (animDur === 0) {
      t.marker.setLatLng([t.toLat, t.toLng]);
    }
    applyGlyph(t, s.icao24 === selectedRef.current);
  }

  function displayedTrack(t: Tracked, now: number): number {
    if (t.animDur <= 0) return t.toTrack;
    const f = Math.min((now - t.animStart) / t.animDur, 1);
    return t.fromTrack + shortestArc(t.fromTrack, t.toTrack) * f;
  }

  function applyGlyph(t: Tracked, isSelected: boolean): void {
    const el = t.marker.getElement();
    if (!el) return;
    el.classList.toggle("ground", t.state.on_ground);
    el.classList.toggle("sel", isSelected);
    el.classList.toggle("pick", isPick("aircraft", t.state.icao24));
    el.classList.toggle("emergency", EMERGENCY_SQUAWKS.has(t.state.squawk));
    el.classList.toggle("mil", milSetRef.current.has(t.state.icao24));
    el.classList.toggle("adsb", adsbSetRef.current.has(t.state.icao24));
    const g = el.querySelector<HTMLElement>(".g");
    if (!g) return;
    t.glyphEl = g;
    const size = isSelected ? 28 : glyphSize(t.state.baro_altitude);
    g.style.width = `${size}px`;
    g.style.height = `${size}px`;
    g.style.opacity = String(isSelected ? 1 : glyphOpacity(t.state.baro_altitude));
    g.style.marginLeft = `${-size / 2}px`;
    g.style.marginTop = `${-size / 2}px`;
    g.style.transform = `rotate(${Math.round(t.toTrack)}deg)`;
  }

  /* -- selected trail: altitude-colored segments -- */

  function drawSegment(a: TrailPoint, b: TrailPoint, altColor: (alt: number | null) => string): void {
    const group = trailGroupRef.current;
    if (!group) return;
    const alt = b[2] ?? a[2];
    group.addLayer(
      L.polyline(
        [[a[0], a[1]], [b[0], b[1]]],
        { color: altColor(alt), weight: 2.5, opacity: 0.85, interactive: false },
      ),
    );
  }

  function redrawTrail(): void {
    const group = trailGroupRef.current;
    if (!group) return;
    group.clearLayers();
    const pts = trailPtsRef.current;
    if (!selectedRef.current || pts.length < 2) return;
    const altColor = makeAltColor(themeEl());
    for (let i = 1; i < pts.length; i++) drawSegment(pts[i - 1], pts[i], altColor);
  }

  function appendTrailFix(p: TrailPoint): void {
    const pts = trailPtsRef.current;
    const last = pts[pts.length - 1];
    if (last && last[0] === p[0] && last[1] === p[1]) return;
    pts.push(p);
    if (pts.length > SEL_TRAIL_MAX) {
      pts.splice(0, pts.length - SEL_TRAIL_MAX);
      redrawTrail(); // rare: full redraw only when the cap trims history
      return;
    }
    if (last) drawSegment(last, p, makeAltColor(themeEl()));
  }

  /* -- standing route: dashed origin→destination + labeled airport dots -- */

  function redrawRoute(): void {
    const group = routeGroupRef.current;
    if (!group) return;
    group.clearLayers();
    const r = route;
    if (!selectedRef.current || !r) return;
    const o = r.origin;
    const d = r.destination;
    if (o.lat == null || o.lon == null || d.lat == null || d.lon == null) return;
    const color = readVar("--select");
    group.addLayer(
      L.polyline([[o.lat, o.lon], [d.lat, d.lon]], {
        color, weight: 1.5, opacity: 0.35, dashArray: "6 8", interactive: false,
      }),
    );
    for (const [ap, pos] of [[o, [o.lat, o.lon]], [d, [d.lat, d.lon]]] as const) {
      group.addLayer(
        L.circleMarker(pos as [number, number], {
          radius: 4, color, weight: 1.5, opacity: 0.75,
          fillColor: color, fillOpacity: 0.25, interactive: false,
        }).bindTooltip(esc(ap.iata || ap.icao || "?"), {
          permanent: true, direction: "top", offset: [0, -6], className: "ap-tip",
        }),
      );
    }
  }

  function updateSelectionDecor(): void {
    const map = mapRef.current;
    if (!map) return;
    const sel = selectedRef.current ? trackedRef.current.get(selectedRef.current) : null;
    if (!sel) {
      ringRef.current?.remove();
      ringRef.current = null;
      return;
    }
    const pos = sel.marker.getLatLng();
    if (!ringRef.current) {
      ringRef.current = L.circle(pos, {
        radius: RANGE_RING_M,
        color: readVar("--select"),
        weight: 1,
        opacity: 0.4,
        fill: false,
        dashArray: "3 5",
        interactive: false,
      }).addTo(map);
    } else {
      ringRef.current.setLatLng(pos).setStyle({ color: readVar("--select") });
    }
  }

  function ensureAnimLoop(): void {
    cancelAnimationFrame(rafRef.current);
    if (reducedMotion) {
      for (const t of trackedRef.current.values()) {
        t.marker.setLatLng([t.toLat, t.toLng]);
        if (t.glyphEl) t.glyphEl.style.transform = `rotate(${Math.round(t.toTrack)}deg)`;
      }
      return;
    }
    let lastTick = 0;
    const step = () => {
      const now = performance.now();
      if (now - lastTick < ANIM_TICK_MS) {
        rafRef.current = requestAnimationFrame(step);
        return;
      }
      lastTick = now;
      let active = false;
      for (const t of trackedRef.current.values()) {
        if (t.animDur <= 0) continue;
        const f = Math.min((now - t.animStart) / t.animDur, 1);
        const lat = t.fromLat + (t.toLat - t.fromLat) * f;
        const lng = t.fromLng + (t.toLng - t.fromLng) * f;
        t.marker.setLatLng([lat, lng]);
        const track = t.fromTrack + shortestArc(t.fromTrack, t.toTrack) * f;
        if (t.glyphEl) t.glyphEl.style.transform = `rotate(${Math.round(track)}deg)`;
        if (f < 1) active = true;
        else t.animDur = 0;
      }
      const sel = selectedRef.current ? trackedRef.current.get(selectedRef.current) : null;
      if (sel && ringRef.current) ringRef.current.setLatLng(sel.marker.getLatLng());
      if (active) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
  }

  /* ================= render ================= */

  if (!mapReady) {
    return (
      <div className="map-fallback" role="alert">
        {T.map.unavailable}
        <br />
        {T.map.unavailableSub}
      </div>
    );
  }

  return (
    <>
      {/* data-theme on the map subtree only: tiles, glyphs, trails, controls
          recolor to the basemap's day/night; the surrounding UI stays fixed. */}
      <div ref={hostRef} className="map-host" data-theme={theme} aria-label={T.map.aria} />
      {tileWarning && (
        <div className="tile-warning" role="status">
          {T.map.tileWarning}
        </div>
      )}
    </>
  );
}
