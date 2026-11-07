#!/bin/sh
# One-shot: park the cold CSV export archive on the Windows store and reclaim the volume.
#
# WHY THIS EXISTS. MEASURED 2026-08-25: /dev/nvme0n1p2 was 88% full with 57 GB free, and
# csv_exports/ was 17 GB of it — 25 files, newest mtime 2026-08-11, and referenced by
# NOTHING in this repository except .gitignore. It is a one-off database export, not an
# input to any collector, detector, timer or API path (verified by grepping the whole tree
# for the name). It is the single largest reclaimable object that costs nothing to lose
# from this volume.
#
# ─────────────────────────────────────────────────────────────────────────────────────
# ⛔ WHAT THIS SCRIPT DELETES, AND WHAT IT DOES NOT.
#
# It deletes the 17 GB csv_exports/ DIRECTORY. It does NOT delete csv_exports.zip.
#
# That asymmetry is the whole design, so it is worth stating why rather than leaving it to
# look like an oversight. csv_exports.zip is a complete compressed copy of the directory —
# 26 entries, 17,957,224,991 bytes uncompressed, and `unzip -t` reports "No errors detected"
# (verified 2026-08-25). Sending the 3.25 GB zip therefore preserves 100% of the content for
# 19% of the bytes and 19% of the transfer time.
#
# The tempting next step is to delete the local zip too, which would free 20.25 GB instead of
# 17 GB. ⛔ DO NOT. The destination is a SINGLE ST8000DM004 HDD with no RAID (measured). If
# the local zip were deleted, that disk would hold the only copy of this archive in existence,
# and 3.25 GB is a very cheap price for the difference between "two copies on two physical
# machines" and "one copy on one spinning disk". The extra 3.25 GB of reclaim is not worth
# what it costs.
#
# ⛔ AND WHY `unzip -t` IS NOT ENOUGH ON ITS OWN TO JUSTIFY THE DELETE. `unzip -t` validates
# each member's CRC32 against the CRC stored in the archive. That proves the archive is
# INTACT. It does not prove it is COMPLETE — an archive missing a file entirely passes -t
# perfectly. Deleting 17 GB on the strength of an integrity check that cannot see an absent
# file would be exactly the "the guard reported success without checking the thing that
# matters" shape this project has paid for twice (§6.8, and the fail-open free-space gate in
# backup_offsite.sh). So the gate below compares NAME AND BYTE SIZE for every file in the
# directory against the archive's index, and refuses to delete anything if even one differs.
#
# Together the three checks cover the three ways this could go wrong:
#   unzip -t          -> the archive's bytes are not corrupt
#   name+size compare -> the archive is not missing or truncating a file
#   mtime ordering    -> nothing changed on disk after the archive was made
#     (directory newest mtime 2026-08-11 06:14; archive mtime 2026-08-16 09:24)
#
# ─────────────────────────────────────────────────────────────────────────────────────
# GROUND-TRUTH DISCIPLINE — the same contract as rotate_archive.sh, which also RELOCATES:
#   sha256 the source -> scp -> sha256 read back FROM THE DESTINATION -> compare -> only on
#   an exact match is the local copy allowed to be deleted. A mismatch deletes NOTHING.
#
# This is idempotent and safe to re-run: if the archive is already present at the destination
# and byte-identical, the upload is skipped and only the local reclaim is re-evaluated.
set -eu
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail || \
    echo "archive_to_windows: WARNING: shell lacks pipefail" >&2

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
SRC_ZIP="${SRC_ZIP:-$REPO/csv_exports.zip}"
SRC_DIR="${SRC_DIR:-$REPO/csv_exports}"

WIN_USER="${WIN_USER:-scraplanbak}"
WIN_HOST="${WIN_HOST:-10.0.0.113}"
WIN_ROOT="${WIN_ROOT:-F:\\scraplan}"
WIN_SSH_KEY="${WIN_SSH_KEY:-$HOME/.ssh/winstore}"
DEST="$WIN_USER@$WIN_HOST"

# Set to 1 to actually delete the local directory once the copy is verified. Default 0:
# a first run reports what it WOULD reclaim and touches nothing, because a script whose
# first action is deleting 17 GB should have to be asked twice.
RECLAIM="${RECLAIM:-0}"

SSH_OPTS="-o BatchMode=yes -o IdentitiesOnly=yes -i $WIN_SSH_KEY \
-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
-o StrictHostKeyChecking=yes"

# ⛔ printf, NEVER echo, FOR ANY LINE THAT CAN CARRY A WINDOWS PATH. dash's echo interprets
# backslash escapes: in `F:\scraplan\archive\x.zip` it turns \a into a BEL and — far worse —
# \c into "stop all output", so the line SILENTLY TRUNCATES. Measured: a correct transfer to
# F:\scraplan\archive\csv_exports.zip logged as "F:\scraplanrchive" and lost everything after.
# The transfer was fine; the log lied. This matters most in warn()/die(), where a truncated
# message is the only account of a failure anyone will ever see.
warn() { printf '%s\n' "archive_to_windows: WARNING: $*" >&2; }
die()  { printf '%s\n' "archive_to_windows: FAILED: $*" >&2; exit 1; }

is_uint()   { case "${1:-}" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac ; }
is_sha256() {
    case "${1:-}" in
        *[!0-9a-f]* | '')                                                  return 1 ;;
        ????????????????????????????????????????????????????????????????)  return 0 ;;
        *)                                                                 return 1 ;;
    esac
}

# See scripts/backup_windows.sh for why every remote command is base64-encoded UTF-16LE:
# the destination's default shell is cmd.exe and it splits commands at quotes and pipes.
# shellcheck disable=SC2086
win_ps() {
    _enc=$(printf '%s' "\$ProgressPreference='SilentlyContinue'; $1" \
           | iconv -f UTF-8 -t UTF-16LE | base64 -w0) || return 1
    ssh $SSH_OPTS "$DEST" "powershell -NoProfile -EncodedCommand $_enc" 2>/dev/null
}
trim() { printf '%s' "$1" | tr -d ' \t\r\n'; }

[ -f "$SRC_ZIP" ] || die "no archive at $SRC_ZIP"
[ -d "$SRC_DIR" ] || die "no directory at $SRC_DIR"

# --- gate 1: the archive's bytes are not corrupt -------------------------------------- #
echo "gate 1/3: testing archive integrity (this decompresses ~18 GB, takes a few minutes)…"
unzip -t "$SRC_ZIP" >/dev/null 2>&1 || die "unzip -t failed on $SRC_ZIP — refusing to go further"
echo "gate 1/3: OK — no errors detected in compressed data"

# --- gate 2: the archive is not missing or truncating a file -------------------------- #
# `unzip -l` lines are: <size> <date> <time> <name>. Compare name+size against the real
# directory, both sorted, and require an EXACT match of the whole listing.
echo "gate 2/3: comparing every file's name and byte size against the archive index…"
zip_list=$(unzip -l "$SRC_ZIP" \
    | awk '$1 ~ /^[0-9]+$/ && $4 != "" { name=$4; for(i=5;i<=NF;i++) name=name" "$i; if (name !~ /\/$/) { sub(/^csv_exports\//, "", name); print $1"  "name } }' \
    | sort -k2)
dir_list=$(cd "$SRC_DIR" && find . -maxdepth 1 -type f -printf '%s  %P\n' | sort -k2)

if [ "$zip_list" != "$dir_list" ]; then
    # ⛔ Temp files, NOT `comm -23 - <(...)`. Process substitution is a bashism and /bin/sh
    # on this host is dash: it parses `<(` as a syntax error and the script dies at LOAD
    # time, before any gate runs. Which is how it was found — the diagnostic path that only
    # executes on failure is exactly the path nothing exercises until it matters.
    _tz=$(mktemp) ; _td=$(mktemp)
    printf '%s\n' "$zip_list" > "$_tz"
    printf '%s\n' "$dir_list" > "$_td"
    echo "--- in the archive but not matching the directory ---" >&2
    comm -23 "$_tz" "$_td" >&2 || true
    echo "--- in the directory but not matching the archive ---" >&2
    comm -13 "$_tz" "$_td" >&2 || true
    rm -f "$_tz" "$_td"
    die "the archive does not exactly represent the directory — DELETING NOTHING"
fi
file_count=$(printf '%s\n' "$dir_list" | wc -l)
echo "gate 2/3: OK — all $file_count files match by name and byte size"

# --- gate 3: nothing changed on disk after the archive was made ----------------------- #
zip_mtime=$(stat -c %Y "$SRC_ZIP")
dir_newest=$(find "$SRC_DIR" -type f -printf '%T@\n' | cut -d. -f1 | sort -n | tail -1)
is_uint "$zip_mtime" && is_uint "$dir_newest" || die "could not measure mtimes — DELETING NOTHING"
if [ "$dir_newest" -gt "$zip_mtime" ]; then
    die "a file in $SRC_DIR is NEWER than the archive ($(date -d "@$dir_newest" '+%F %T') > $(date -d "@$zip_mtime" '+%F %T')) — the archive is stale, DELETING NOTHING"
fi
echo "gate 3/3: OK — archive ($(date -d "@$zip_mtime" '+%F %T')) is newer than every file in it"

# --- push, verified at the destination ------------------------------------------------ #
base=$(basename "$SRC_ZIP")
dst="$WIN_ROOT\\archive\\$base"

probe=$(win_ps 'Write-Output "alive"' || true)
[ "$(trim "${probe:-}")" = "alive" ] || die "destination $DEST unreachable, or key $WIN_SSH_KEY rejected"
win_ps "New-Item -ItemType Directory -Force -Path '$WIN_ROOT\\archive' | Out-Null; Write-Output ok" >/dev/null \
    || die "cannot create $WIN_ROOT\\archive on $DEST"

src_sum=$(sha256sum "$SRC_ZIP" | cut -d' ' -f1)
is_sha256 "$src_sum" || die "could not compute a local sha256 for $SRC_ZIP"
src_size=$(stat -c %s "$SRC_ZIP")
echo "local:  $base  ${src_size}B  sha256=$src_sum"

# Already there and byte-identical? Then this run has nothing to send.
have=$(win_ps "if (Test-Path -LiteralPath '$dst') { (Get-FileHash -Algorithm SHA256 -LiteralPath '$dst').Hash.ToLower() } else { Write-Output 'absent' }" || true)
have=$(trim "${have:-}")
if [ "$have" = "$src_sum" ]; then
    echo "destination already holds byte-identical $base — skipping the transfer"
else
    [ "$have" = "absent" ] || warn "destination holds a DIFFERENT $base (got '$have') — it will be replaced"

    # A killed transfer leaves the file LOCKED at a Windows destination, not merely partial
    # (measured — see scripts/backup_windows.sh trap 3). Prove the .part is gone first.
    gone=$(win_ps "Remove-Item -LiteralPath '$dst.part' -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath '$dst.part') { Write-Output 'LOCKED' } else { Write-Output 'clear' }" || true)
    [ "$(trim "${gone:-}")" = "clear" ] || die "a stale '$base.part' at $DEST is locked by another process — release it and re-run"

    # System.IO.DriveInfo and not Get-PSDrive/Get-Volume/Win32_LogicalDisk: as a non-admin
    # the other three return EMPTY with exit status 0. Measured — see win_free() in
    # scripts/backup_windows.sh for the full comparison.
    avail=$(trim "$(win_ps "\$d = New-Object System.IO.DriveInfo 'F:'; Write-Output \$d.AvailableFreeSpace" || true)")
    is_uint "$avail" || die "free space at $DEST came back non-numeric ('$avail') — refusing to transfer"
    need=$((src_size + 1073741824))
    [ "$avail" -ge "$need" ] || die "destination has ${avail}B free, needs ${need}B — refusing to transfer"

    printf '%s\n' "transferring ${src_size}B to $DEST:$dst (about $((src_size / 11070000 / 60 + 1)) min at the measured 11.07 MB/s)…"
    # shellcheck disable=SC2086
    scp $SSH_OPTS -q "$SRC_ZIP" "$DEST:$dst.part" || die "transfer failed or was interrupted; .part left unpromoted, nothing deleted"

    dst_sum=$(trim "$(win_ps "(Get-FileHash -Algorithm SHA256 -LiteralPath '$dst.part').Hash.ToLower()" || true)")
    is_sha256 "$dst_sum" || die "could not read back a valid checksum from $DEST (got '$dst_sum') — nothing deleted"
    [ "$src_sum" = "$dst_sum" ] || die "CHECKSUM MISMATCH ($src_sum != $dst_sum) — promoting nothing, deleting nothing"

    moved=$(trim "$(win_ps "Move-Item -LiteralPath '$dst.part' -Destination '$dst' -Force; if (Test-Path -LiteralPath '$dst') { Write-Output 'ok' } else { Write-Output 'FAIL' }" || true)")
    [ "$moved" = "ok" ] || die "could not promote $base at the destination (got '$moved') — nothing deleted"

    win_ps "Add-Content -LiteralPath '$WIN_ROOT\\MANIFEST.sha256' -Encoding ascii -Value '$dst_sum  archive/$base'" >/dev/null \
        || warn "pushed archive/$base but could not append it to MANIFEST.sha256"
    printf '%s\n' "verified at destination: archive/$base sha256=$dst_sum"
fi

# --- provenance travels with the archive ---------------------------------------------- #
# WHY: on the far side this is a bare zip on a disk full of other projects' folders. Six
# months from now the question "what is this and is it safe to delete" has to be answerable
# from the destination alone, without this repository — which is precisely the thing a
# disaster may have taken.
win_ps "Set-Content -LiteralPath '$WIN_ROOT\\archive\\$base.provenance.txt' -Encoding ascii -Value @(
 'csv_exports.zip — Atheer RADAR (scraplan) cold CSV export archive',
 '',
 'WHAT: a complete zip of csv_exports/, a one-off export of the opensky database taken',
 '      2026-08-11. $file_count files, ${src_size} bytes compressed.',
 'SOURCE HOST: gis-syr-ThinkCentre-neo-50t-Gen-5, /home/gis-syr/Desktop/scraplan/',
 'SHA256: $src_sum',
 'PUSHED BY: scripts/archive_to_windows.sh',
 '',
 'STATUS: this is a SECOND copy. The same zip is retained on the source host.',
 'DERIVED DATA: every file here is an export of the opensky TimescaleDB database and is',
 'reproducible from a database restore. It is not irreplaceable ground truth.',
 '',
 'VERIFY: sha256sum -c against MANIFEST.sha256 one directory up.'
)" >/dev/null || warn "could not write the provenance file next to the archive"

# --- reclaim, only now, and only when asked ------------------------------------------- #
dir_bytes=$(du -sb "$SRC_DIR" | cut -f1)
if [ "$RECLAIM" != "1" ]; then
    echo ""
    echo "DRY RUN — nothing deleted. The copy at $DEST is verified."
    echo "  would delete: $SRC_DIR (${dir_bytes}B)"
    echo "  would keep:   $SRC_ZIP (${src_size}B) — the local half of a two-machine pair"
    echo "  re-run with RECLAIM=1 to reclaim the space."
    exit 0
fi

echo "reclaiming $SRC_DIR (${dir_bytes}B)…"
rm -rf "$SRC_DIR"
[ -d "$SRC_DIR" ] && die "delete did not complete — $SRC_DIR still present"
printf '%s\n' "reclaimed ${dir_bytes}B; $SRC_ZIP retained locally, verified copy at $DEST:$dst"
df -h /home/gis-syr | tail -1
