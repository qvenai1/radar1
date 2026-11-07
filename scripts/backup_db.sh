#!/bin/sh
# Nightly TimescaleDB dump (runs via scraplan-backup.timer).
# Keeps 4 days (see the retention note below). Failure exits non-zero so systemd flags the unit.
#
# WHY pipefail. `pg_dump | gzip` hides pg_dump's exit status: the shell reports
# only gzip's, and gzip happily succeeds on a truncated or empty stream. That is
# how opensky-2026-07-15.sql.gz came to exist as a 20-byte file that looked like
# a valid backup for two weeks. `set -o pipefail` makes the pipeline fail when
# pg_dump fails.
#
# WHY A MINIMUM SIZE. pipefail catches a non-zero pg_dump, but not a dump that
# "succeeds" while producing almost nothing. A real dump of this database is
# gigabytes; anything under MIN_BYTES is not a backup, whatever its exit code.
#
# FLAG, NEVER DELETE. A failed attempt is renamed to *.failed rather than removed
# or left under the real name. Deleting it would erase the evidence that a night
# was missed; leaving it named opensky-<date>.sql.gz would make a broken file
# look restorable. The same rule the data layers use: record it, mark it, never
# silently drop it.
set -eu
# POSIX sh has no pipefail; dash/sh on this host is dash, so ask for it only
# when the shell supports it (bash/ksh/zsh do, and systemd runs this via /bin/sh).
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail || \
    echo "backup_db: WARNING: shell lacks pipefail; relying on the size gate" >&2

BACKUP_DIR="$HOME/scraplan-backups"
# A genuine dump is gigabytes; 100 MB is far below any real one and far above a
# truncated or empty stream.
MIN_BYTES="${BACKUP_MIN_BYTES:-104857600}"

mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/opensky-$(date +%F).sql.gz"
STAMP=$(date +%H%M%S)

fail() {
    reason="$1"
    dst="$OUT.$STAMP.failed"
    [ -e "$OUT.tmp" ] && mv "$OUT.tmp" "$dst" || :
    echo "backup_db: FAILED ($reason) -> $dst" >&2
    exit 1
}

docker exec scraplan-timescaledb pg_dump -U opensky opensky | gzip > "$OUT.tmp" \
    || fail "pg_dump/gzip pipeline returned non-zero"

size=$(stat -c %s "$OUT.tmp" 2>/dev/null || echo 0)
if [ "$size" -lt "$MIN_BYTES" ]; then
    fail "output only ${size}B, below the ${MIN_BYTES}B floor"
fi

# gzip integrity, not just size — a valid-length but corrupt member is still junk.
gzip -t "$OUT.tmp" || fail "gzip integrity check failed"

mv "$OUT.tmp" "$OUT"
# RETENTION — 4 days, cut from 14 on 2026-08-25 (WINSTORE). This tier's job is the FAST
# restore, not depth: it lives on the same volume as the database, so it cannot survive the
# event it exists for, and every extra day of it is a day of disk pressure bought for a
# capability another tier already provides better.
#
# MEASURED that day: per-dump size had grown to 3.83 GB, so 14 days had become 38 GB — on a
# volume with 57 GB free. The directory was the second-largest consumer on the machine and
# LARGER THAN THE DATABASE IT BACKS UP.
#
# ⛔ AND THE DEPTH WAS NOT LOST, IT MOVED SOMEWHERE BETTER. On the same day the Windows store
# (scripts/backup_windows.sh) began keeping 90 nightly dumps on a 6,964 GB volume on a THIRD
# machine, where 90 days costs 5 % of the disk. So this change makes recoverable history
# LONGER — 14 days on one dying volume became 4 local + 7 on serverq + 90 on the Windows
# store. ⚠ Do not raise this number back without checking those two tiers are still running:
# `systemctl --user list-timers 'scraplan-offsite*' 'scraplan-windows*'`.
#
# -maxdepth 1 is new and deliberate: this find carried none, so it descended into any
# subdirectory of $BACKUP_DIR and would delete matching files there too. backup_offsite.sh
# documents having to stage its in-flight dump OUTSIDE this directory precisely to stay out
# of this delete scope. Bounding the find removes the hazard at the source instead.
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-4}"
find "$BACKUP_DIR" -maxdepth 1 -name 'opensky-*.sql.gz' -mtime "+$RETAIN_DAYS" -delete
echo "backup written: $OUT ($(du -h "$OUT" | cut -f1))"
