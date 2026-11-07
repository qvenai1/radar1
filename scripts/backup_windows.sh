#!/bin/sh
# Deep-retention backup to the Windows store on F: (runs via scraplan-windows.timer).
#
# WHY THIS EXISTS, AND WHY IT IS NOT A SECOND backup_offsite.sh. serverq@10.10.20.57 holds
# SEVEN nightly dumps (scripts/backup_offsite.sh, OFFSITE_KEEP=7) and ~/scraplan-backups
# holds fourteen — but both are bounded by DISK, not by how far back an incident might need
# to be reconstructed from. MEASURED 2026-08-25: the local series had grown to 38 GB on a
# volume with 57 GB free, so local retention is about to become a disk-pressure decision
# rather than a recovery decision. The Windows store has 6,964 GB free (§ below), which
# makes depth free: 90 nightly dumps at the measured 3.83 GB is 345 GB, or 5% of it.
#
# So the three tiers have three DIFFERENT jobs, and none replaces another:
#   ~/scraplan-backups  (4 days)  — same volume as the DB. FAST restore. Dies with the volume.
#   serverq  (7 days)             — second machine, Linux. The proven survivor of a volume loss.
#   F: on 10.0.0.113 (90 days)    — third machine, deep history. Survives a bad WEEK, not just
#                                   a bad night: a corruption noticed on day 20 is still
#                                   recoverable here and nowhere else.
#
# ─────────────────────────────────────────────────────────────────────────────────────
# WHAT IS DIFFERENT ABOUT A WINDOWS DESTINATION — four measured traps, all of which this
# script is shaped around. None of them exist on the serverq path.
#
# 1. ⛔ `ssh host "cat > file"` DOES NOT WORK. The destination's default shell is cmd.exe
#    (HKLM:\SOFTWARE\OpenSSH has no DefaultShell), and PowerShell's stdin is a TEXT stream:
#    MEASURED 2026-08-25, streaming 300 MB through
#      powershell -Command "$i=[Console]::OpenStandardInput(); $i.CopyTo($o)"
#    moved 135,168 bytes in 300 seconds and then had to be killed. Every byte here travels
#    by scp instead, which uses the sftp-server.exe subsystem and moved the same 300 MB in
#    27.1 s (11.07 MB/s) with a byte-identical sha256 at the far end.
#
# 2. ⛔ EVERY REMOTE COMMAND IS BASE64-ENCODED. cmd.exe eats quotes and pipes: a plain
#    `ssh host 'powershell -Command "... | Select-String ..."'` failed with
#      'PasswordAuth' is not recognized as an internal or external command
#    because cmd.exe split the command at the pipe. `-EncodedCommand <base64 UTF-16LE>` has
#    no metacharacters for cmd.exe to find, so it is immune. See win_ps() below.
#
# 3. ⛔ A KILLED TRANSFER LEAVES A LOCKED FILE, NOT JUST A PARTIAL ONE. MEASURED: after the
#    stalled transfer in trap 1 was killed, the destination still held the file open —
#    Get-FileHash and Remove-Item both failed with "because it is being used by another
#    process", and a retry to the same name could not overwrite it. On Linux the .part name
#    is enough; here the script must also prove the .part is GONE before writing, and say so
#    loudly if it cannot, because a locked stale .part makes every future run fail the same
#    way for a reason the error message does not name.
#
# 4. ⛔ POWERSHELL ERRORS COME BACK AS CLIXML ON STDERR, NOT AS PLAIN TEXT. A failing remote
#    command can therefore return status 0 with empty stdout and an XML blob on stderr. That
#    is precisely the failed-measurement shape §6.8 documents, so every measured value here
#    goes through is_uint/is_sha256 before it can reach a comparison — inherited from
#    backup_offsite.sh, and load-bearing for a different reason.
#
# ─────────────────────────────────────────────────────────────────────────────────────
# WHY THE KEY IS PINNED, AND WHY THE ACCOUNT IS NOT AN ADMINISTRATOR.
#
# Pinned for the reason backup_offsite.sh's header sets out in full: a user unit has no
# ssh-agent and no unlocked keyring, so a key that is not offered explicitly is not offered
# at all. IdentitiesOnly=yes stops ssh wandering off to the agent.
#
# The ACCOUNT is separate and specific to this host. MEASURED 2026-08-25, before any change:
# the login offered for this machine was A001, and A001 is a LOCAL ADMINISTRATOR
# (IsInRole(544) = True) with password authentication enabled. A stored credential for that
# account would have put full administrative control of a machine holding several unrelated
# archives into a file on this host. So this script authenticates as `scraplanbak`, which
# was created for it: not in Administrators (verified by enumerating the group, not by
# assuming), key-only, and denied write access to everything on F: except F:\scraplan.
#
# ⚠ AND A TRAP THAT COST AN HOUR, RECORDED SO IT IS NOT REDISCOVERED: the destination's
# sshd_config carries
#     Match Group administrators
#            AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
# so for an administrator a key in ~/.ssh/authorized_keys is IGNORED. And for a brand-new
# account there is no ~ at all — MEASURED: scraplanbak had no ProfileList registry entry, so
# sshd could not resolve its home and rejected a correctly-installed key with a bare
# "Permission denied (publickey)". The fix is a profile-independent path, set in sshd_config:
#     Match User scraplanbak
#            AuthorizedKeysFile __PROGRAMDATA__/ssh/scraplanbak_authorized_keys
#            PasswordAuthentication no
#            KbdInteractiveAuthentication no
#
# ⚠ BOTH auth lines are required. `PasswordAuthentication no` ALONE IS COSMETIC on Windows,
# because keyboard-interactive is a second route to the same password check. Verified by
# contrast rather than asserted — with only the first line, `ssh -o PubkeyAuthentication=no`
# to this account still offered `publickey,keyboard-interactive`; with both, it offers
# `publickey` and nothing else, while A001 still offers all three.
#
# GROUND-TRUTH DISCIPLINE — identical contract to backup_offsite.sh, which is the point:
#   sha256 the source -> scp -> sha256 read back FROM THE DESTINATION -> compare -> only on
#   an exact match does the file get its real name and become eligible to be counted by
#   retention. A mismatch promotes nothing and exits non-zero.
#
# WHY RETENTION RUNS LAST AND ONLY AFTER A VERIFIED PUSH. Deleting the oldest copy before
# the newest is proven present is how a rotation scheme destroys the last good backup on a
# bad night. Nothing is pruned until tonight's file is verified at the destination.
set -eu
# POSIX sh has no pipefail; /bin/sh on this host is dash, so ask for it only when the shell
# supports it, exactly as backup_db.sh and backup_offsite.sh do.
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail || \
    echo "backup_windows: WARNING: shell lacks pipefail; relying on the size gate" >&2

# --- destination --------------------------------------------------------------------- #
# MEASURED 2026-08-25 (docs/ENVIRONMENT.md §3, "LAN throughput to the Windows store"):
# Windows 11 Pro build 26200 on 10.0.0.113, reached over the LAN via enp2s0. F: is a
# 7,452 GB NTFS volume with 6,964 GB free, backed by disk 0, a single ST8000DM004 SATA HDD.
# ⚠ SINGLE DISK, NO RAID. This is a THIRD COPY, never a safe place by itself.
WIN_USER="${WIN_USER:-scraplanbak}"
WIN_HOST="${WIN_HOST:-10.0.0.113}"
# An absolute Windows path. Not relative to a home directory: this account deliberately has
# no usable profile (see the header), so $HOME on the far side is not a thing to rely on.
WIN_ROOT="${WIN_ROOT:-F:\\scraplan}"
WIN_SSH_KEY="${WIN_SSH_KEY:-$HOME/.ssh/winstore}"

# How many nightly dumps to keep AT THE WINDOWS STORE. 90 * ~3.83 GB = ~345 GB against
# 6,964 GB free (measured 2026-08-25), so this is bounded by taste, not by capacity — and
# the depth is the entire reason this tier exists.
WIN_KEEP="${WIN_KEEP:-90}"

# A genuine dump is gigabytes; 100 MB is far below any real one and far above a truncated
# stream. Same floor as backup_db.sh and backup_offsite.sh.
MIN_BYTES="${WIN_MIN_BYTES:-104857600}"

# Local staging for the fresh dump. A SIBLING of ~/scraplan-backups, never a directory
# inside it — backup_db.sh prunes with a `find` that carries no -maxdepth, so a stage
# directory under $BACKUP_DIR would put this script's in-flight dump inside another
# script's delete scope. backup_offsite.sh avoids this the same way and for the same reason.
STAGE_DIR="${WIN_STAGE_DIR:-$HOME/scraplan-windows-stage}"

# The restore instructions travel WITH the dumps. WHY: measured in RESTORE-TEST, the obvious
# restore command `gunzip -c dump.sql.gz | psql -d opensky` EXITS 0 and silently destroys
# 9,431,499 rows, all four hypertable primary keys, and adds 116 duplicate indexes. A dump
# nobody can safely restore is not a backup, and the knowledge that makes it restorable
# lives in this repository — the very thing a disaster may have taken.
RESTORE_README_SRC="${RESTORE_README_SRC:-$(dirname "$0")/../docs/OFFSITE_RESTORE_README.md}"
RESTORE_README_NAME="READ_THIS_BEFORE_RESTORING.md"

DEST="$WIN_USER@$WIN_HOST"
# ConnectTimeout fails fast when the peer is off. ServerAlive* makes a dead TCP session
# surface as an error in ~60 s instead of hanging a timer until the next boot.
SSH_OPTS="-o BatchMode=yes -o IdentitiesOnly=yes -i $WIN_SSH_KEY \
-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
-o StrictHostKeyChecking=yes"

# ⛔ printf, NEVER echo, FOR ANY LINE THAT CAN CARRY A WINDOWS PATH. dash's echo interprets
# backslash escapes: in `F:\scraplan\archive\x.zip` it turns \a into a BEL and — far worse —
# \c into "stop all output", so the line SILENTLY TRUNCATES. Measured: a correct transfer to
# F:\scraplan\archive\csv_exports.zip logged as "F:\scraplanrchive" and lost everything after.
# The transfer was fine; the log lied. This matters most in warn()/die(), where a truncated
# message is the only account of a failure anyone will ever see.
warn() { printf '%s\n' "backup_windows: WARNING: $*" >&2; }
die()  { printf '%s\n' "backup_windows: FAILED: $*" >&2; exit 1; }

# --- guards that cannot fail open ----------------------------------------------------- #
# Inherited verbatim from backup_offsite.sh, where the lesson was paid for: a free-space
# gate whose measurement returned an EMPTY string turned `[ "" -lt 12345 ]` into an ERROR,
# dash exited the test with status 2, and inside an `if` that reads as FALSE — so the run
# sailed straight past the check that exists to stop it.
# ⛔ A GUARD THAT STOPS GUARDING WHEN ITS MEASUREMENT FAILS IS WORSE THAN NO GUARD, because
# it also reports that it checked.
is_uint() {
    case "${1:-}" in
        '' | *[!0-9]*) return 1 ;;
        *)             return 0 ;;
    esac
}

# A sha256 is 64 lowercase hex characters. An empty or short value must never be allowed to
# compare equal to anything — two failed hashes comparing EQUAL is how a corrupt transfer
# gets promoted as verified.
is_sha256() {
    case "${1:-}" in
        *[!0-9a-f]* | '')                                                  return 1 ;;
        ????????????????????????????????????????????????????????????????)  return 0 ;;
        *)                                                                 return 1 ;;
    esac
}

# --- fail fast on a nonsense configuration -------------------------------------------- #
# `${VAR:-default}` covers unset and empty but NOT garbage: with WIN_KEEP=abc, the retention
# arithmetic below would select EVERY dump for deletion. A typo in an override must not be
# able to wipe 90 days of history.
is_uint "$WIN_KEEP" && [ "$WIN_KEEP" -ge 1 ] \
    || die "WIN_KEEP must be a positive integer (got '$WIN_KEEP')"
is_uint "$MIN_BYTES" && [ "$MIN_BYTES" -ge 1 ] \
    || die "WIN_MIN_BYTES must be a positive integer (got '$MIN_BYTES')"

# --- the ONE way a remote command is ever run ----------------------------------------- #
# See trap 2 in the header: cmd.exe is the default shell and mangles quotes and pipes, so
# the command is handed over as base64-encoded UTF-16LE where there are no metacharacters
# left for it to find. $ProgressPreference is silenced because PowerShell otherwise emits
# CLIXML progress records on stderr that swamp the real output.
#
# ⛔ stdout ONLY is returned, and ssh runs ALONE — not inside a pipeline. A pipeline's exit
# status is its LAST command's, so a `| tr` returning 0 would mask ssh dying and the
# function would report SUCCESS while printing nothing. That is exactly how an empty value
# reached a `-lt` test on the serverq path on 2026-08-03. Trimming happens in the caller.
# shellcheck disable=SC2086
win_ps() {
    _enc=$(printf '%s' "\$ProgressPreference='SilentlyContinue'; $1" \
           | iconv -f UTF-8 -t UTF-16LE | base64 -w0) || return 1
    ssh $SSH_OPTS "$DEST" "powershell -NoProfile -EncodedCommand $_enc" 2>/dev/null
}

# Trim CR (the far side is Windows; every line comes back CRLF) and surrounding whitespace.
trim() { printf '%s' "$1" | tr -d ' \t\r\n'; }

cleanup() { rm -rf "$STAGE_DIR"; }
trap cleanup EXIT INT TERM

# --- 1. preflight: prove the destination before touching the database ----------------- #
# A pg_dump costs minutes of DB load and gigabytes of local disk. Doing that first and
# discovering the peer is unreachable afterwards wastes both.
probe=$(win_ps 'Write-Output "alive"' || true)
[ "$(trim "${probe:-}")" = "alive" ] \
    || die "destination $DEST unreachable, or key $WIN_SSH_KEY rejected (got '${probe:-}')"

win_ps "New-Item -ItemType Directory -Force -Path '$WIN_ROOT','$WIN_ROOT\\db' | Out-Null; Write-Output ok" >/dev/null \
    || die "cannot create $WIN_ROOT on $DEST"

# Free bytes at the destination, measured now — never assumed from an earlier run.
#
# ⛔ System.IO.DriveInfo, AND THE THREE OBVIOUS ALTERNATIVES ALL RETURN EMPTY HERE. MEASURED
# 2026-08-25 as this account (not as an administrator — the numbers differ, which is the
# whole point):
#     (Get-PSDrive F).Free                     -> ''      (and .Used -> 0)
#     (Get-CimInstance Win32_LogicalDisk).Free -> ''      (CIM needs elevation)
#     (Get-Volume -DriveLetter F).SizeRemaining-> ''      (Storage module needs elevation)
#     New-Object System.IO.DriveInfo 'F:'      -> 7477827284992   ✅
# The first three are not errors — they return EMPTY with exit status 0, which is the exact
# failed-measurement shape that is_uint exists to catch. This gate was written with
# Get-PSDrive first and the very first end-to-end run refused to transfer anything, correctly,
# with "free space came back non-numeric ('')". The guard did its job; the measurement was
# simply wrong, and a guard that failed OPEN would have hidden that.
#
# AvailableFreeSpace rather than TotalFreeSpace on purpose: it is the space available to THIS
# user after any quota, which is the number this gate actually needs to know.
win_free() {
    _wf_raw=$(win_ps "\$d = New-Object System.IO.DriveInfo 'F:'; Write-Output \$d.AvailableFreeSpace") || return 1
    trim "$_wf_raw"
}

# --- verified push: the one path every byte travels through --------------------------- #
# $1 = local source path, $2 = destination subdirectory (relative to $WIN_ROOT, or "." )
push_verified() {
    src="$1"; rel="$2"
    base=$(basename "$src")
    case "$rel" in
        . | '') dst="$WIN_ROOT\\$base";      relpath="$base" ;;
        *)      dst="$WIN_ROOT\\$rel\\$base"; relpath="$rel/$base" ;;
    esac

    # An unmeasurable local size is a failed measurement, not a zero.
    size=$(stat -c %s "$src" 2>/dev/null || true)
    if ! is_uint "$size"; then
        warn "cannot measure the size of $src (got '${size}') — refusing to transfer $base"
        return 1
    fi

    # Space check before the transfer, not after a half-filled disk reports a mismatch.
    # Both the STATUS of win_free and the SHAPE of its output are checked, because either
    # can fail on its own: ssh can die (status), and a PowerShell error returns empty stdout
    # with CLIXML on stderr (value).
    if ! avail=$(win_free); then
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

    # Validated for the same reason the destination's hash is: an empty local hash must
    # never reach a string comparison.
    src_sum=$(sha256sum "$src" 2>/dev/null | cut -d' ' -f1)
    if ! is_sha256 "$src_sum"; then
        warn "could not compute a local sha256 for $src (got '${src_sum}') — promoting nothing"
        return 1
    fi

    # ⛔ CLEAR A STALE .part FIRST, AND PROVE IT IS GONE. See trap 3 in the header: a killed
    # transfer leaves the destination holding the file OPEN, and every later run then fails
    # to overwrite it with an error that never says "a previous run was killed". Remove it
    # and read back Test-Path, so a lock is reported as a lock instead of as a mystery.
    gone=$(win_ps "Remove-Item -LiteralPath '$dst.part' -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath '$dst.part') { Write-Output 'LOCKED' } else { Write-Output 'clear' }" || true)
    case "$(trim "${gone:-}")" in
        clear) : ;;
        LOCKED) warn "a stale '$base.part' at $DEST is LOCKED by another process (a killed"
                warn "  earlier run). Nothing can be written until it is released — reboot the"
                warn "  destination or end the holding process, then re-run."
                return 1 ;;
        *)      warn "could not establish the state of '$base.part' at $DEST (got '${gone:-}')"
                return 1 ;;
    esac

    # Stream to a .part name over scp — NOT `ssh "cat >"`, which moves 135 KB in 300 s here
    # (trap 1). scp propagates failure, and a dropped connection leaves the .part unpromoted.
    # shellcheck disable=SC2086
    if ! scp $SSH_OPTS -q "$src" "$DEST:$dst.part"; then
        warn "transfer of $base failed or was interrupted; leaving .part unpromoted"
        return 1
    fi

    # The proof: sha256 computed BY THE DESTINATION, on the bytes that actually landed.
    dst_sum=$(win_ps "(Get-FileHash -Algorithm SHA256 -LiteralPath '$dst.part').Hash.ToLower()" || true)
    dst_sum=$(trim "${dst_sum:-}")
    if ! is_sha256 "$dst_sum"; then
        warn "could not read back a valid checksum for $base from $DEST (got '${dst_sum}')"
        return 1
    fi
    if [ "$src_sum" != "$dst_sum" ]; then
        warn "CHECKSUM MISMATCH for $base ($src_sum != $dst_sum) — promoting nothing"
        return 1
    fi

    # Only now does it become a backup: real name, then recorded.
    moved=$(win_ps "Move-Item -LiteralPath '$dst.part' -Destination '$dst' -Force; if (Test-Path -LiteralPath '$dst') { Write-Output 'ok' } else { Write-Output 'FAIL' }" || true)
    [ "$(trim "${moved:-}")" = "ok" ] || { warn "could not promote $base (got '${moved:-}')"; return 1; }

    # MANIFEST.sha256 is permanent and append-only — the full history of everything ever
    # sent, including files retention has since pruned. CURRENT.sha256 is rewritten each run
    # to list only what is present right now. Two files because one cannot be both a
    # complete history and a checkable index of the present.
    # -Encoding ascii so the file stays byte-comparable with the serverq manifests rather
    # than acquiring a UTF-16 BOM that no `sha256sum -c` will read.
    win_ps "Add-Content -LiteralPath '$WIN_ROOT\\MANIFEST.sha256' -Encoding ascii -Value '$dst_sum  $relpath'" >/dev/null || \
        warn "pushed $relpath but could not append it to MANIFEST.sha256"
    printf '%s\n' "windows store verified: $relpath (${size}B) sha256=$dst_sum"
    return 0
}

# --- 2. the restore instructions travel with the dumps -------------------------------- #
# Deliberately BEFORE the dump: a run that later fails still refreshes the instructions, and
# the destination is never left holding dumps with no way to read them. ~5 KB, so the cost
# is not measurable against a 3.8 GB transfer. A failure here does not abort the run — the
# nightly dump matters more — but the exit status is flipped at the very end.
readme_failed=0
mkdir -p "$STAGE_DIR"
if [ -f "$RESTORE_README_SRC" ]; then
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

# --- 3. tonight's dump — REUSED, not taken again -------------------------------------- #
# ⛔ THIS SCRIPT DOES NOT RUN pg_dump, AND THAT IS DELIBERATE. backup_db.sh already takes one
# at 03:00 and backup_offsite.sh takes ANOTHER at 04:15. A third would mean three full dumps
# of the same database every night: MEASURED 2026-08-25, a dump is 3.83 GB and ~4 minutes of
# DB load, so a third costs ~4 min of contention and 3.83 GB of writes on a volume that had
# 57 GB free — to produce bytes that already exist a directory away.
#
# AND REUSE IS NOT MERELY CHEAPER, IT IS MORE USEFUL. Three independent pg_dumps of a live
# database produce three DIFFERENT files with three different checksums, so the manifests at
# the three tiers can never be compared to each other. Pushing the SAME artifact means one
# sha256 identifies tonight's backup everywhere it exists, and `sha256sum -c` at any tier
# answers a question about all of them.
#
# WHAT THIS DEPENDS ON, STATED PLAINLY: if the 03:00 run did not produce a dump, this script
# has nothing to send and FAILS — it does not quietly substitute a dump of its own. That is
# the intended behaviour. A missing local dump is a real failure of the 03:00 unit, and the
# four historical dates that went missing in July (§7, defect 3) went unnoticed for exactly
# as long as nothing downstream depended on them. Now something does.
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-$HOME/scraplan-backups}"
DUMP="$LOCAL_BACKUP_DIR/opensky-$(date +%F).sql.gz"

[ -f "$DUMP" ] || die "no local dump for today at $DUMP — the 03:00 scraplan-backup unit did not produce one; nothing to copy (this script never dumps on its own)"

dump_size=$(stat -c %s "$DUMP" 2>/dev/null || echo '')
is_uint "$dump_size" && [ "$dump_size" -ge "$MIN_BYTES" ] \
    || die "local dump is ${dump_size:-unmeasurable}B, below the ${MIN_BYTES}B floor — refusing to propagate it"

# Length alone does not prove a gzip member is intact — a 20-byte opensky-2026-07-15.sql.gz
# looked like a valid backup for two weeks because nothing tested the member itself. Tested
# again HERE even though backup_db.sh already tested it: this script is about to copy the
# file to a third machine and record its checksum as authoritative, and 14 hours of disk can
# happen between the two checks.
gzip -t "$DUMP" || die "gzip integrity check failed on $DUMP — refusing to propagate a corrupt dump"

echo "reusing tonight's local dump: $DUMP (${dump_size}B)"

push_verified "$DUMP" "db" || die "nightly dump did not reach $DEST intact"

# --- 4. retention — only now, with tonight's copy proven present ---------------------- #
# Dump names are opensky-YYYY-MM-DD.sql.gz, so a lexical sort is a chronological sort.
# Everything past the newest $WIN_KEEP is removed. This runs only because push_verified
# above returned success, so the set can never drop below one good copy.
#
# ⚠ `-Skip` operates on a SORTED, DESCENDING list so "skip the newest N" is exact. Sorting
# by Name rather than by LastWriteTime on purpose: a file's mtime at a Windows destination
# is its ARRIVAL time, which a catch-up run after an outage would make non-chronological.
# The date is in the name, and the name is what the dump is about.
win_ps "Get-ChildItem -LiteralPath '$WIN_ROOT\\db' -Filter 'opensky-*.sql.gz' -File | Sort-Object Name -Descending | Select-Object -Skip $WIN_KEEP | ForEach-Object { Remove-Item -LiteralPath \$_.FullName -Force; Write-Output ('windows retention: removed ' + \$_.Name) }" \
    || warn "retention pass did not complete at $DEST"

# --- 5. republish the checkable index + a success marker ------------------------------ #
# CURRENT.sha256 lists what is present at the destination RIGHT NOW, so it never names a
# file retention has just removed. Written in the `sha256sum -c` two-space format and in
# ascii, so it can be checked from this host after a download, or on any Linux box, without
# trusting the Windows side to verify itself.
#
# ⛔ THE HASHES ARE READ FROM MANIFEST.sha256, NOT RECOMPUTED. This is the difference
# between an index that costs seconds and one that becomes unusable as the tier does its job.
# The obvious implementation — Get-FileHash over every file in the store — was written first
# and is wrong at this tier's whole purpose: at the target depth of 90 dumps it would read
# **~345 GB from a spinning disk every single night**, roughly 40 minutes of pure re-hashing
# that grows with retention, to re-derive numbers this script computed and VERIFIED at the
# moment each file was promoted. backup_offsite.sh can afford the naive version because it
# keeps 7 dumps (~25 GB); at 90 it stops being affordable.
#
# ⚠ AND BE PRECISE ABOUT WHAT IS THEREFORE NOT CHECKED. Reading recorded hashes proves the
# file is still PRESENT; it does not re-prove its bytes are still intact on disk. Detecting
# silent corruption after the fact is a SCRUB, and a scrub is a different job on a different
# schedule — not something to smuggle into the nightly path by making it 40 minutes longer.
# Until one exists, `sha256sum -c CURRENT.sha256` run at the destination is the scrub, and it
# is on demand.
#
# A file present with no MANIFEST entry is omitted rather than guessed at — the index lists
# what has been verified and is still there, which is exactly the question it answers.
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
    || warn "could not republish CURRENT.sha256 at $DEST"

# Answers one question — did the last copy to the Windows store succeed? — from the
# destination itself, without trusting this host's journal. PRECISE MEANING: "the nightly
# dump arrived and verified".
win_ps "Set-Content -LiteralPath '$WIN_ROOT\\LAST_SUCCESS' -Encoding ascii -Value ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))" >/dev/null \
    || warn "could not write LAST_SUCCESS at $DEST"

printf '%s\n' "windows store backup complete -> $DEST:$WIN_ROOT (keeping $WIN_KEEP dumps)"

# Flip the exit status last, so a missing restore README is visible in
# `systemctl --user status` without having cost the nightly dump its retention and index.
if [ "$readme_failed" -ne 0 ]; then
    echo "backup_windows: FAILED: the nightly dump is safe at $DEST, but the restore README is not — dumps there are undocumented" >&2
    exit 1
fi
