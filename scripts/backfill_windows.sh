#!/bin/sh
# Push every local dump the Windows store does not already hold (WINSTORE backfill).
#
# WHY THIS EXISTS, AND WHY IT WAS NEEDED THE DAY THE WINDOWS TIER WAS BUILT.
#
# scripts/backup_windows.sh keeps 90 nightly dumps, but it only ever sends TONIGHT's. On the
# day it was deployed the store therefore held exactly ONE dump, while the local directory
# held eleven — and local retention was being cut from 14 days to 4 in the same change. Run
# in that order, the cut would have deleted history that existed in only one place.
#
# ⛔ MEASURED 2026-08-25, and this is the finding that made the script necessary rather than
# merely tidy. Comparing all three tiers file by file:
#
#   date      local  serverq                       windows
#   08-14      yes   NO                            no      <- one copy in the world
#   08-15      yes   NO                            no      <- one copy in the world
#   08-16      yes   yes                           no
#   08-17      yes   yes                           no
#   08-18      yes   ONLY AN UNPROMOTED .part      no      <- one copy in the world
#   08-19      yes   yes                           no
#   08-20      yes   yes                           no
#
# Two things worth naming. First, `opensky-2026-08-18.sql.gz.part` on serverq is a push that
# FAILED AND WAS NEVER RETRIED — the .part discipline worked exactly as designed (a partial
# was never promoted to a name that looks restorable) but nothing ever went back for it, so
# 08-18 quietly had one copy for a week. Second, 08-14 and 08-15 predate serverq's 7-day
# window entirely. Nothing was broken; the retention depths simply did not overlap the way
# everyone assumed they did.
#
# So: run this BEFORE letting a shortened local retention take effect, and after any outage
# that left the Windows store behind.
#
# CONTRACT — identical to scripts/backup_windows.sh, deliberately:
#   sha256 the source -> scp to a .part -> sha256 read back FROM THE DESTINATION -> compare
#   -> promote -> record. A mismatch promotes nothing. Nothing local is ever deleted here.
#
# Idempotent: a dump already present at the destination with a matching sha256 is skipped,
# so this is safe to re-run and cheap when there is nothing to do.
set -eu
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail || \
    echo "backfill_windows: WARNING: shell lacks pipefail" >&2

WIN_USER="${WIN_USER:-scraplanbak}"
WIN_HOST="${WIN_HOST:-10.0.0.113}"
WIN_ROOT="${WIN_ROOT:-F:\\scraplan}"
WIN_SSH_KEY="${WIN_SSH_KEY:-$HOME/.ssh/winstore}"
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-$HOME/scraplan-backups}"
MIN_BYTES="${WIN_MIN_BYTES:-104857600}"
DEST="$WIN_USER@$WIN_HOST"

SSH_OPTS="-o BatchMode=yes -o IdentitiesOnly=yes -i $WIN_SSH_KEY \
-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
-o StrictHostKeyChecking=yes"

# ⛔ printf, NEVER echo, FOR ANY LINE THAT CAN CARRY A WINDOWS PATH. dash's echo interprets
# backslash escapes: in `F:\scraplan\archive\x.zip` it turns \a into a BEL and — far worse —
# \c into "stop all output", so the line SILENTLY TRUNCATES. Measured: a correct transfer to
# F:\scraplan\archive\csv_exports.zip logged as "F:\scraplanrchive" and lost everything after.
# The transfer was fine; the log lied. This matters most in warn()/die(), where a truncated
# message is the only account of a failure anyone will ever see.
warn() { printf '%s\n' "backfill_windows: WARNING: $*" >&2; }
die()  { printf '%s\n' "backfill_windows: FAILED: $*" >&2; exit 1; }

is_uint()   { case "${1:-}" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac ; }
is_sha256() {
    case "${1:-}" in
        *[!0-9a-f]* | '')                                                  return 1 ;;
        ????????????????????????????????????????????????????????????????)  return 0 ;;
        *)                                                                 return 1 ;;
    esac
}

# Base64-encoded UTF-16LE because the destination's default shell is cmd.exe and it splits
# commands at quotes and pipes. See docs/ENVIRONMENT.md §3, trap 2.
# shellcheck disable=SC2086
win_ps() {
    _enc=$(printf '%s' "\$ProgressPreference='SilentlyContinue'; $1" \
           | iconv -f UTF-8 -t UTF-16LE | base64 -w0) || return 1
    ssh $SSH_OPTS "$DEST" "powershell -NoProfile -EncodedCommand $_enc" 2>/dev/null
}
trim() { printf '%s' "$1" | tr -d ' \t\r\n'; }

probe=$(win_ps 'Write-Output "alive"' || true)
[ "$(trim "${probe:-}")" = "alive" ] || die "destination $DEST unreachable, or key $WIN_SSH_KEY rejected"
win_ps "New-Item -ItemType Directory -Force -Path '$WIN_ROOT\\db' | Out-Null; Write-Output ok" >/dev/null \
    || die "cannot create $WIN_ROOT\\db on $DEST"

pushed=0; skipped=0; failed=0

for src in "$LOCAL_BACKUP_DIR"/opensky-*.sql.gz; do
    [ -f "$src" ] || continue          # an unmatched glob is a literal, not a file
    base=$(basename "$src")
    dst="$WIN_ROOT\\db\\$base"

    size=$(stat -c %s "$src" 2>/dev/null || true)
    if ! is_uint "$size" || [ "$size" -lt "$MIN_BYTES" ]; then
        warn "$base is ${size:-unmeasurable}B, below the ${MIN_BYTES}B floor — skipping (not propagating a broken dump)"
        failed=$((failed + 1)); continue
    fi

    # Integrity BEFORE the wire, not after: a corrupt local dump copied faithfully is still
    # a corrupt dump, and it would arrive with a perfectly matching checksum.
    if ! gzip -t "$src" 2>/dev/null; then
        warn "$base fails gzip -t — skipping (refusing to propagate a corrupt dump)"
        failed=$((failed + 1)); continue
    fi

    src_sum=$(sha256sum "$src" 2>/dev/null | cut -d' ' -f1)
    if ! is_sha256 "$src_sum"; then
        warn "could not compute a local sha256 for $base — skipping"
        failed=$((failed + 1)); continue
    fi

    have=$(trim "$(win_ps "if (Test-Path -LiteralPath '$dst') { (Get-FileHash -Algorithm SHA256 -LiteralPath '$dst').Hash.ToLower() } else { Write-Output 'absent' }" || true)")
    if [ "$have" = "$src_sum" ]; then
        echo "skip (already present, byte-identical): $base"
        skipped=$((skipped + 1)); continue
    fi
    [ "$have" = "absent" ] || warn "$base exists at $DEST with a DIFFERENT hash ('$have') — replacing it"

    # A killed transfer leaves a LOCKED file at a Windows destination, not merely a partial
    # one. Prove the .part is gone before writing. See docs/ENVIRONMENT.md §3, trap 3.
    gone=$(trim "$(win_ps "Remove-Item -LiteralPath '$dst.part' -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath '$dst.part') { Write-Output 'LOCKED' } else { Write-Output 'clear' }" || true)")
    if [ "$gone" != "clear" ]; then
        warn "a stale '$base.part' at $DEST is locked or unreadable (got '$gone') — skipping"
        failed=$((failed + 1)); continue
    fi

    avail=$(trim "$(win_ps "\$d = New-Object System.IO.DriveInfo 'F:'; Write-Output \$d.AvailableFreeSpace" || true)")
    if ! is_uint "$avail"; then
        warn "free space at $DEST came back non-numeric ('$avail') — skipping $base"
        failed=$((failed + 1)); continue
    fi
    if [ "$avail" -lt $((size + 1073741824)) ]; then
        warn "destination has ${avail}B free, needs $((size + 1073741824))B for $base — skipping"
        failed=$((failed + 1)); continue
    fi

    echo "pushing $base (${size}B, about $((size / 11000000 / 60 + 1)) min at the measured 11.0 MB/s)…"
    # shellcheck disable=SC2086
    if ! scp $SSH_OPTS -q "$src" "$DEST:$dst.part"; then
        warn "transfer of $base failed or was interrupted; .part left unpromoted"
        failed=$((failed + 1)); continue
    fi

    dst_sum=$(trim "$(win_ps "(Get-FileHash -Algorithm SHA256 -LiteralPath '$dst.part').Hash.ToLower()" || true)")
    if ! is_sha256 "$dst_sum" || [ "$src_sum" != "$dst_sum" ]; then
        warn "CHECKSUM MISMATCH or unreadable for $base ('$dst_sum' vs '$src_sum') — promoting nothing"
        failed=$((failed + 1)); continue
    fi

    moved=$(trim "$(win_ps "Move-Item -LiteralPath '$dst.part' -Destination '$dst' -Force; if (Test-Path -LiteralPath '$dst') { Write-Output 'ok' } else { Write-Output 'FAIL' }" || true)")
    if [ "$moved" != "ok" ]; then
        warn "could not promote $base at $DEST (got '$moved')"
        failed=$((failed + 1)); continue
    fi

    win_ps "Add-Content -LiteralPath '$WIN_ROOT\\MANIFEST.sha256' -Encoding ascii -Value '$dst_sum  db/$base'" >/dev/null \
        || warn "pushed db/$base but could not append it to MANIFEST.sha256"
    printf '%s\n' "backfilled: db/$base sha256=$dst_sum"
    pushed=$((pushed + 1))
done

# Republish the checkable index so it describes what is actually there now.
#
# ⛔ HASHES COME FROM MANIFEST.sha256; NOTHING IS RE-HASHED. Identical reasoning to
# scripts/backup_windows.sh §5, and it bites harder here: this script exists to fill the
# store, so by the time it finishes there is the most data to pointlessly re-read. At the
# target depth that is ~345 GB off a spinning disk to re-derive numbers this script just
# computed and verified file by file.
#
# ⚠ It also FIXES a filter bug the naive version carried: `-like '*.gz' -or -like '*.md'`
# silently omitted archive/csv_exports.zip, so the index quietly under-reported what the
# store holds. Keying off the manifest means anything that was verified is listed and
# anything that was not is not — no filename pattern to keep in sync with reality.
win_ps "\$root = '$WIN_ROOT'
\$man = @{}
if (Test-Path -LiteralPath (\$root + '\\MANIFEST.sha256')) {
  foreach (\$l in Get-Content -LiteralPath (\$root + '\\MANIFEST.sha256')) {
    if (\$l -match '^([0-9a-f]{64})  (.+)\$') { \$man[\$matches[2]] = \$matches[1] }
  }
}
\$lines = foreach (\$rel in (\$man.Keys | Sort-Object)) {
  if (Test-Path -LiteralPath (\$root + '\\' + \$rel.Replace('/','\\'))) { \$man[\$rel] + '  ' + \$rel }
}
Set-Content -LiteralPath (\$root + '\\CURRENT.sha256') -Encoding ascii -Value \$lines" >/dev/null \
    || warn "could not republish CURRENT.sha256"

echo "backfill complete: $pushed pushed, $skipped already present, $failed failed"
[ "$failed" -eq 0 ] || exit 1
