/**
 * CaseFile — DASH-5. The shared case file: evidence kept for a report.
 *
 * ⛔ EVERY ROW IS A SNAPSHOT, NOT A LIVE READ. The alert feed is stateless and rebuilt on every
 * request, so an item collected last week is simply absent from it now. These rows store the
 * claim AS IT WAS WHEN COLLECTED — which is also what an analytic product is supposed to do:
 * ICD 206 requires a source descriptor "when the time of pertinent information in a source is
 * significantly different from the time of publication", and that gap is exactly the distance
 * between keeping an item and writing the report.
 *
 * ⚠ `still_live` IS INFORMATION, NEVER A CORRECTION. An item that has left the feed's window
 * has not been disproven, and this component must not present it as retracted. It is shown as
 * a neutral state — "still in the current feed" / "outside it" — with no severity colouring.
 *
 * ⛔ THE CAVEAT IS RENDERED ON EVERY ITEM AND IN THE EXPORT. models.py calls the field
 * mandatory because "a lead read as a finding is the failure mode this project is built to
 * avoid" — and a lead copied into a report, stripped of what it does NOT mean, is that failure
 * in its most durable form. There is deliberately no way to collapse or omit it here.
 */
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../api/client";
import type { CaseItem } from "../api/types";
import { T } from "../lib/strings";

const DOMAIN_DOT: Record<string, string> = {
  air: "var(--glyph-air)", sea: "var(--glyph-vessel)", space: "var(--glyph-sat)",
};

const stamp = (iso?: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");

/**
 * The export — a REPORT DRAFT in plain text, not a data dump.
 *
 * ⛔ TEXT, AND THE CHOICE IS MEASURED. Joslyn & Savelli (2020) found every graphical redesign
 * of an uncertainty display failed to reduce deterministic construal errors while plain text
 * raised correct reading to 94 %. A report is where a reader is most likely to treat a lead as
 * a finding, so it is the last place to hand them a graphic instead of a sentence.
 * ⚠ Every item carries its caveat and its collection time. An export that dropped either would
 * produce a document asserting more than the evidence does.
 */
function toReport(items: CaseItem[]): string {
  const now = new Date().toISOString().slice(0, 16).replace("T", " ");
  const lines: string[] = [
    T.caseFile.reportTitle,
    `${T.caseFile.reportAt}: ${now}Z`,
    `${T.caseFile.reportCount}: ${items.length}`,
    "",
  ];
  for (const [i, it] of items.entries()) {
    lines.push(`${i + 1}. ${it.label}${it.severity ? ` — ${T.dashHome.severity[it.severity] ?? it.severity}` : ""}`);
    if (it.headline) lines.push(`   ${T.entityDetail.observed}: ${it.headline}`);
    if (it.why) lines.push(`   ${T.entityDetail.why}: ${it.why}`);
    // ⛔ NEVER omitted, even when long.
    if (it.caveat) lines.push(`   ${T.entityDetail.caveat}: ${it.caveat}`);
    if (it.note) lines.push(`   ${T.caseFile.note}: ${it.note}`);
    lines.push(`   ${T.caseFile.collected}: ${stamp(it.added_at)}Z` +
      (it.alert_ts ? ` · ${T.caseFile.signalAt}: ${stamp(it.alert_ts)}Z` : "") +
      ` · ${it.still_live ? T.caseFile.live : T.caseFile.notLive}`);
    lines.push("");
  }
  /* ⚠ The standing reservation, restated ONCE at the foot. The per-item caveats say what each
   * signal does not mean; this says what the SET does not mean. */
  lines.push(T.caseFile.reportFoot);
  return lines.join("\n");
}

export default function CaseFile() {
  const [items, setItems] = useState<CaseItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.caseItems()
      .then((r) => { setItems(r); setErr(null); })
      .catch((e) => setErr(e instanceof ApiError ? e.message : String(e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function drop(it: CaseItem) {
    setBusy(true); setErr(null);
    try {
      /* ⚠ Removal needs only the key — the snapshot fields are ignored server-side, so a
       * minimal object is sent rather than reconstructing an Alert this component never had. */
      await api.setCase(
        { signal_type: it.signal_type ?? "", entity_ref: { id: it.dedup_key.split(":").slice(1).join(":") } } as never,
        { remove: true },
      );
      load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(false); }
  }

  function exportReport() {
    const blob = new Blob([toReport(items ?? [])], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `atheer-case-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (err && !items) return <div className="ed-empty">{err}</div>;
  if (!items) return <div className="ed-empty">{T.caseFile.loading}</div>;
  if (!items.length) return <div className="ed-empty">{T.caseFile.empty}</div>;

  return (
    <section className="case-file" aria-label={T.caseFile.aria}>
      <header className="cf-head">
        <span className="cf-count">{T.caseFile.count(items.length)}</span>
        <button type="button" className="ed-act" onClick={exportReport}>{T.caseFile.export}</button>
        {err ? <span className="ed-err">{err}</span> : null}
      </header>

      {items.map((it) => (
        <article className="cf-item" key={it.dedup_key}>
          <div className="cf-top">
            <span className="er-dot" style={{ background: DOMAIN_DOT[it.domain ?? ""] }} aria-hidden="true" />
            <span className="cf-label">{it.label}</span>
            {it.severity ? (
              <span className={`er-sev s-${it.severity}`}>{T.dashHome.severity[it.severity] ?? it.severity}</span>
            ) : null}
            {/* ⚠ NEUTRAL, not a severity colour: leaving the window is not a verdict. */}
            <span className={`cf-live${it.still_live ? " on" : ""}`}>
              {it.still_live ? T.caseFile.live : T.caseFile.notLive}
            </span>
            <span className="er-age">{stamp(it.added_at)}</span>
            <button type="button" className="ed-act ghost" disabled={busy} onClick={() => drop(it)}>
              {T.caseFile.remove}
            </button>
          </div>
          {it.headline ? <p className="ed-claim obs"><b>{T.entityDetail.observed}</b>{it.headline}</p> : null}
          {it.why ? <p className="ed-claim why"><b>{T.entityDetail.why}</b>{it.why}</p> : null}
          {/* ⛔ Always rendered; there is no collapse control for it, by design. */}
          {it.caveat ? <p className="ed-claim cav"><b>{T.entityDetail.caveat}</b>{it.caveat}</p> : null}
          {it.note ? <p className="cf-note"><b>{T.caseFile.note}</b>{it.note}</p> : null}
        </article>
      ))}
    </section>
  );
}
