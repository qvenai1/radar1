/**
 * DataTable (UI-3) — the shared selectable contact table.
 *
 * ContactList, VesselList, SatelliteList and the airport landing table each rebuilt
 * the SAME table by hand: a `.contact-list` scroll box, a sticky header, one row per
 * item, and four behaviours that were duplicated verbatim in every copy —
 *
 *   1. scroll the selected row into view (`scrollIntoView({block:"nearest"})`)
 *   2. click to toggle selection (clicking the selected row clears it)
 *   3. Enter/Space do the same, with preventDefault
 *   4. an italic centred empty row spanning every column
 *
 * Only the columns and the cell contents ever differed, so those stay with the caller
 * and everything else lives here. The row key attribute is normalised to `data-rowkey`
 * (it was `data-icao` / `data-vkey` / `data-norad`); nothing outside the components
 * referenced those, so the rename is internal.
 *
 * NOT in scope: the alert-card grid. Turning those cards into a ~128px table row is
 * UI-4's job (see DashboardHome), and this component is deliberately not shaped for it
 * yet — it assumes one line per row.
 *
 * Behaviour-preserving by construction: same element order, same class names, same
 * handlers.
 *
 * Two callers were deliberately left alone rather than bent to fit:
 *   - The analysis panels (GapCandidates / Rendezvous / IdentityChanges) — multi-line
 *     composite rows with no selection; routing them through this contract would
 *     change how they render.
 *   - AirportSearchDetail's landing table — it is HEADERLESS, resolves its empty state
 *     before the table exists, and its React key (icao24 + landing_ts) is not its
 *     selection key (icao24, which can repeat across landings). Supporting all three
 *     would mean three opt-out flags for one caller; the duplication left behind is
 *     the scroll effect alone.
 */

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

export interface Column {
  /** header label */
  header: string;
  /** right-align this column (the `.r` class the stylesheet already defines) */
  right?: boolean;
  /**
   * Class applied to every `<td>` in this column. The name column carries `v-name`
   * in three of the four callers — dropping it would have changed how names wrap,
   * so it is part of the contract rather than something callers re-add per cell.
   */
  cellClass?: string;
}

export interface Row {
  /** stable identity — the value handed back to onSelect, and the scroll target */
  key: string;
  /** one node per column, in column order */
  cells: ReactNode[];
  /** extra class on the <tr> (e.g. `stale`) */
  className?: string;
}

interface Props {
  columns: Column[];
  rows: Row[];
  selected: string | null;
  /** toggle semantics: selecting the already-selected row clears the selection */
  onSelect: (key: string | null) => void;
  empty: string;
  aria: string;
}

export default function DataTable({ columns, rows, selected, onSelect, empty, aria }: Props) {
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  // keep the selected row visible when selection comes from elsewhere (the map)
  useEffect(() => {
    if (!selected || !bodyRef.current) return;
    bodyRef.current
      .querySelector<HTMLTableRowElement>(`tr[data-rowkey="${CSS.escape(selected)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <div className="contact-list table-wrap" role="region" aria-label={aria}>
      <table className="table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.header} className={c.right ? "r" : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody ref={bodyRef}>
          {rows.map((r) => {
            const toggle = () => onSelect(r.key === selected ? null : r.key);
            return (
              <tr
                key={r.key}
                data-rowkey={r.key}
                className={r.className}
                aria-selected={r.key === selected}
                tabIndex={0}
                onClick={toggle}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle();
                  }
                }}
              >
                {r.cells.map((cell, i) => {
                  const c = columns[i];
                  const cls = [c?.cellClass, c?.right ? "r" : ""].filter(Boolean).join(" ");
                  return (
                    <td key={i} className={cls || undefined}>
                      {cell}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} style={{ textAlign: "center", fontStyle: "italic" }}>
                {empty}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
