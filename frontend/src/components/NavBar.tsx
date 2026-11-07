/**
 * Top navigation — a floating pill bar organised by DOMAIN, not by data source.
 *
 * Sources within a domain are merged, so the user never picks a feed here. `stats` is a
 * SUB-mode of the dashboard (so the لوحة tab stays active on it), not a tab. Contextual
 * controls are passed as `children` and live on the right.
 *
 * ⛔ DASH-NAV — the dashboard's رصد/إحصاءات switch is NO LONGER one of those children.
 * It lived here while the three search modes lived inside the page — one surface carrying
 * two levels of navigation in two places, measured and named as drift. The switch now
 * renders on the surfaces it switches (DashViewToggle: DashboardPanel's head row and
 * StatsView's controls); the theme button is the only contextual child left.
 *
 * ⚠ THE DOMAIN RULE HAS A DOCUMENTED EXCEPTION, stated rather than broken quietly.
 * FOUR tabs, and only THREE of them are domains:
 *
 *   لوحة (monitor)                          — a CROSS-DOMAIN VIEW
 *   جوّي (air) · بحري (sea) · فضائي (space)  — domains
 *
 * The rule's purpose is that the analyst never chooses a *data source* from this bar, and
 * لوحة does not ask them to: it reads across air, sea and space at once. It answers "what
 * am I looking at?", not "which feed?" — so the bar still never surfaces a source, which is
 * the invariant that matters.
 *
 * ⚠ CORRECTED IN DASH-2g′ — this header said "Five tabs" and named الرئيسية as the second
 * cross-domain view. The owner removed that tab: the country card it carried is now pinned
 * to the three domain tabs instead (App.CARD_VIEWS), so the landing surface no longer exists
 * as a destination. The count is four. This header has now been wrong twice in its own
 * history — it claimed four domain tabs when one was not a domain, and then five tabs after
 * one was removed — which is exactly why the count is restated here rather than left to be
 * inferred from the array below.
 */

import type { ReactNode } from "react";

type View = "live" | "maritime" | "satellites" | "monitor" | "stats";

const ICON: Record<string, ReactNode> = {
  // dashboard — a 2×2 grid
  monitor: (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="3" y="3" width="8" height="8" rx="1.6" />
      <rect x="13" y="3" width="8" height="8" rx="1.6" />
      <rect x="3" y="13" width="8" height="8" rx="1.6" />
      <rect x="13" y="13" width="8" height="8" rx="1.6" />
    </svg>
  ),
  // air — aircraft (Material "flight")
  live: (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M21 16v-2l-8-5V3.5A1.5 1.5 0 0 0 11.5 2 1.5 1.5 0 0 0 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V21l3.5-1 3.5 1v-1.5L13 19v-5.5z" />
    </svg>
  ),
  // sea — ship (Material "directions_boat")
  maritime: (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20 21c-1.39 0-2.78-.47-4-1.32-2.44 1.71-5.56 1.71-8 0C6.78 20.53 5.39 21 4 21H2v2h2c1.38 0 2.74-.35 4-.99 2.52 1.29 5.48 1.29 8 0 1.26.65 2.62.99 4 .99h2v-2h-2zM3.95 19H4c1.6 0 3.02-.88 4-2 .98 1.12 2.4 2 4 2s3.02-.88 4-2c.98 1.12 2.4 2 4 2h.05l1.89-6.68c.08-.26.06-.54-.06-.78a1 1 0 0 0-.6-.5L20 10.62V6c0-1.1-.9-2-2-2h-3V1H9v3H6c-1.1 0-2 .9-2 2v4.62l-1.29.42a1 1 0 0 0-.6.5c-.12.24-.15.52-.06.78L3.95 19zM6 6h12v3.97L12 8 6 9.97z" />
    </svg>
  ),
  // space — satellite (composed of simple shapes for reliability)
  satellites: (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="10" y="9.4" width="4" height="5.2" rx="0.8" />
      <rect x="2.6" y="10.6" width="6" height="2.8" rx="0.6" />
      <rect x="15.4" y="10.6" width="6" height="2.8" rx="0.6" />
      <rect x="11.35" y="4.6" width="1.3" height="5" />
      <circle cx="12" cy="4" r="1.5" />
    </svg>
  ),
};

// ⚠ `DOMAINS` remains a mild misnomer, kept for a smaller diff: the first entry is the
// cross-domain view, the other three are the actual domains (see the header).
const DOMAINS: { v: View; label: string }[] = [
  { v: "monitor", label: "لوحة" },
  { v: "live", label: "جوّي" },
  { v: "maritime", label: "بحري" },
  { v: "satellites", label: "فضائي" },
];

interface Props {
  view: View;
  onSelect: (v: View) => void;
  children?: ReactNode; // contextual right-side controls (per-view)
}

export default function NavBar({ view, onSelect, children }: Props) {
  return (
    <nav className="navbar" aria-label="التنقّل">
      <div className="nav-brand">
        {/* Decorative: alt="" + aria-hidden because the wordmark beside it already
            names the brand — a screen reader announcing it twice is noise, not access. */}
        <img src="./logo1.png" alt="" aria-hidden="true" className="brand-mark" />
        <span>
          Atheer <span className="tail">RADAR</span>
        </span>
      </div>
      <div className="nav-tabs" role="tablist" aria-label="المجالات">
        {DOMAINS.map((d) => {
          // 'stats' is a sub-mode of the dashboard, so keep لوحة active on it
          const active = view === d.v || (d.v === "monitor" && view === "stats");
          return (
            <button
              key={d.v}
              role="tab"
              aria-selected={active}
              className={`nav-tab${active ? " active" : ""}`}
              onClick={() => onSelect(d.v)}
              title={d.label}
            >
              <span className="nav-ico">{ICON[d.v]}</span>
              <span className="nav-lbl">{d.label}</span>
            </button>
          );
        })}
      </div>
      <div className="nav-right">{children}</div>
    </nav>
  );
}
