/**
 * DomainOverlay (DASH-2c′ → 2d″ → 2g′) — the two panels that float over the map on the
 * three DOMAIN tabs: the country card on the right, one entity's live payload on the left.
 *
 * ⛔ RENAMED FROM `HomePanel` IN 2g′, BECAUSE THE OLD NAME BECAME FALSE. The owner removed
 * the home tab and pinned this pair to جوّي · بحري · فضائي instead, so a file called
 * "HomePanel" would name a surface that no longer exists — the same stale-claim trap as the
 * NavBar header this item also corrected. What it renders did not change; where it mounts did.
 *
 * The owner's earlier decision still holds and is why no query input is mounted here — not
 * hidden, not disabled, not rendered: search is لوحة's job, this is the map's overlay.
 *
 * THE LAYOUT — a third grammar beside `.shell` and `.shell.monitor` (see app.css):
 *
 *   ⛔ This renders NO full-screen container. `.workspace` — the monitor's shell — is an
 *      opaque `inset:0` curtain at z-1100, and wrapping the home in it is precisely the
 *      UI-pre Q2 defect where a map view hid its own map. The map here is never covered.
 *   ✅ The single `MapView` already mounted under `.main` IS the hero; App mounts it for
 *      every view, so there is no second map to build. This component returns only the two
 *      floating panels, absolutely positioned inside `.main`.
 *
 *   `.home-panel.lead`   → `inset-inline-start` → the RIGHT edge in RTL
 *   `.home-panel.entity` → `inset-inline-end`   → the LEFT edge in RTL
 *
 * ⚠ WHAT IS AND IS NOT BUILT HERE. This item builds the destination and the layout only:
 *   - the lead panel carries `CountryEvents` — the country card (2e). It replaced the
 *     temporary `DashboardHome` tenant that 2c′ lifted and 2d″ parked here.
 *   - the entity panel is a STRUCTURAL placeholder for 2f: a titled shell that states it
 *     is not wired. No fabricated rows, counts or names — a fake would have to be
 *     un-built later, and on this project a convincing placeholder is worse than an
 *     obviously empty one.
 */

import type { Alert, AlertsResponse, AoiCountry } from "../api/types";
import CountryEvents from "./CountryEvents";
import EntityPayload from "./EntityPayload";
import type { EntitySel } from "./EntityPayload";
import { T } from "../lib/strings";

interface Props {
  // --- D5 the live dashboard: domain stats + the multi-domain alert feed. Pass-through
  //     ONLY; these moved here verbatim from DashboardPanel, which no longer knows about
  //     them. (DASH-2-pre-ب counted three — `sort`/`onSort` came later, with 2b's axis.) ---
  data: AlertsResponse | null;
  loading: boolean;
  onOpenAlert: (a: Alert) => void;
  /** DASH-2e: which AOI country the card is scoped to, and the window it reads. */
  country: AoiCountry;
  onCountry: (c: AoiCountry) => void;
  windowH: number;
  onWindow: (h: number) => void;
  /** DASH-2f: the entity whose live payload the LEFT panel shows (null = none chosen). */
  entity: EntitySel | null;
  onCloseEntity: () => void;
  /** DASH-2g′: which domain tab is showing this overlay. ⛔ ORDERING ONLY — see CountryEvents.
   *  It never filters: the card shows all three domains on every tab. */
  activeDomain: "air" | "sea" | "space";
}

export default function DomainOverlay({
  data, loading, onOpenAlert, country, onCountry, windowH, onWindow, entity, onCloseEntity,
  activeDomain,
}: Props) {
  return (
    <>
      {/* RIGHT in RTL — the lead panel. 2e replaced the temporary tenant with the
          country card; the panel itself (position, glass, RTL anchoring) is unchanged. */}
      <section className="home-panel lead" aria-label={T.home.leadAria}>
        <CountryEvents
          data={data}
          loading={loading}
          country={country}
          onCountry={onCountry}
          windowH={windowH}
          onWindow={onWindow}
          onOpenAlert={onOpenAlert}
          activeDomain={activeDomain}
        />
      </section>

      {/* LEFT in RTL — 2f wired this: one entity's live payload, fetched on demand. The
          panel's position, glass and anchoring are 2d″'s and unchanged. */}
      <EntityPayload sel={entity} onClose={onCloseEntity} />
    </>
  );
}
