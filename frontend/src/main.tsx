import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Brand fonts (bundled locally — no CDN dependency): Qomra for UI (THEME-1),
// Manrope for the brand heading, JetBrains Mono for data readouts.
//
// Cairo is KEPT and still imported. It is no longer the UI face, but it remains the
// fallback behind Qomra in --font-ui and the Arabic fallback inside --font-data, and it
// is the only bundled face with TABULAR numerals — which Qomra does not have, so the
// stacked-number sites route to --font-data (JetBrains) rather than to nothing.
import "@fontsource/cairo/400.css";
import "@fontsource/cairo/600.css";
import "@fontsource/cairo/700.css";
import "@fontsource/manrope/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/700.css";
import "./styles/fonts.css";
// Forest BEFORE tokens: the bridge is applied UNDER the project layer, so on any name
// collision the project's value wins by cascade order (THEME-2).
import "./styles/forest.css";
import "./styles/tokens.css";
// Forest's component grammar. Position in this list is informational only — every rule in
// it sits in `@layer forest`, so it loses to unlayered project CSS wherever the project has
// a measured reason to differ, no matter which file loads first (THEME-3).
import "./styles/components.css";
import "./styles/app.css";
// ⛔ BIDI IS LAST, AND MUST STAY LAST (THEME-3 commit 2). Forest's component layer has no
// direction handling whatsoever — measured: zero `unicode-bidi` declarations in the whole
// file — and it is about to be laid under the surfaces that carry MMSIs, icao24s, NORAD ids
// and UTC stamps. Loading these rules last makes the project's direction win on cascade
// order; `@layer forest` (forest.css) makes it win on specificity too.
import "./styles/bidi.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
