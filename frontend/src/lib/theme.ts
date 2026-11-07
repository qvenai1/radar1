/**
 * Theme switching.
 *
 * Two DECOUPLED concerns:
 *  - UI theme (data-theme on <html>): drives every color token in
 *    styles/tokens.css — sidebar, glyphs, charts, chrome. Set once before
 *    first paint by index.html (persisted value, else prefers-color-scheme)
 *    and NOT changed at runtime — the app keeps one coherent identity.
 *  - Map basemap (this module's toggle): swaps ONLY the raster tiles + their
 *    filter. The runtime toggle button lives here so day/night applies to the
 *    map alone; the surrounding UI never repaints.
 * All colors are CSS custom properties in tokens.css — components never see hex.
 */

export type Theme = "dark" | "light";

// UI theme persists under "scope-theme" (written by index.html at boot only).
const MAP_KEY = "scope-map-theme";

/** The fixed UI theme (from <html data-theme>, set before first paint). */
export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

/**
 * Current basemap theme: persisted separately; falls back to the UI theme so a
 * fresh load shows a map that matches the chrome until the user toggles it.
 */
export function currentMapTheme(): Theme {
  try {
    const v = localStorage.getItem(MAP_KEY);
    if (v === "dark" || v === "light") return v;
  } catch {
    /* private mode — fall through to the UI theme */
  }
  return currentTheme();
}

export function setMapTheme(t: Theme): void {
  try {
    localStorage.setItem(MAP_KEY, t);
  } catch {
    /* private mode — basemap choice just won't persist */
  }
}

/** Flip the basemap only (tiles + tile filter); UI chrome is untouched. */
export function toggleMapTheme(): Theme {
  const next: Theme = currentMapTheme() === "dark" ? "light" : "dark";
  setMapTheme(next);
  return next;
}

/**
 * Map basemap per theme — CARTO's own Dark Matter / Voyager, as VECTOR styles.
 *
 * ⛔ THE RASTER ENDPOINTS ARE WATERMARKED AND THE VECTOR ONES ARE NOT. Measured 2026-08-31:
 * every CARTO raster path — `{s}.basemaps.cartocdn.com/dark_all`, `/rastertiles/voyager`, the
 * legacy `cartodb-basemaps-{a,b}.global.ssl.fastly.net`, `tiles.basemaps.cartocdn.com`,
 * `dark_nolabels`, and the `@2x` variants — answers **HTTP 200 with a valid PNG that has
 * `API KEY REQUIRED / carto.com/basemaps/apikey` painted diagonally across it**. Sending a
 * Referer from the app's own origin changes nothing. There is no keyless CARTO raster left.
 *
 * ✅ The VECTOR tiles the same styles are built on are still served openly:
 * `tiles-{a..d}.basemaps.cartocdn.com/vectortiles/carto.streets/v1/{z}/{x}/{y}.mvt` returns
 * 200 with real protobuf (63,965 B for one measured tile). So the watermark is a policy on the
 * raster CDN, not on the cartography — and pointing at the style instead of the pre-rendered
 * PNGs gives back the EXACT map this app has always had, clean, with no key and no signup.
 *
 * ⚠ WHY THIS IS THE SAME MAP AND NOT ANOTHER LOOK-ALIKE. `dark-matter-gl-style` and
 * `voyager-gl-style` are the styles the two raster basemaps above were rendered FROM. This is
 * not a substitute chosen for resembling CARTO; it is CARTO. Two earlier attempts to swap
 * providers (Esri Canvas) were rejected precisely because a different cartography is visible
 * however close the palette gets.
 *
 * ⚠ AND LEAFLET IS STILL THE ENGINE. `maplibre-gl-leaflet` renders this style into a canvas
 * that lives inside Leaflet's own tile pane; every marker, trail, popup, layerGroup, pane and
 * z-index in MapView is untouched Leaflet. The changelog's rejection of MapLibre was about
 * making it the map ENGINE — a second hard dependency plus GPU headroom "the target hardware
 * may not have". Rendering happens in the VIEWER's browser, not on this host, and WebGL was
 * verified working before this was adopted; the engine decision itself is unchanged.
 */
export const TILE_URL: Record<Theme, string> = {
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  light: "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
};

/**
 * Paint properties forced onto the style after it loads, so the vector basemap renders as the
 * RASTER one did. Each entry is [layer id, paint property, value].
 *
 * ⛔ CARTO'S GL STYLES AND THEIR OWN RASTER TILESETS HAVE DRIFTED APART, and every value here
 * is a measured difference, not a preference. All of them were found by sampling the same
 * pixels of the same viewport in both basemaps:
 *
 *   feature                  raster (original)   GL style        what it looked like
 *   water fill               #262626             #2C353C         every sea a pale blue patch
 *   country border           #2b2c2c             #5b5d5d         ~2x too bright
 *   country label            #7b7c7c neutral     #8a9aa3         brighter, and blue-tinted
 *   internal state borders   near-invisible      rgba(103,103,114) dotted lines filling deserts
 *
 * ⚠ AND AN EARLIER MEASUREMENT OF THIS SAID THE OPPOSITE, which is worth recording. Comparing
 * brightness histograms over a land region reported the ORIGINAL as the brighter map — because
 * the watermark the original carries is white text, and it dominated the statistic. The
 * aggregate was measuring the defacement, not the cartography. Cropping the same region from
 * both and looking at it showed the truth immediately. ⛔ Do not tune these from histograms.
 *
 * Light (Voyager) needs only the water fix: its borders and labels already matched.
 */
export const STYLE_OVERRIDES: Record<Theme, ReadonlyArray<readonly [string, string, string]>> = {
  dark: [
    // Land itself. The GL background is #0e0e0e against the raster's #090909 — a small number
    // that covers most of the screen, so it sets how dark the map reads overall.
    ["background", "background-color", "#090909"],
    ["water", "fill-color", "#262626"],
    // Rivers and canals carry the same blue cast as the water fill.
    ["waterway", "line-color", "#262626"],
    // The halo Dark Matter draws around coastlines; blue for the same reason.
    ["boundary_country_outline", "line-color", "#262626"],
    ["boundary_country_inner", "line-color", "#2b2c2c"],
    // ⚠ Not hidden, dimmed. The raster does draw internal boundaries — they are simply almost
    // invisible against the land, and removing them outright would be a different map again.
    ["boundary_state", "line-color", "#171717"],
    ["place_country_1", "text-color", "#858687"],
    ["place_country_2", "text-color", "#7b7c7c"],
  ],
  light: [
    ["water", "fill-color", "#d5e8eb"],
  ],
};

/**
 * The credit the RASTER fallback must carry. On the vector path this text arrives from the
 * style's own source metadata; a plain L.tileLayer publishes nothing, so without this the
 * fallback map would render with the department line alone — which is exactly the ODbL
 * attribution gap the raster basemap shipped with for as long as it was the only basemap.
 * A degraded path is still a published map.
 */
export const RASTER_FALLBACK_ATTRIBUTION =
  '<span class="map-credit" dir="ltr">&copy; <a href="https://carto.com/about-carto/" target="_blank" rel="noopener noreferrer">CARTO</a>, &copy; ' +
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors</span>';

export const RASTER_FALLBACK_URL: Record<Theme, string> = {
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  light: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
};

/**
 * Is a WebGL context obtainable at all? Checked BEFORE constructing the vector layer, because
 * MapLibre's own failure mode is a throw that takes the React tree with it (see above).
 * Wrapped in try/catch because some hardened browsers throw from getContext itself rather
 * than returning null.
 */
export function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

/* The tile filter and every glyph/trail color come from tokens.css. MapView
 * stamps the basemap theme as data-theme on .map-host ONLY, so that subtree's
 * tokens (tiles, glyphs, controls) recolor while the UI's <html> data-theme —
 * and thus the whole surrounding chrome — stays put. */

/**
 * The department line for the map's attribution control.
 *
 * ⚠ IT DELIBERATELY CARRIES NO MAP CREDIT OF ITS OWN, AND THAT IS NOT THE OLD BUG RETURNING.
 * The vector style publishes its own attribution — "© CARTO, © OpenStreetMap contributors",
 * from the `attribution` field of the source's tiles.json — and maplibre-gl-leaflet feeds it
 * straight into Leaflet's control. Verified in the running page. Adding the same credit here
 * would print it twice.
 *
 * ⛔ SO DO NOT DELETE THE STYLE'S CREDIT ON THE ASSUMPTION IT IS DECORATION: with the raster
 * basemap this control showed ONLY this department line, which meant the ODbL attribution
 * condition on the OpenStreetMap data underneath was unmet for as long as those tiles were
 * used. The move to vector fixed that as a side effect. If the basemap ever changes again,
 * check the credit is still being printed by something.
 *
 * ⚠ maplibre-gl-leaflet ignores a Leaflet `attribution` layer option, so this is added
 * directly to the attribution control in MapView rather than passed to the layer.
 */
export const TILE_ATTRIBUTION =
  '<div class="custom-map-attribution"><img src="./logo.png" alt="الشعار" class="custom-map-attribution-logo" /><span>قسم التحليل المرئي</span></div>';
