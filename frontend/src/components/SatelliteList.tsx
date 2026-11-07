/**
 * Satellite contact table for the أقمار tab — parallel to Contact/VesselList,
 * bidirectionally linked with map selection. NORAD-keyed. Inclination is
 * static (parsed from the TLE) so listing it costs no propagation.
 *
 * UI-3: the table mechanics moved to DataTable. NORAD ids are numbers, so the
 * row key is stringified here and parsed back on select — DataTable keys on
 * strings so one contract serves every list.
 */

import type { SatelliteElement } from "../api/types";
import { T } from "../lib/strings";
import DataTable from "./DataTable";
import type { Column, Row } from "./DataTable";

interface Props {
  satellites: SatelliteElement[];
  selected: number | null;
  onSelect: (norad: number | null) => void;
}

function inclDeg(tle2: string): string {
  const v = parseFloat(tle2.slice(8, 16));
  return Number.isFinite(v) ? `${v.toFixed(1)}°` : "—";
}

const COLUMNS: Column[] = [
  { header: T.satellite.colName, cellClass: "v-name" },
  { header: T.satellite.colNorad },
  { header: T.satellite.colIncl, right: true },
];

export default function SatelliteList({ satellites, selected, onSelect }: Props) {
  const sorted = [...satellites].sort((a, b) => a.name.localeCompare(b.name));

  const rows: Row[] = sorted.map((s) => ({
    key: String(s.norad_id),
    cells: [s.name, s.norad_id, inclDeg(s.tle2)],
  }));

  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      selected={selected == null ? null : String(selected)}
      onSelect={(k) => onSelect(k == null ? null : Number(k))}
      empty={T.satellite.listEmpty}
      aria={T.satellite.listAria}
    />
  );
}
