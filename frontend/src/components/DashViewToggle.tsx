/**
 * DashViewToggle (DASH-NAV) — the لوحة surface's own رصد / إحصاءات switch.
 *
 * ⛔ THIS USED TO LIVE IN THE NAVBAR, and the move is the correction, not a restyle.
 * Measured before: the dashboard carried TWO levels of navigation in TWO places —
 * رصد/إحصاءات as an app-level pill in the floating bar, while بحث كيان/منطقة/تفصيلي sat
 * inside the page. One surface, two hierarchies, two locations. The owner decided for one:
 * the sub-mode belongs to the surface it switches, on its common starting line, exactly
 * where the three search modes already live. The navbar renders no dashboard control any
 * more (see NavBar's header).
 *
 * Rendered by BOTH surfaces it switches: DashboardPanel's head row (monitor) and
 * StatsView's controls row (stats) — so the way out is always visible from the surface
 * you are on. Presentation only; `view`/`onSelect` are App's existing state.
 */

import { T } from "../lib/strings";

interface Props {
  view: "monitor" | "stats";
  onSelect: (v: "monitor" | "stats") => void;
}

export default function DashViewToggle({ view, onSelect }: Props) {
  return (
    <div className="seg segment" role="group" aria-label={T.dashHome.viewAria}>
      <button aria-pressed={view === "monitor"} onClick={() => onSelect("monitor")}>
        {T.dashHome.statsMonitor}
      </button>
      <button aria-pressed={view === "stats"} onClick={() => onSelect("stats")}>
        {T.dashHome.statsCharts}
      </button>
    </div>
  );
}
