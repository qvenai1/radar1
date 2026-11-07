/**
 * Caveat (UI-1 Part A) — the one place a caveat is rendered.
 *
 * Shows the Arabic paraphrase (the flip), and keeps the AUTHORITATIVE backend
 * methodology one disclosure away: `المنهجية (EN)` reveals the live response text,
 * LTR-isolated in the data font so the English reads correctly inside RTL chrome.
 * When the backend text no longer matches the paraphrased baseline, a drift marker
 * appears — the honest "read the original" signal (see lib/caveats.ts).
 *
 * Presentational: no state beyond the local disclosure. `className` lets it drop into
 * each panel's existing slot (.caveat / .strip-warn / .dash-caveat) with no CSS churn.
 */

import { useState } from "react";
import { resolveCaveat } from "../lib/caveats";
import type { CaveatKey } from "../lib/caveats";
import { T } from "../lib/strings";

interface Props {
  /** which caveat — a CaveatKey, so a typo is a tsc error (the completeness guard) */
  k: CaveatKey;
  /** the endpoint's `caveat` field; authoritative text, no longer the display text */
  response?: string | null;
  /** the host panel's existing class, so styling is unchanged */
  className?: string;
}

export default function Caveat({ k, response, className = "caveat" }: Props) {
  const [open, setOpen] = useState(false);
  const { text, authoritative, drifted } = resolveCaveat(k, response);
  const C = T.caveat;

  return (
    <div className={className}>
      <span className="caveat-ar">{text}</span>
      {drifted && (
        <span className="caveat-drift" title={C.driftTitle} role="img" aria-label={C.driftTitle}>
          {C.drift}
        </span>
      )}{" "}
      <button
        type="button"
        className="caveat-src"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? C.hideSource : C.showSource}
      </button>
      {open && (
        <div className="caveat-en" dir="ltr" lang="en" aria-label={C.sourceAria}>
          {authoritative}
        </div>
      )}
    </div>
  );
}
