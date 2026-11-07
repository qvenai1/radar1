/**
 * Sidebar for the أقمار tab — parallel to Maritime/aircraft sidebars.
 * Satellite KPIs + a CelesTrak group selector + TLE-freshness feed line +
 * composed children (list + detail strip).
 */

import type { ReactNode } from "react";
import type { SatelliteElement, SatelliteGroup } from "../api/types";
import { num } from "../lib/format";
import { T } from "../lib/strings";

interface Props {
  satellites: SatelliteElement[];
  groups: SatelliteGroup[];
  group: string;
  fetchedAt: string | null;
  onGroup: (group: string) => void;
  children: ReactNode;
}

function ageText(iso: string | null): string {
  if (!iso) return "—";
  const h = (Date.now() - Date.parse(iso)) / 3_600_000;
  if (!Number.isFinite(h)) return "—";
  return h < 1 ? "< ساعة" : h < 48 ? `${Math.round(h)} ساعة` : `${(h / 24).toFixed(1)} يوم`;
}

function groupLabel(g: string): string {
  return T.satellite.groupNames[g] ?? g;
}

export default function SatelliteSidebar({
  satellites, groups, group, fetchedAt, onGroup, children,
}: Props) {
  const available = satellites.length > 0;
  return (
    <aside className="side">
      <div className="brand">
        <h1>
          <img src="./logo1.png" alt="" aria-hidden="true" className="brand-mark" />
          <span>
            Atheer <span className="tail">RADAR</span>
          </span>
        </h1>
        <div className="sub">{T.satellite.brandSub}</div>
      </div>

      <div className="stat-grid" role="group" aria-label={T.satellite.kpiAria}>
        <div className="metric accent">
          <div className="n">{num(satellites.length)}</div>
          <div className="l">{T.satellite.kpiCount}</div>
        </div>
        <div className="metric">
          <div className="n">{num(groups.length)}</div>
          <div className="l">{T.satellite.kpiGroups}</div>
        </div>
      </div>

      <div>
        <div className="section-label">{T.satellite.group}</div>
        <div className="sat-groups" role="group" aria-label={T.satellite.groupAria}>
          {groups.map((g) => (
            <button
              key={g.group}
              aria-pressed={g.group === group}
              onClick={() => onGroup(g.group)}
              title={`${g.count}`}
            >
              {groupLabel(g.group)}
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="section-label">{T.feed.section}</div>
        <div className="feed-row">
          <span className={`dot ${available ? "ws" : ""}`} aria-hidden="true" />
          <span role="status">
            {available ? T.satellite.feedFetched(ageText(fetchedAt)) : T.satellite.feedNone}
          </span>
        </div>
      </div>

      {children}
    </aside>
  );
}
