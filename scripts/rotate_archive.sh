#!/bin/sh
# Daily raw-log rotation + off-volume archival (runs via scraplan-rotate.timer at 00:05).
#
# Safe while collectors run: every writer opens its file in append mode per
# write (archive.RawLog / JsonlWriter), so the next write recreates it.
# Rotated .gz files remain GROUND TRUTH — the backfill paths read them
# transparently, so a full DB rebuild covers the whole history.
#
# Covers every append-only raw log across all three layers + signals, and then
# moves .gz older than ARCHIVE_AGE_HOURS off this volume (DISK-1 step 8).
#
# WHY THE OFF-VOLUME MOVE EXISTS. Rotation gzips but never freed space: rotated
# .gz are ground truth and accumulate forever on the SAME single ext4 volume
# that holds root, /home, the TimescaleDB Docker volume and every archive
# (docs/PROJECT_STATE.md §7). Compressing a log that never leaves the disk does
# not buy headroom. So rotation now ends by relocating aged .gz to external
# media, checksum-verified, and deletes the local copy ONLY after the copy is
# proven byte-identical at the destination.
#
# GROUND-TRUTH DISCIPLINE (non-negotiable, mirrors the DB backfill contract):
#   sha256 the source -> copy -> sha256 the DESTINATION file -> compare ->
#   only on an exact match, delete the source. A mismatch leaves BOTH files in
#   place and exits non-zero so systemd flags the unit. Nothing is ever deleted
#   on the strength of a copy that was not read back.
#
# A MANIFEST.sha256 at the destination is permanent and append-only: it is the
# index that makes the relocated archive self-verifying (sha256sum -c) without
# this machine.
#
# SAFETY: the destination must be a real MOUNT POINT. If the external volume is
# absent, this skips the move with a warning and deletes NOTHING — writing into
# an unmounted /media/... path would silently consume the very volume we are
# trying to relieve, which is the failure this check exists to prevent.
#
# ⛔ WHY THE VERIFICATION IS WRITTEN THE WAY IT IS (ROTATE-HARDEN, 2026-08-04).
# The discipline above was correct in intent and fail-OPEN in implementation. Measured in
# BACKUP-HARDEN and proven here by detonation: `src_sum` and `dst_sum` were captured through
# a PIPELINE and compared with `!=` without ever being validated. Two consequences compound:
#
#   1. `sha256sum "$f" | cut -d' ' -f1` — a pipeline's exit status is its LAST command's, so
#      `cut` returning 0 MASKS sha256sum failing entirely. ⚠ And it cannot be fixed with
#      pipefail: /bin/sh on this host is dash, which has none.
#   2. A failed hash yields an EMPTY string, and `[ "" != "" ]` is FALSE — so two failed
#      measurements "match", the verification reports success, and `rm -f "$src"` deletes the
#      source. This script MOVES rather than copies, so that deletion removes the ONLY copy of
#      ground truth.
#
# THE GOVERNING RULE NOW: no source is deleted except after a PROVEN match of two hashes that
# were BOTH read successfully AND are both well-formed. An unmeasurable hash is not a mismatch
# and not a match — it is a failed measurement, which stops the step, leaves BOTH copies, and
# is RECORDED. Tag, never delete.
set -eu

# Overridable ONLY so the shipped script itself can be exercised against a sandbox tree — the
# fail-open above survived because nothing could run this file without touching real ground
# truth. The default is unchanged, and a deliberately distinct name (not plain BASE) so an
# unrelated exported variable cannot silently redirect the whole rotation.
BASE="${SCRAPLAN_BASE:-/home/gis-syr/Desktop/scraplan}"
STAMP=$(date -d yesterday +%F)
[ -n "$STAMP" ] || { echo "rotate_archive: FAILED: could not compute yesterday's date" >&2; exit 1; }

# --- off-volume archival configuration ------------------------------------- #
# Destination root on the external volume. Overridable for a different disk.
ARCHIVE_DEST="${ARCHIVE_DEST:-/media/gis-syr/EXTERNAL1/scraplan-archive}"
# Only relocate .gz at least this old, so a just-rotated file stays local long
# enough for same-day backfills and inspection.
ARCHIVE_AGE_HOURS="${ARCHIVE_AGE_HOURS:-48}"

# --- state_vectors.jsonl rotation (DEFAULT OFF — authorised deferral) ------- #
# The ADS-B time-series archive is the largest object on the host and the
# dominant consumer (docs/PROJECT_STATE.md §7): ~45 GB, growing ~9.3 GB/day. It
# lives OUTSIDE $BASE, is written by the foreign adsb_scraper service, and is
# tailed by scraplan-adsb-ingest by (inode, byte offset).
#
# It is therefore NOT rotated by the plain rotate() above: a bare `mv` would
# leave adsb_scraper's held O_APPEND handle (adsb_scraper/persistence.py:109)
# writing into the ROTATED file, so new observations would land in the archive
# we just closed while the live path stayed empty.
#
# This is an EXPLICIT, DOCUMENTED DEFERRAL, not a silent stub: the rotation
# mechanism for the live file (copytruncate vs coordinated stop) is designed and
# reported in docs/PROJECT_STATE.md §7 but awaits owner sign-off, because both
# options trade a short collection gap or a truncation race against each other.
# Turn this on ONLY together with the approved mechanism.
ROTATE_STATE_VECTORS="${ROTATE_STATE_VECTORS:-0}"
STATE_VECTORS_ARCHIVE="${STATE_VECTORS_ARCHIVE:-/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1/state_vectors.jsonl}"

# --- pre-narrowing ADS-B history (DISK-2) ---------------------------------- #
# Compressed global-era archives produced by the DISK-2 cut. These are ground
# truth, live outside $BASE, and are the first thing that should leave this volume
# when external media returns. A glob, not one filename, so a future cut is picked
# up without editing this script.
STATE_VECTORS_HISTORY="${STATE_VECTORS_HISTORY:-/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1/state_vectors_pre-narrowing_*.jsonl.gz}"

warn() { echo "rotate_archive: WARNING: $*" >&2; }
die()  { echo "rotate_archive: FAILED: $*" >&2; exit 1; }

# --- guards that cannot fail open (ROTATE-HARDEN) ---------------------------- #
# A measurement that failed is not a measurement. `[ "" -lt 5 ]` exits 2, and inside an `if`
# that reads as FALSE — so an unmeasurable gate silently permits what it exists to forbid.
is_uint() {
    case "${1:-}" in
        '' | *[!0-9]*) return 1 ;;
        *)             return 0 ;;
    esac
}

# A sha256 is exactly 64 lowercase hex characters, or it is not a hash. ⛔ This is the check
# that stands between a failed hash and `rm -f "$src"`: emptiness alone was never enough,
# because any error text would also have gone straight into the string comparison.
is_sha256() {
    case "${1:-}" in
        *[!0-9a-f]* | '')                                                 return 1 ;;
        ????????????????????????????????????????????????????????????????) return 0 ;;
        *)                                                                return 1 ;;
    esac
}

# ⛔ THE PIPELINE IS SPLIT ON PURPOSE. `sha256sum "$f" | cut -d' ' -f1` reports cut's status,
# so a failed hash looked like a successful one. Here sha256sum runs ALONE, its status is
# checked, and only then is the text trimmed — so the caller can distinguish "hashed" from
# "could not hash" instead of receiving an empty string either way.
sha256_of() {
    _so_raw=$(sha256sum "$1") || return 1
    _so_sum=${_so_raw%% *}
    is_sha256 "$_so_sum" || return 1
    printf '%s' "$_so_sum"
}

# TAG, NEVER DELETE. Every refusal is recorded at the destination in an append-only log beside
# the manifest, so a night that declined to delete leaves evidence that outlives the journal.
# ⚠ Best-effort by necessity: if the destination is the thing that is broken, this cannot
# write — which is why it is always paired with a warn() that does not depend on it.
ROTATE_FAILURE_LOG="${ROTATE_FAILURE_LOG:-ROTATE_FAILURES.log}"
tag_failure() {
    warn "$*"
    if [ -n "${ARCHIVE_DEST:-}" ] && [ -d "$ARCHIVE_DEST" ] && [ -w "$ARCHIVE_DEST" ]; then
        printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" \
            >> "$ARCHIVE_DEST/$ROTATE_FAILURE_LOG" 2>/dev/null || true
    fi
}

# The age gate decides what gets relocated AND therefore what gets deleted locally.
# `${VAR:-default}` covers unset and empty but NEVER garbage: with ARCHIVE_AGE_HOURS=abc,
# $((ARCHIVE_AGE_HOURS * 60)) evaluates the bare word as an unset variable = 0 under bash, so
# `-mmin +0` would relocate and delete a file rotated seconds ago — destroying the local copy
# same-day backfills still need. (Under dash it aborts instead; /bin/sh here is dash, so the
# deployed path fails closed — but this script is also run by hand.) Validated either way.
is_uint "$ARCHIVE_AGE_HOURS" \
    || die "ARCHIVE_AGE_HOURS must be a non-negative integer (got '$ARCHIVE_AGE_HOURS')"

# Rotate one append-only log in place (dir derived from the path).
rotate() {
    src="$1"; prefix="$2"
    d=$(dirname "$src")
    [ -s "$src" ] || { echo "nothing to rotate: $src"; return 0; }
    dst="$d/$prefix-$STAMP.jsonl"
    if [ -e "$dst" ] || [ -e "$dst.gz" ]; then
        dst="$d/$prefix-$STAMP-$(date +%H%M%S).jsonl"
    fi
    mv "$src" "$dst"
    gzip "$dst"
    echo "rotated: $dst.gz ($(du -h "$dst.gz" | cut -f1))"
}

# True only if $ARCHIVE_DEST sits on a real mount that is NOT this root volume.
archive_dest_ready() {
    dest_mount=$(findmnt -T "$ARCHIVE_DEST" -no TARGET 2>/dev/null || true)
    root_src=$(findmnt -T / -no SOURCE 2>/dev/null || true)
    dest_src=$(findmnt -T "$ARCHIVE_DEST" -no SOURCE 2>/dev/null || true)
    if [ -z "$dest_mount" ]; then
        warn "no filesystem resolves $ARCHIVE_DEST — skipping off-volume move"
        return 1
    fi
    # ⛔ THE FIFTH FAIL-OPEN, and the one that guards the documented safety property of this
    # whole script. The root-volume test below compares two MEASURED values, and the emptiness
    # of only one of them was ever checked. If `findmnt` yields an empty SOURCE for the
    # destination while returning one for /, the two are UNEQUAL — so the check PASSES and the
    # script proceeds to write into a path that may not be a mounted volume at all, which is
    # precisely the failure the header says this function exists to prevent. An unresolvable
    # source is a failed measurement: refuse, do not proceed.
    if [ -z "$dest_src" ] || [ -z "$root_src" ]; then
        warn "could not resolve the backing device of $ARCHIVE_DEST (dest='$dest_src') or of /"
        warn "  (root='$root_src') — cannot prove this is not the root volume; deleting NOTHING"
        return 1
    fi
    if [ "$dest_src" = "$root_src" ]; then
        warn "$ARCHIVE_DEST resolves to the ROOT volume ($root_src) — the external"
        warn "  disk is not mounted. Skipping the move and deleting NOTHING."
        return 1
    fi
    mkdir -p "$ARCHIVE_DEST" 2>/dev/null || {
        warn "cannot create $ARCHIVE_DEST — skipping off-volume move"
        return 1
    }
    [ -w "$ARCHIVE_DEST" ] || { warn "$ARCHIVE_DEST not writable — skipping"; return 1; }
    return 0
}

# Copy one .gz to the destination, verify by sha256 read back from the
# DESTINATION, record it in the permanent manifest, then delete the source.
# Returns non-zero on any verification failure, leaving both copies intact.
archive_one() {
    src="$1"
    base=$(basename "$src")
    # Destination layout mirrors the source tree for files under $BASE; anything
    # outside it (the foreign adsb_scraper output) goes under adsb_history/ so the
    # manifest paths stay relative and never absolute.
    case "$src" in
        "$BASE"/*) rel=$(dirname "${src#"$BASE"/}") ;;
        *)         rel="adsb_history" ;;
    esac
    out_dir="$ARCHIVE_DEST/$rel"
    mkdir -p "$out_dir"
    dst="$out_dir/$base"

    if [ -e "$dst" ]; then
        warn "$dst already exists — not overwriting, leaving $src local"
        return 0
    fi

    # ⛔ EVERY STEP FROM HERE TO `rm` IS CHECKED, because `archive_one` is invoked as
    # `archive_one "$f" || …`, and that `||` DISABLES `set -e` for the entire function body.
    # So an unchecked failure here does not abort — it falls through to the next line, which is
    # how a failed hash used to reach the comparison and the comparison used to reach the `rm`.
    if ! src_sum=$(sha256_of "$src"); then
        tag_failure "could not hash the SOURCE $src — refusing to relocate it; nothing deleted"
        return 1
    fi

    if ! cp -p "$src" "$dst.part"; then
        tag_failure "copy of $base to $dst.part failed — nothing deleted"
        rm -f "$dst.part" 2>/dev/null || true
        return 1
    fi
    sync "$dst.part" 2>/dev/null || true
    if ! mv "$dst.part" "$dst"; then
        tag_failure "could not promote $dst.part to $dst — nothing deleted"
        return 1
    fi

    # The proof: the hash of the bytes that actually landed at the DESTINATION.
    if ! dst_sum=$(sha256_of "$dst"); then
        tag_failure "could not hash the DESTINATION copy $dst — cannot prove the copy is good, so $src is KEPT"
        return 1
    fi

    # Both values are now guaranteed to be well-formed 64-hex hashes, so this comparison means
    # what it says. Before ROTATE-HARDEN two unreadable hashes reached here as "" and ""
    # and compared EQUAL, which is what authorised the deletion below.
    if [ "$src_sum" != "$dst_sum" ]; then
        tag_failure "CHECKSUM MISMATCH for $base ($src_sum != $dst_sum) — keeping BOTH copies; nothing deleted"
        return 1
    fi

    # Permanent, append-only index at the destination: `sha256sum -c` verifies
    # the relocated archive without this machine.
    #
    # ⛔ RECORDED BEFORE DELETED, and the write is checked. This append was previously
    # unchecked, so a destination that had gone full or read-only would fail to record the file
    # and the very next line would still delete the local original — leaving ground truth that
    # exists nowhere in any index. If it cannot be recorded, it does not get deleted.
    if ! printf '%s  %s\n' "$dst_sum" "$rel/$base" >> "$ARCHIVE_DEST/MANIFEST.sha256"; then
        tag_failure "could not record $rel/$base in MANIFEST.sha256 — $src is KEPT (an unindexed archive is not an archive)"
        return 1
    fi

    rm -f "$src"
    echo "archived off-volume: $rel/$base ($(du -h "$dst" | cut -f1)) sha256=$dst_sum"
}

# --- 1. rotate the live logs ----------------------------------------------- #
# Aviation + military (opensky_output)
rotate "$BASE/opensky_output/states.jsonl"        states
rotate "$BASE/opensky_output/military.jsonl"      military
# Maritime (maritime_output): every new AIS signal + operational signals
rotate "$BASE/maritime_output/ais-signals.jsonl"  ais-signals
rotate "$BASE/maritime_output/signals.jsonl"      signals
# Satellites (satellite_output): raw TLE fetches + operational signals
rotate "$BASE/satellite_output/tle.jsonl"         tle
rotate "$BASE/satellite_output/signals.jsonl"     signals

# The ADS-B time-series archive — off by default, see the deferral note above.
if [ "$ROTATE_STATE_VECTORS" = "1" ]; then
    warn "ROTATE_STATE_VECTORS=1 — rotating the LIVE ADS-B archive."
    warn "  This is only correct with the approved mechanism in place"
    warn "  (docs/PROJECT_STATE.md §7); a bare mv leaves the writer's held"
    warn "  O_APPEND handle writing into the rotated file."
    rotate "$STATE_VECTORS_ARCHIVE" state_vectors
else
    echo "state_vectors rotation: DISABLED (ROTATE_STATE_VECTORS=0) — documented deferral"
fi

# --- 2. move aged .gz off this volume -------------------------------------- #
if archive_dest_ready; then
    failed=0
    for d in opensky_output maritime_output satellite_output; do
        [ -d "$BASE/$d" ] || continue
        # -mmin instead of -mtime: hours, not whole days.
        find "$BASE/$d" -maxdepth 1 -type f -name '*.jsonl.gz' \
             -mmin +$((ARCHIVE_AGE_HOURS * 60)) -print |
        while IFS= read -r f; do
            archive_one "$f" || echo "FAILED:$f" >> "$ARCHIVE_DEST/.rotate_failures"
        done
    done
    # The pre-narrowing ADS-B history (DISK-2). It lives OUTSIDE $BASE, in the
    # foreign adsb_scraper output dir, so the loop above cannot see it — but it is
    # the single largest relocatable object the project owns (~3.48 GB holding
    # 67,891,995 lines of the global era) and it is exactly what off-volume
    # archival exists for. Same guard, same checksum proof, same delete-only-after-
    # verify rule; it just needs its explicit path.
    for f in $STATE_VECTORS_HISTORY; do
        [ -f "$f" ] || continue
        archive_one "$f" || echo "FAILED:$f" >> "$ARCHIVE_DEST/.rotate_failures"
    done
    if [ -s "$ARCHIVE_DEST/.rotate_failures" ]; then
        warn "one or more files failed verification — see $ARCHIVE_DEST/.rotate_failures"
        rm -f "$ARCHIVE_DEST/.rotate_failures"
        failed=1
    fi
    [ "$failed" -eq 0 ] || exit 1
    echo "off-volume archival complete (dest $ARCHIVE_DEST, age >${ARCHIVE_AGE_HOURS}h)"
else
    echo "off-volume archival SKIPPED — external volume not mounted; no .gz deleted"
fi
