/**
 * EntityDetail — DASH-4b-3. ONE entity, the whole width, and the analysis finally visible.
 *
 * ⛔ WHAT THIS FIXES, AND IT IS THE MISS I REPEATED TWICE. The backend composes THREE
 * calibrated fields per alert — `headline` (what was observed), `why` (why it ranks there)
 * and `caveat` (what it does NOT mean) — separately tuned per family across five worked items
 * (BE-SUMMARY-1..5). Two rejected dashboard proposals rendered ONLY the headline. The most
 * valuable thing the system produces was on the wire the whole time and never reached the
 * screen.
 *
 * ⛔ AND TEXT IS THE RIGHT CARRIER FOR THIS, WHICH IS COUNTER-INTUITIVE AND MEASURED. Joslyn &
 * Savelli (Frontiers in Computer Science, 2020) tested redesign after redesign of an
 * uncertainty graphic — broken lines, transparency, reoriented brackets, embedded keys — and
 * "these manipulations did not reduce DCEs" (deterministic construal errors). What worked was
 * removing the visualization and stating it in plain text: 94 % then read it correctly. The
 * lanes show WHEN; only the sentences can say WHAT IT DOES NOT MEAN.
 *
 * ⚠ AT 300px THOSE SENTENCES WERE UNREADABLE — ~30 characters a line. That is why DASH-4b-1
 * released the map seat before this component existed: the claims column is capped at 78ch
 * here, inside the ~65ch–75ch band that keeps running text legible.
 *
 * ⛔ NOTHING ON THIS SURFACE IS AUTHORED. Every claim is the alert's own field, rendered
 * verbatim. The only text this component owns is chrome — labels, and the empty state.
 */
import { useState } from "react";

import { api, ApiError, setWriteToken, writeToken } from "../api/client";
import type { Alert } from "../api/types";
import PatternLanes from "./PatternLanes";
import { T } from "../lib/strings";

const DOMAIN_DOT: Record<string, string> = {
  air: "var(--glyph-air)", sea: "var(--glyph-vessel)", space: "var(--glyph-sat)",
};

/** ⛔ MUST MATCH `notify.identity.dedup_key` — the SAME key the bot uses for its send ledger.
 *  A second definition would let the dashboard and the bot disagree about what "the same
 *  alert" is, and the disagreement would stay invisible until an analyst closed something the
 *  bot then re-sent.
 *  ⚠ Mirrored by hand here because the rules live in Python. The families whose key includes a
 *  timestamp (`military_satellite`) include it here too. */
const triageKey = (a: Alert): string =>
  a.signal_type === "military_satellite"
    ? `${a.signal_type}:${a.entity_ref.id}:${a.ts ?? ""}`
    : `${a.signal_type}:${a.entity_ref.id}`;

const stamp = (iso?: string | null) =>
  iso ? iso.slice(0, 16).replace("T", " ") : null;

interface Props {
  alert: Alert | null;
  onBack: () => void;
  /** Called after a successful write so the caller can refetch. ⚠ The caller REFETCHES rather
   *  than this component patching its prop: the state is COLLECTIVE, so another analyst may
   *  have changed something else in the same window, and an optimistic local patch would show
   *  one analyst a screen nobody else has. */
  onTriaged?: () => void;
}

export default function EntityDetail({ alert, onBack, onTriaged }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [askToken, setAskToken] = useState(false);
  const hasToken = !!writeToken();

  const [kept, setKept] = useState(false);

  async function keep(a: Alert) {
    setBusy(true); setErr(null);
    try {
      await api.setCase(a);
      setKept(true);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
      if (e instanceof ApiError && e.status === 403) setAskToken(true);
    } finally { setBusy(false); }
  }

  async function triage(a: Alert, state: "reviewing" | "closed" | null) {
    setBusy(true); setErr(null);
    try {
      /* ⛔ ECHO THE SERVER'S OWN SIGNATURE. An earlier version sent `severity_basis` — a
       * different field entirely — so the stored sig never matched the live one and every
       * triaged item came back `triage_stale=true` on the next poll. The feature inverted
       * itself, and only an end-to-end click found it. */
      await api.setTriage(triageKey(a), state, { changeSig: a.change_sig ?? undefined });
      onTriaged?.();
    } catch (e) {
      /* ⛔ THE REASON IS SHOWN, NOT SWALLOWED. A 503 means the server has no token configured
       * at all and a 403 means this browser's is missing or wrong — two different fixes, and
       * a generic "failed" would send the analyst looking in the wrong place. */
      const msg = e instanceof ApiError ? e.message : String(e);
      setErr(msg);
      if (e instanceof ApiError && e.status === 403) setAskToken(true);
    } finally {
      setBusy(false);
    }
  }
  if (!alert) {
    return (
      <div className="ed-empty">{T.entityDetail.none}</div>
    );
  }
  const a = alert;

  /* ⚠ FACTS ARE BUILT FROM FIELDS THAT EXIST, and a missing one is DROPPED rather than
   * rendered as an em-dash. An empty row invites the reader to treat absence as a measured
   * zero; `countries: []` means "not attributable to an AOI country", never "nowhere". */
  const facts: Array<[string, string]> = [];
  if (a.source) facts.push([T.entityDetail.source, a.source]);
  const vintage = stamp(a.vintage);
  if (vintage) facts.push([T.entityDetail.vintage, vintage]);
  const first = stamp(a.first_seen_at);
  if (first) facts.push([T.entityDetail.firstSeen, first]);
  const ps = stamp(a.phenomenon_start);
  const pe = stamp(a.phenomenon_end);
  if (ps) facts.push([T.entityDetail.window, pe && pe !== ps ? `${ps} ← ${pe}` : ps]);
  const rt = stamp(a.result_time);
  if (rt) facts.push([T.entityDetail.derived, rt]);
  if (a.n_pattern_spans) facts.push([T.entityDetail.events, String(a.n_pattern_spans)]);

  return (
    <section className="entity-detail" aria-label={T.entityDetail.aria}>
      <header className="ed-head">
        <button type="button" className="ed-back" onClick={onBack}>{T.entityDetail.back}</button>
        <span className="ed-dot" style={{ background: DOMAIN_DOT[a.domain] }} aria-hidden="true" />
        <div className="ed-id">
          <h3>{a.entity_ref.label}</h3>
          {a.summary ? <span className="ed-sub">{a.summary}</span> : null}
        </div>
        {/* ⛔ THE PREDICTION BADGE IS DERIVED, NOT DECLARED. `is_prediction` is computed from
            `result_time < phenomenon_start` — OGC's own machine test — so a satellite pass
            labels itself without anyone setting a flag. The El Faro finding (NTSB MSR-17/02)
            is what makes this non-optional: a forecast rendered like an observation is read
            like one. */}
        {a.is_prediction ? <span className="ed-pred">{T.entityDetail.predicted}</span> : null}
        <span className={`er-sev s-${a.severity}`}>
          {T.dashHome.severity[a.severity] ?? a.severity}
        </span>
        {a.triage ? (
          <span className={`er-triage${a.triage_stale ? " stale" : ""} t-${a.triage}`}>
            {/* ⚠ THE STALE LABEL NAMES THE STATE IT QUALIFIES. It read a hard-coded «مُغلق ·
                تغيّر» for every stale item, so a vessel someone was REVIEWING displayed as
                closed. The state is the subject; "changed" is the qualifier. */}
            {T.entityRows.triage[a.triage]}{a.triage_stale ? T.entityRows.staleSuffix : ""}
          </span>
        ) : null}
      </header>

      {/* ⛔ DASH-4b-4 — THE TRIAGE CONTROLS. The state is COLLECTIVE: «قيد المراجعة» means
          SOMEONE is on it, which is what stops two analysts opening the same vessel on a
          shared screen with no per-analyst identity. */}
      <div className="ed-actions">
        <button type="button" className={`ed-act${a.triage === "reviewing" ? " on" : ""}`}
                disabled={busy} onClick={() => triage(a, a.triage === "reviewing" ? null : "reviewing")}>
          {T.entityRows.triage.reviewing}
        </button>
        <button type="button" className={`ed-act${a.triage === "closed" ? " on" : ""}`}
                disabled={busy} onClick={() => triage(a, a.triage === "closed" ? null : "closed")}>
          {T.entityRows.triage.closed}
        </button>
        {/* ⚠ NOT DISABLED WHEN THE TOKEN IS MISSING. A disabled control with no explanation is
            a dead end; the write is attempted, the server answers with a readable reason, and
            the passphrase field opens itself. Failing loudly beats failing greyed-out. */}
        {/* ⛔ DASH-5 — KEEP IT FOR THE REPORT. The add carries a SNAPSHOT of the claim, because
            the feed is stateless: a key alone would resolve to nothing once the window rolled
            past, and the report would cite a row nobody can produce. */}
        <button type="button" className="ed-act" disabled={busy}
                onClick={() => keep(a)}>{T.entityDetail.keep}</button>
        {kept ? <span className="ed-kept">{T.entityDetail.kept}</span> : null}
        {!hasToken && !askToken ? (
          <button type="button" className="ed-act ghost" onClick={() => setAskToken(true)}>
            {T.entityDetail.setToken}
          </button>
        ) : null}
        {err ? <span className="ed-err">{err}</span> : null}
      </div>

      {askToken ? (
        <form
          className="ed-token"
          onSubmit={(e) => {
            e.preventDefault();
            const v = new FormData(e.currentTarget).get("tok");
            setWriteToken(typeof v === "string" ? v : "");
            setAskToken(false); setErr(null);
          }}
        >
          <label htmlFor="ed-tok">{T.entityDetail.tokenLabel}</label>
          <input id="ed-tok" name="tok" type="password" autoComplete="off"
                 defaultValue={writeToken()} placeholder={T.entityDetail.tokenHint} />
          <button type="submit" className="ed-act">{T.entityDetail.tokenSave}</button>
          <p className="ed-token-note">{T.entityDetail.tokenNote}</p>
        </form>
      ) : null}

      <div className="ed-body">
        {/* ⛔ THE MAIN COLUMN HOLDS TWO THINGS WITH OPPOSITE APPETITES, so it is a wrapper and
            not one of them. `.ed-claims` caps at 78ch because running text stops being
            readable past it; the lane stack wants EVERY pixel, because its cell size is the
            measurement this whole redesign turned on. Nesting the lanes INSIDE the claims —
            the first attempt — capped the track at 468px and the cell at 11.6px, back under
            the 24px bar. Siblings, not parent and child. */}
        <div className="ed-main">
        <div className="ed-claims">
          {a.headline ? (
            <p className="ed-claim obs"><b>{T.entityDetail.observed}</b>{a.headline}</p>
          ) : null}
          {a.why ? (
            <p className="ed-claim why"><b>{T.entityDetail.why}</b>{a.why}</p>
          ) : null}
          {/* ⛔ THE CAVEAT IS NEVER OPTIONAL AND NEVER COLLAPSED. models.py calls it mandatory
              for the stated reason that "a lead read as a finding is the failure mode this
              project is built to avoid". It is rendered last because that is where a reader
              leaves the entity, and at full opacity — a dimmed reservation is a scrolled-past
              one. */}
          {a.caveat ? (
            <p className="ed-claim cav"><b>{T.entityDetail.caveat}</b>{a.caveat}</p>
          ) : null}
          {!a.headline && !a.why && !a.caveat ? (
            /* ⚠ An HONEST empty state: this family has no Arabic register yet (models.py
               defines an empty `headline` as exactly that). It is not "no information". */
            <p className="ed-claim"><b>{T.entityDetail.noRegister}</b>{a.summary || "—"}</p>
          ) : null}
        </div>
          {/* ⛔ DASH-7 — THE LANES BELONG HERE TOO, and their absence was the entity surface's
              last gap. The fourth proposal puts them under the claims with a heading that
              states how to read them; without them this surface asserted «تزامن ٣ أنماط» in
              prose and gave the reader no way to CHECK it, which is the difference between a
              claim being read and a claim being believed.
              ⚠ It is also where the 24 cells get the most room: the claims cap at 78ch and
              left ~580px of the main column empty at 1440 before this. */}
          {(alert => alert.pattern_spans?.length ? (
            <div className="ed-tl">
              <h4>{T.entityDetail.lanesHead}</h4>
              <PatternLanes alert={alert} />
            </div>
          ) : null)(a)}
        </div>

        <aside className="ed-side">
          {facts.map(([k, v]) => (
            <div className="ed-fact" key={k}>
              <b>{k}</b>
              <span className={/\d/.test(v) ? "v mono" : "v"}>{v}</span>
            </div>
          ))}
          {/* ⛔ THE RELATIONSHIP, WHICH WAS IMPOSSIBLE BEFORE DASH-3. A rendezvous is a
              statement about TWO vessels and the model could only name one, so no consumer
              could render it. `counterparts[]` now carries the other side.
              ⚠ NEVER inferred from proximity in a list: two vessels sharing a feed is not a
              rendezvous, and asserting one is the claim CAVEAT_RENDEZVOUS spends a sentence
              denying. */}
          {(a.counterparts ?? []).map((c) => (
            <div className="ed-fact rel" key={c.id}>
              <b>{T.entityDetail.counterpart}</b>
              <span className="v">{c.label}</span>
              <span className="ed-key">{c.id}</span>
            </div>
          ))}
        </aside>
      </div>
    </section>
  );
}
