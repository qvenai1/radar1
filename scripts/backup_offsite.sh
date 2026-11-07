#!/bin/sh
# Off-host backup over SSH (runs via scraplan-offsite.timer).
#
# WHY THIS EXISTS. Every backup this project had lived on /dev/nvme0n1p2 — the same
# single ext4 volume as the TimescaleDB Docker volume it dumps (docs/PROJECT_STATE.md
# §6.8, §7). One volume failure, one accidental mkfs, one filesystem corruption took the
# database AND every backup of it in the same event. A copy that cannot survive the loss
# of the thing it copies is not a backup. This script puts a verified copy on a SECOND
# PHYSICAL MACHINE, which is the only property that makes it one.
#
# ─────────────────────────────────────────────────────────────────────────────────────
# WHY THE SSH KEY IS PINNED — this is the whole reason the timer works.
#
# MEASURED, and it is a trap worth stating in full: plain
#   ssh -o BatchMode=yes serverq@10.10.20.57
# succeeds from an interactive shell and FAILS from a systemd timer with
# "Permission denied (publickey,password)". The reason is that the accepted key is
# ~/.ssh/serverq — not a default id_* name, and no ~/.ssh/config Host entry covers this
# address — so ssh never offers it on its own. Interactively it works only because the
# GNOME-Keyring agent (SSH_AUTH_SOCK=/run/user/1000/gcr/ssh) holds the key and offers it.
# A systemd unit has no agent and no unlocked keyring, so it gets nothing to offer.
#
# Verified both directions before this script was written:
#   env -u SSH_AUTH_SOCK ssh -o BatchMode=yes serverq@10.10.20.57   -> Permission denied
#   env -u SSH_AUTH_SOCK ssh -o BatchMode=yes -o IdentitiesOnly=yes \
#       -i ~/.ssh/serverq serverq@10.10.20.57                       -> OK
#
# So the key is pinned explicitly and IdentitiesOnly=yes stops ssh wandering off to agent
# or default identities. Had this been left to the ambient agent, the timer would have
# reported success in testing and then failed every night after the first reboot — the
# exact silent-failure shape that let the identity timer go 9 days unnoticed (§6.4).
# ─────────────────────────────────────────────────────────────────────────────────────
#
# WHY pipefail AND A MINIMUM SIZE. Inherited verbatim from backup_db.sh, for the same
# reason: `pg_dump | gzip` hides pg_dump's exit status — the shell reports only gzip's,
# and gzip succeeds happily on a truncated or empty stream. That is how a 20-byte
# opensky-2026-07-15.sql.gz looked like a valid backup for two weeks. pipefail catches a
# non-zero pg_dump; the size floor catches a pg_dump that "succeeds" while producing
# almost nothing. Neither check alone is enough.
#
# WHY THIS COPIES AND rotate_archive.sh MOVES. rotate_archive.sh RELOCATES: it deletes
# the local file once the destination copy is proven, because its purpose is to free the
# volume. This script's purpose is the opposite — redundancy — so the source must SURVIVE.
# Nothing on this host is deleted by this script except its own temporary dump, which is
# not ground truth and is reproducible from the live database in minutes.
#
# GROUND-TRUTH DISCIPLINE (identical contract to rotate_archive.sh):
#   sha256 the source -> stream over SSH -> sha256 read back FROM THE DESTINATION ->
#   compare -> only on an exact match does the file get its real name, get recorded, and
#   does retention become allowed to delete anything. A mismatch promotes nothing and
#   exits non-zero so systemd flags the unit.
#
# WHY A .part NAME AT THE DESTINATION. If SSH drops mid-stream the destination is left
# holding a partial file. Writing under <name>.part and renaming only after the
# destination's own sha256 matches means a partial can never be mistaken for a backup:
# it has no valid name, it is not in the manifest, and the next run overwrites it.
# A half-copy is not accepted, and a failed night deletes nothing.
#
# WHY RETENTION RUNS LAST AND ONLY AFTER A VERIFIED PUSH. Deleting the oldest copy before
# the newest is proven present is how a rotation scheme destroys the last good backup on
# a bad night. Nothing is pruned until tonight's file is verified at the destination.
set -eu
# POSIX sh has no pipefail; /bin/sh on this host is dash, so ask for it only when the
# shell supports it (bash/ksh/zsh do), exactly as backup_db.sh does.
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail || \
    echo "backup_offsite: WARNING: shell lacks pipefail; relying on the size gate" >&2

# --- destination ------------------------------------------------------------------- #
# Measured 2026-08-01: serverq-MS-7E06, Ubuntu 24.04, reached over the LAN via enp2s0
# (ip route get 10.10.20.57 -> via 10.0.0.1 dev enp2s0), NOT over tailscale0.
OFFSITE_USER="${OFFSITE_USER:-serverq}"
OFFSITE_HOST="${OFFSITE_HOST:-10.10.20.57}"
# Relative to the remote user's home: a non-interactive ssh command starts in $HOME.
OFFSITE_ROOT="${OFFSITE_ROOT:-scraplan-offsite}"
OFFSITE_SSH_KEY="${OFFSITE_SSH_KEY:-$HOME/.ssh/serverq}"

# How many nightly dumps to keep AT THE DESTINATION. 7 * ~3.1 GB ≈ 22 GB against 664 GB
# free (measured 2026-08-01), so this is bounded by taste, not by capacity.
OFFSITE_KEEP="${OFFSITE_KEEP:-7}"

# A genuine dump is gigabytes; 100 MB is far below any real one and far above a
# truncated stream. Same floor as backup_db.sh.
MIN_BYTES="${OFFSITE_MIN_BYTES:-104857600}"

# The pre-narrowing ADS-B history (DISK-2) — 3.48 GB of the global era, ground truth,
# living outside this repo. A glob, not one filename, so a future cut is picked up
# without editing this script. Uploaded ONCE, not nightly (see push_era below).
STATE_VECTORS_HISTORY="${STATE_VECTORS_HISTORY:-/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1/state_vectors_pre-narrowing_*.jsonl.gz}"

# Local staging for the fresh dump. Removed on success AND on failure — it is this
# script's own temp, never ground truth.
#
# DELIBERATELY A SIBLING OF ~/scraplan-backups, NOT A DIRECTORY INSIDE IT. backup_db.sh
# prunes with `find "$BACKUP_DIR" -name 'opensky-*.sql.gz' -mtime +14 -delete` and that
# find carries NO -maxdepth, so it descends into subdirectories. A stage directory under
# $BACKUP_DIR would put this script's in-flight dump inside another script's delete
# scope — harmless today only because the two timers do not overlap, which is not a
# property worth depending on. Staging outside removes the interaction entirely.
STAGE_DIR="${OFFSITE_STAGE_DIR:-$HOME/scraplan-offsite-stage}"

# The restore instructions that travel WITH the dumps (BACKUP-HARDEN item 2).
#
# WHY THIS SHIPS AT ALL. Measured in RESTORE-TEST: the obvious restore command,
# `gunzip -c dump.sql.gz | psql -d opensky`, EXITS 0 and silently destroys 9,431,499 rows,
# all four hypertable primary keys, and adds 116 duplicate indexes — while the hypertables,
# the continuous aggregate and the application's own /stats query all still look correct.
# A dump nobody can safely restore is not a backup, and the knowledge that makes it
# restorable lived only in this repository — the very thing a disaster may have taken.
#
# WHY A README AND NOT `pg_dump -Fc`. The third option was to change the dump format on the
# theory that -Fc forces pg_restore and so prevents the mistake structurally. MEASURED
# 2026-08-04 on a purpose-built reproduction, and the theory is FALSE: -Fc + pg_restore
# without timescaledb_pre_restore() produced the SAME two errors, lost the SAME rows
# (86,401 of 108,001), lost the SAME primary key and created the SAME 10 duplicate indexes —
# and pg_restore ignores errors by default too ("errors ignored on restore: 2"). The format
# is irrelevant; only pre_restore/post_restore matters. Changing it would have invalidated
# the tested procedure, broken the gzip -t gate and the opensky-*.sql.gz retention glob, and
# bought nothing.
#
# WHY IT IS RE-UPLOADED EVERY RUN rather than once: a file written once drifts out of date
# and silently stops describing the dumps beside it. It is ~5 KB, so re-sending it costs
# nothing measurable, and it goes through the same checksum-verified path as the dumps.
RESTORE_README_SRC="${RESTORE_README_SRC:-$(dirname "$0")/../docs/OFFSITE_RESTORE_README.md}"
RESTORE_README_NAME="READ_THIS_BEFORE_RESTORING.md"

DEST="$OFFSITE_USER@$OFFSITE_HOST"
# ConnectTimeout fails fast when the peer is off. ServerAlive* makes a dead TCP session
# surface as an error in ~60 s instead of hanging a timer until the next boot.
SSH_OPTS="-o BatchMode=yes -o IdentitiesOnly=yes -i $OFFSITE_SSH_KEY \
-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
-o StrictHostKeyChecking=yes"

warn() { echo "backup_offsite: WARNING: $*" >&2; }
die()  { echo "backup_offsite: FAILED: $*" >&2; exit 1; }

# --- a guard that cannot fail open (BACKUP-HARDEN item 1) ---------------------------- #
# WHY THIS EXISTS. MEASURED 2026-08-03, and it is the whole reason for this helper: the
# destination free-space gate was written as
#
#     avail=$(remote_free)
#     if [ "$avail" -lt "$need" ]; then die ...; fi
#
# When the peer became unreachable, `remote_free` produced an EMPTY string, and
# `[ "" -lt 12345 ]` is not false — it is an ERROR. dash printed
#
#     backup_offsite.sh: 143: [: Illegal number:
#
# and exited the test with status 2. Inside an `if`, any non-zero status reads as FALSE, so
# the branch was skipped and the run sailed straight past the check that exists to stop it.
# ⛔ A GUARD THAT STOPS GUARDING WHEN ITS MEASUREMENT FAILS IS WORSE THAN NO GUARD, because
# it also reports that it checked. The 08-03 run was saved only by the transfer failing for
# the same underlying reason a moment later — not by this gate.
#
# So: every comparison in this script whose input is measured (locally or over ssh) now runs
# that input through is_uint first, and a failed measurement is an explicit, logged failure.
# Never a skipped branch.
is_uint() {
    case "${1:-}" in
        '' | *[!0-9]*) return 1 ;;
        *)             return 0 ;;
    esac
}

# A sha256 is 64 lowercase hex characters. An empty or short value means the hash could not
# be computed, which must never be allowed to compare equal to anything (see the
# rotate_archive.sh finding recorded in docs/PROJECT_STATE.md §6.8).
is_sha256() {
    case "${1:-}" in
        *[!0-9a-f]* | '')                                                  return 1 ;;
        ????????????????????????????????????????????????????????????????)  return 0 ;;
        *)                                                                 return 1 ;;
    esac
}

# --- fail fast on a nonsense configuration ------------------------------------------- #
# `${VAR:-default}` covers unset and empty, but NOT garbage: with OFFSITE_KEEP=abc,
# $((OFFSITE_KEEP + 1)) evaluates the bare word as an unset variable = 0, so KEEP_FROM
# becomes 1 and `tail -n +1` selects EVERY dump for deletion. A typo in an override would
# wipe the whole off-host set, so the value is validated before it can reach retention.
is_uint "$OFFSITE_KEEP" && [ "$OFFSITE_KEEP" -ge 1 ] \
    || die "OFFSITE_KEEP must be a positive integer (got '$OFFSITE_KEEP')"
is_uint "$MIN_BYTES" && [ "$MIN_BYTES" -ge 1 ] \
    || die "OFFSITE_MIN_BYTES must be a positive integer (got '$MIN_BYTES')"

# Deliberate word-splitting of $SSH_OPTS; no path here contains a space.
# shellcheck disable=SC2086
remote() { ssh $SSH_OPTS "$DEST" "$@"; }

cleanup() { rm -rf "$STAGE_DIR"; }
trap cleanup EXIT INT TERM

# --- 1. preflight: prove the destination before touching the database ---------------- #
# A pg_dump costs minutes of DB load and gigabytes of local disk. Doing that first and
# discovering the peer is unreachable afterwards wastes both, so the cheap check runs
# first.
remote 'true' 2>/dev/null || die "destination $DEST unreachable, or key $OFFSITE_SSH_KEY rejected"

remote "mkdir -p '$OFFSITE_ROOT/db' '$OFFSITE_ROOT/adsb_history'" \
    || die "cannot create $OFFSITE_ROOT on $DEST"

# Free bytes at the destination, measured now — never assumed from an earlier run.
#
# ⛔ THE PIPELINE IS SPLIT ON PURPOSE. The original was one remote pipeline:
#     remote "df -B1 --output=avail '$OFFSITE_ROOT' | tail -1 | tr -d ' '"
# A pipeline's exit status is its LAST command's, so `tr` returning 0 masked ssh dying —
# the function reported SUCCESS while printing nothing, which is exactly how the empty
# value reached the `-lt` test on 08-03. This script's own header documents that lesson for
# `pg_dump | gzip`; it simply had not been applied here. ⚠ And it cannot be fixed by
# pipefail: /bin/sh on this host is dash, which has none (see the set -o pipefail dance at
# the top), so the fix has to be structural.
#
# Now ssh runs alone and its status is checked, then the text is trimmed locally. A failure
# returns non-zero AND prints nothing, so both the status and the value are unusable —
# and the caller validates the value anyway.
remote_free() {
    _rf_raw=$(remote "df -B1 --output=avail '$OFFSITE_ROOT'") || return 1
    printf '%s\n' "$_rf_raw" | tail -1 | tr -d ' \t\r'
}

# --- verified push: the one path every byte travels through -------------------------- #
# $1 = local source path, $2 = destination subdirectory (relative to $OFFSITE_ROOT)
push_verified() {
    src="$1"; rel="$2"
    base=$(basename "$src")
    # rel="." means the root of $OFFSITE_ROOT (the restore README lives there, beside the
    # indexes, not inside db/). Normalised here so MANIFEST/CURRENT never carry a "./" prefix
    # that `sha256sum -c` would then have to match literally.
    case "$rel" in
        . | '') dst="$OFFSITE_ROOT/$base";      relpath="$base" ;;
        *)      dst="$OFFSITE_ROOT/$rel/$base"; relpath="$rel/$base" ;;
    esac

    # An unmeasurable local size is a failed measurement, not a zero. Left unvalidated, an
    # empty $size made $((size + 1073741824)) evaluate to just the margin, so the gate below
    # would have asked the destination for 1 GiB regardless of how large the file actually is.
    size=$(stat -c %s "$src" 2>/dev/null || true)
    if ! is_uint "$size"; then
        warn "cannot measure the size of $src (got '${size}') — refusing to transfer $base"
        return 1
    fi

    # Space check before the transfer, not after a half-filled disk reports a mismatch.
    # 1 GiB of margin on top of the file so the destination never lands at exactly full.
    #
    # ⛔ A FAILED MEASUREMENT NOW STOPS THE TRANSFER. Both the status of remote_free and the
    # shape of its output are checked, because either can fail on its own: ssh can die (status)
    # and a df whose output format changes can yield something non-numeric (value). Returning
    # non-zero here means the caller decides the blast radius — `die` for the nightly dump,
    # era_failed=1 for the one-time historical file — which keeps the documented era behaviour
    # (§6.8: a stalled era upload must not cost the nightly dump its LAST_SUCCESS) instead of
    # aborting the whole run from inside a helper.
    if ! avail=$(remote_free); then
        warn "could not measure free space at $DEST (ssh failed) — refusing to transfer $base"
        return 1
    fi
    if ! is_uint "$avail"; then
        warn "free space at $DEST came back non-numeric ('${avail}') — refusing to transfer $base"
        return 1
    fi
    need=$((size + 1073741824))
    if [ "$avail" -lt "$need" ]; then
        warn "destination has ${avail}B free, needs ${need}B for $base — refusing to transfer"
        return 1
    fi

    # Validated for the same reason the destination's hash is (below): an empty local hash
    # must never reach a string comparison, where two failed hashes would compare EQUAL and
    # a corrupt transfer would be promoted as verified.
    src_sum=$(sha256sum "$src" 2>/dev/null | cut -d' ' -f1)
    if ! is_sha256 "$src_sum"; then
        warn "could not compute a local sha256 for $src (got '${src_sum}') — promoting nothing"
        return 1
    fi

    # Stream to a .part name. ssh propagates the remote command's exit status, and a
    # dropped connection surfaces as 255 — either way a failed stream leaves the .part
    # unpromoted and this function returns non-zero.
    if ! remote "cat > '$dst.part'" < "$src"; then
        warn "transfer of $base failed or was interrupted; leaving .part unpromoted"
        return 1
    fi

    # The proof: sha256 computed BY THE DESTINATION, on the bytes that actually landed.
    # ⚠ Upgraded from a bare `-z` test: emptiness was checked, but any other malformed value
    # (a df/sha256sum error message, a truncated line) would have gone straight into the
    # string comparison below. A hash is 64 hex characters or it is not a hash.
    dst_sum=$(remote "sha256sum '$dst.part' | cut -d' ' -f1" || true)
    if ! is_sha256 "$dst_sum"; then
        warn "could not read back a valid checksum for $base from the destination (got '${dst_sum}')"
        return 1
    fi
    if [ "$src_sum" != "$dst_sum" ]; then
        warn "CHECKSUM MISMATCH for $base ($src_sum != $dst_sum) — promoting nothing"
        return 1
    fi

    # Only now does it become a backup: real name, then recorded.
    remote "mv '$dst.part' '$dst'" || { warn "could not promote $base"; return 1; }

    # MANIFEST.sha256 is permanent and append-only — the full history of everything ever
    # sent, including files retention has since pruned. CURRENT.sha256 is rewritten each
    # run to list only what is present right now, so `sha256sum -c CURRENT.sha256` at the
    # destination verifies the live set without this machine. Two files because one
    # cannot be both a complete history and a checkable index of the present.
    remote "printf '%s  %s\n' '$dst_sum' '$relpath' >> '$OFFSITE_ROOT/MANIFEST.sha256'"
    echo "offsite verified: $relpath (${size}B) sha256=$dst_sum"
    return 0
}

# --- 1a. how old is the PREVIOUS verified copy? (BACKUP-HARDEN item 3) --------------- #
# Runs BEFORE this run can overwrite LAST_SUCCESS, so the journal records how long the
# off-host copy had actually been stale — the number nobody had for 44 hours on 08-03.
#
# ⚠ Informational, never fatal: this run exists to CLOSE that gap, so a stale reading must not
# stop it. The signal that matters is in the journal and in the standalone check, which §9
# documents as the thing to run. ⛔ And note what this cannot do: if the timer never fires at
# all, nothing here runs either. In-band checking catches a FAILING run, not an ABSENT one;
# that needs the standalone check on its own schedule.
# ⛔ THE CHILD'S OUTPUT IS CAPTURED AND RE-EMITTED BY THIS SCRIPT, not left to stream on its
# own. MEASURED on the 2026-08-04 02:06 verification run: the check ran and returned non-zero
# (its warning is in the journal) but NOT ONE of its own lines was recorded, while the identical
# script under `systemd-run` journals all of them. The cause is not established, so rather than
# guess, the signal is made independent of it — the parent prints the text itself, so "the
# numbers reached the journal" no longer depends on how a child's streams are inherited.
# A one-line warning that says STALE without saying how stale is most of the signal lost.
FRESHNESS_CHECK="${FRESHNESS_CHECK:-$(dirname "$0")/check_offsite_freshness.sh}"
if [ -f "$FRESHNESS_CHECK" ]; then
    if freshness_out=$(sh "$FRESHNESS_CHECK" 2>&1); then
        printf '%s\n' "$freshness_out"
    else
        printf '%s\n' "$freshness_out" >&2
        warn "the off-host copy was STALE or unmeasurable before this run (detail above); this run is attempting to close that gap"
    fi
else
    warn "freshness check not found at $FRESHNESS_CHECK — a missed night would go unreported"
fi

# --- 1b. the restore instructions travel WITH the dumps (BACKUP-HARDEN item 2) ------- #
# Deliberately BEFORE the dump: a run that later fails still refreshes the instructions, and
# the destination is never left holding dumps with no way to read them. ~5 KB, so the cost is
# not measurable against a 3 GB transfer.
#
# A failure here does NOT abort the run — the nightly dump matters more, and the exit status
# is flipped at the very end so systemd still shows the problem. Same trade as the era file.
readme_failed=0
mkdir -p "$STAGE_DIR"
if [ -f "$RESTORE_README_SRC" ]; then
    # Pushed under a name an operator cannot walk past, which is not the repo filename.
    if cp "$RESTORE_README_SRC" "$STAGE_DIR/$RESTORE_README_NAME" \
       && push_verified "$STAGE_DIR/$RESTORE_README_NAME" "."; then
        rm -f "$STAGE_DIR/$RESTORE_README_NAME"
    else
        warn "the restore README did not reach $DEST — the dumps there are undocumented,"
        warn "  and the obvious restore command silently destroys data (docs/RESTORE_PROCEDURE.md)"
        readme_failed=1
    fi
else
    warn "no restore README at $RESTORE_README_SRC — refusing to leave that silent;"
    warn "  the dumps at $DEST may now be undocumented"
    readme_failed=1
fi

# --- 2. fresh dump, guarded exactly as backup_db.sh guards its own ------------------- #
DUMP="$STAGE_DIR/opensky-$(date +%F).sql.gz"

docker exec scraplan-timescaledb pg_dump -U opensky opensky | gzip > "$DUMP" \
    || die "pg_dump/gzip pipeline returned non-zero"

dump_size=$(stat -c %s "$DUMP" 2>/dev/null || echo 0)
[ "$dump_size" -ge "$MIN_BYTES" ] \
    || die "dump only ${dump_size}B, below the ${MIN_BYTES}B floor"

# Length alone does not prove a gzip member is intact.
gzip -t "$DUMP" || die "gzip integrity check failed on the fresh dump"

echo "fresh dump staged: $DUMP (${dump_size}B)"

push_verified "$DUMP" "db" || die "nightly dump did not reach the destination intact"

# --- 3. the era file: once, gated on CONTENT not on a filename ----------------------- #
# 3.48 GB of the global era. Nightly re-upload would move ~3.5 GB every night to prove
# something that has not changed since 2026-07-29, so it is sent only when the
# destination does not already hold those exact bytes.
#
# The check is the sha256, NOT the name. A name match proves nothing: an upload
# interrupted before this script existed, or any truncated copy, carries the right
# filename and the wrong contents. Hashing 3.48 GB locally costs ~7 s at the measured
# 512 MB/s, which is a cheap price for not trusting a filename.
#
# A FAILURE HERE DOES NOT ABORT THE RUN, BUT IT DOES FAIL THE UNIT. The nightly dump has
# already arrived and verified by this point, and throwing that away because a one-time
# historical file did not transfer would be the wrong trade. So the run continues —
# retention and the index still execute — and the exit status is flipped at the very end.
# Warning without a non-zero exit would leave systemd showing green while an object is
# missing off-host, which is the silent-failure shape this project keeps paying for (§6.4).
era_failed=0
push_era() {
    for f in $STATE_VECTORS_HISTORY; do
        [ -f "$f" ] || continue
        base=$(basename "$f")
        era_sum=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
        # ⛔ An empty $era_sum would turn the test below into `grep -qx ''`, which MATCHES an
        # empty line — so a local hashing failure could report "already present and identical"
        # about a file whose bytes were never compared, and skip the upload for good.
        if ! is_sha256 "$era_sum"; then
            warn "could not compute a local sha256 for $f (got '${era_sum}'); skipping it this cycle"
            era_failed=1
            continue
        fi
        if remote "test -f '$OFFSITE_ROOT/adsb_history/$base' && sha256sum '$OFFSITE_ROOT/adsb_history/$base' | cut -d' ' -f1" \
            2>/dev/null | grep -qx "$era_sum"; then
            echo "era file already present and byte-identical: $base (skipping)"
            continue
        fi
        echo "era file missing or not matching at the destination — uploading $base"
        if ! push_verified "$f" "adsb_history"; then
            warn "era file $base did not transfer intact; will retry next cycle"
            era_failed=1
        fi
    done
}
push_era

# --- 4. retention — only now, with tonight's copy proven present --------------------- #
# Dump names are opensky-YYYY-MM-DD.sql.gz, so a lexical sort is a chronological sort.
# Everything past the newest $OFFSITE_KEEP is removed. This runs only because
# push_verified above returned success, so the set can never drop below one good copy.
KEEP_FROM=$((OFFSITE_KEEP + 1))
remote "cd '$OFFSITE_ROOT/db' && ls -1 opensky-*.sql.gz 2>/dev/null | sort -r | tail -n +$KEEP_FROM | while IFS= read -r old; do rm -f -- \"\$old\" && echo \"offsite retention: removed \$old\"; done"

# --- 5. republish the checkable index + a success marker ----------------------------- #
# CURRENT.sha256 is regenerated from what is actually on disk at the destination, so it
# never lists a file retention has just removed.
# ⚠ The README is included on purpose: `sha256sum -c CURRENT.sha256` at the destination now
# also proves the restore instructions are intact, not just the dumps. `find .` with the ./
# prefix stripped keeps the existing `db/x.sql.gz` path shape, so old MANIFEST lines and new
# CURRENT lines stay comparable.
remote "cd '$OFFSITE_ROOT' && find . -maxdepth 2 -type f \\( -name '*.gz' -o -name '$RESTORE_README_NAME' \\) | sed 's|^\\./||' | sort | xargs -r sha256sum > CURRENT.sha256"

# §9 asks a single question — did the last offsite copy succeed? — and this file answers
# it from the destination itself, without trusting this host's journal.
#
# PRECISE MEANING: "the nightly dump arrived and verified". It is written here, after a
# successful push_verified for the dump, and NOT gated on the era file — a stalled
# one-time historical upload must not make the nightly copy look stale for weeks. The
# signal for era trouble is the unit's own failed state, below.
remote "date -u '+%Y-%m-%dT%H:%M:%SZ' > '$OFFSITE_ROOT/LAST_SUCCESS'"

echo "offsite backup complete -> $DEST:$OFFSITE_ROOT (keeping $OFFSITE_KEEP dumps)"

# Flip the exit status last, so a failed era upload or a missing restore README is visible in
# `systemctl --user status` without having cost the nightly dump its retention and index.
if [ "$era_failed" -ne 0 ]; then
    echo "backup_offsite: FAILED: the nightly dump is safe off-host, but an era file did not transfer" >&2
    exit 1
fi
if [ "$readme_failed" -ne 0 ]; then
    echo "backup_offsite: FAILED: the nightly dump is safe off-host, but the restore README is not — dumps there are undocumented" >&2
    exit 1
fi
