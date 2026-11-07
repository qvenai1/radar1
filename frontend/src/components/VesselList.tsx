/**
 * Vessel contact table for the maritime tab — the sea-going parallel of
 * ContactList, bidirectionally linked with map selection (click a row →
 * select on the map; select on the map → highlight + scroll the row).
 * MMSI-keyed (vessels have no icao24).
 *
 * UI-3: the table mechanics (scroll box, sticky header, selection, keyboard,
 * empty row) moved to DataTable. What stays is what is vessel-specific — the
 * columns, the MT source badge, and the stale-age tag.
 */

import type { Vessel } from "../api/types";
import { vesselKey } from "../api/types";
import { isStale, isoAge } from "../lib/format";
import { T } from "../lib/strings";
import DataTable from "./DataTable";
import type { Column, Row } from "./DataTable";

interface Props {
  vessels: Vessel[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}

function typeText(v: Vessel): string {
  if (!v.ship_type) return "—";
  return T.vessel.types[v.ship_type] ?? v.ship_type;
}

function vesselLabel(v: Vessel): string {
  return v.name || `#${v.mmsi ?? v.ship_id ?? "?"}`;
}

const COLUMNS: Column[] = [
  { header: T.vessel.colName, cellClass: "v-name" },
  { header: T.vessel.colType },
  { header: T.vessel.colSpeed, right: true },
];

export default function VesselList({ vessels, selected, onSelect }: Props) {
  const sorted = [...vessels].sort((a, b) => vesselLabel(a).localeCompare(vesselLabel(b)));

  const rows: Row[] = sorted.map((v) => {
    const mt = v.source === "marinetraffic";
    // UI-FRESH: a four-day-old MarineTraffic position and a ten-second-old AIS
    // position were indistinguishable here except by source badge. Mark the age
    // itself — flag, never drop (G3).
    const stale = isStale(v.timestamp);
    const age = isoAge(v.timestamp);
    return {
      key: vesselKey(v),
      className: stale ? "stale" : undefined,
      cells: [
        <>
          {vesselLabel(v)}
          {mt && <span className="badge src-badge src-mt" title={T.vessel.sourceMt}>MT</span>}
          {stale && (
            <span className="stale-tag" title={T.vessel.staleTitle(age)}>
              {age}
            </span>
          )}
        </>,
        typeText(v),
        v.speed == null ? "—" : v.speed.toFixed(1),
      ],
    };
  });

  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      selected={selected}
      onSelect={onSelect}
      empty={T.vessel.listEmpty}
      aria={T.vessel.listAria}
    />
  );
}
