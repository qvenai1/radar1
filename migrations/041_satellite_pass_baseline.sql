-- 041_satellite_pass_baseline.sql
--
-- BASELINE-1 — what is USUAL for this object, so "unusual" can be a measurement.
--
-- ⛔ THE CLAIM THIS TABLE EXISTS TO MAKE POSSIBLE, AND WHY IT COULD NOT BE MADE BEFORE.
-- The bot reports every predicted AOI pass of every military-tagged satellite. A reviewer
-- asked, correctly, for the obvious improvement: say «ضمن النمط المعتاد» / «أقرب من
-- المعتاد» instead of listing fourteen passes. That sentence needs a baseline, and this
-- project had none — the wording could have been added in ten minutes and would have been
-- an INVENTION.
--
-- Measured, before building anything:
--     satellite_passes  — computed_at has exactly ONE distinct value across all rows.
--                         The table is fully recomputed and replaced each run: no history.
--     satellite_elements— 800 rows for 777 satellites, i.e. 1-2 epochs each. TLEs are
--                         REPLACED, not accumulated: no element history either.
-- So there was nothing in this database from which "usual" could be derived, and waiting
-- for history to accumulate would have meant weeks before the sentence could be true.
--
-- ✅ WHAT MAKES IT DERIVABLE TODAY: SGP4 propagates BACKWARD as readily as forward. The
-- builder (scripts/build_pass_baseline.py) runs the CURRENT element over the preceding
-- N days through the SAME classify_passes() the forward prediction uses, and aggregates
-- the result. Reuse is the point — a second pass definition would drift from the first.
--
-- ⛔ AND HERE IS WHAT THIS BASELINE IS NOT, stated on the table because it is the thing a
-- reader will assume: IT IS NOT OBSERVED HISTORY. No sensor saw these passes; they are the
-- same kind of computation as the forward prediction, pointed the other way. It therefore
-- characterises THE ORBIT'S GEOMETRY — how often this object's ground track crosses the
-- AOI and how close it usually comes — and must never be described as "what happened".
-- `method` carries that provenance in every row.
--
-- ⚠ SGP4 error grows with |t − epoch| in both directions, so a single back-propagated pass
-- is NOT accurate to the minute. That is why only DISTRIBUTIONS are stored (a count and
-- quantiles) and never individual reconstructed passes: the aggregate is robust to the
-- per-pass timing error in a way that a "this pass happened at 14:32 last Tuesday" claim
-- would not be.
--
-- Forward-only. Reversal: DROP TABLE.

CREATE TABLE satellite_pass_baseline (
    norad_id          INTEGER PRIMARY KEY,

    -- How much time the builder propagated over, and how many AOI passes it found there.
    -- ⛔ n_passes is the SAMPLE SIZE, and the classifier refuses to call anything "routine"
    -- below a floor (backend/pass_baseline.MIN_SAMPLE). A baseline of two passes is not a
    -- baseline; saying "usual" from it would be the same invention in a new place.
    window_days       INTEGER NOT NULL,
    n_passes          INTEGER NOT NULL,
    passes_per_day    DOUBLE PRECISION NOT NULL,

    -- The distribution of how close this object's ground track comes to the AOI, in km.
    -- p10 is the working threshold for "closer than usual": nearer than nine of ten of its
    -- own typical passes. Median is carried so a message can say what usual IS, not only
    -- that something differs from it.
    closest_min       DOUBLE PRECISION,
    closest_p10       DOUBLE PRECISION,
    closest_median    DOUBLE PRECISION,

    -- Which AOI countries its track usually crosses. A pass over a country ABSENT from
    -- this list is a different kind of unusual from a merely closer one.
    usual_countries   TEXT[],

    -- Provenance, carried per row and surfaced in the caveat.
    -- ⚠ tle_epoch is the element the baseline was propagated FROM; a baseline built from a
    -- stale element is itself stale, and the builder re-stamps this every run.
    method            TEXT NOT NULL,
    tle_epoch         TIMESTAMPTZ,
    computed_at       TIMESTAMPTZ NOT NULL
);

COMMENT ON TABLE satellite_pass_baseline IS
    'BASELINE-1 — per-satellite AOI-crossing geometry, derived by BACK-PROPAGATING the '
    'current TLE through the same classify_passes() as the forward prediction. NOT '
    'observed history: no sensor saw these. Distributions only — per-pass back-propagated '
    'timing is not accurate enough to assert individual past passes.';

-- The alerts endpoint joins this per norad on every request.
CREATE INDEX satellite_pass_baseline_computed_idx
    ON satellite_pass_baseline (computed_at);
