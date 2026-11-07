/**
 * Contact table, bidirectionally linked with map selection: clicking a row
 * selects on the map; selecting on the map highlights + scrolls the row.
 *
 * UI-3: the table mechanics moved to DataTable; what stays is aircraft-specific —
 * the columns and the provenance tags (military / ADS-B / on-ground).
 */

import type { StateVector } from "../api/types";
import { callsign, ft, kt } from "../lib/format";
import { T } from "../lib/strings";
import DataTable from "./DataTable";
import type { Column, Row } from "./DataTable";

interface Props {
  states: StateVector[];
  milSet: Set<string>;
  adsbSet: Set<string>;
  selected: string | null;
  onSelect: (icao24: string | null) => void;
}

const COLUMNS: Column[] = [
  { header: T.list.callsign },
  { header: T.list.icao },
  { header: T.list.alt, right: true },
  { header: T.list.spd, right: true },
];

export default function ContactList({ states, milSet, adsbSet, selected, onSelect }: Props) {
  const sorted = [...states].sort((a, b) => callsign(a.callsign).localeCompare(callsign(b.callsign)));

  const rows: Row[] = sorted.map((s) => ({
    key: s.icao24,
    cells: [
      <>
        {callsign(s.callsign)}
        {milSet.has(s.icao24) && <span className="mil-tag"> ·{T.military.tag}</span>}
        {adsbSet.has(s.icao24) && <span className="adsb-tag"> ·{T.adsb.tag}</span>}
        {s.on_ground && <span className="ground-tag"> ·{T.list.groundTag}</span>}
      </>,
      s.icao24,
      s.baro_altitude == null ? "—" : ft(s.baro_altitude).split(" ")[0],
      s.velocity == null ? "—" : kt(s.velocity).split(" ")[0],
    ],
  }));

  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      selected={selected}
      onSelect={onSelect}
      empty={T.list.empty}
      aria={T.list.aria}
    />
  );
}
