/**
 * HTML-entity escape for Leaflet popup/tooltip content.
 *
 * Leaflet takes popup/tooltip content as an HTML STRING, so every data-derived
 * field must pass through esc() — vessel names/flags arrive from spoofable
 * AIS/GFW feeds and a crafted name would otherwise execute in the analyst's
 * browser (security review finding 2). Static T.* i18n strings and arithmetic
 * results are the only exemptions. Unit-proven in escapeHtml.test.ts.
 */
export function esc(v: unknown): string {
  return String(v).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}
