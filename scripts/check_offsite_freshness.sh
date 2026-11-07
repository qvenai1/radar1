#!/bin/sh
# Off-host backup freshness check (BACKUP-HARDEN item 3).
#
# WHY THIS EXISTS. On 2026-08-03 the 04:15 off-host run died with
#   ssh: connect to host 10.10.20.57 port 22: No route to host
# The design held perfectly — nothing was promoted, no .part was left behind, the staging dump
# was removed by the EXIT trap, and the prior good copies were untouched. The unit went to
# `Result=exit-code` / `ActiveState=failed` exactly as intended.
#
# ⛔ AND NOBODY NOTICED FOR 44 HOURS. It was found only because an unrelated item (RESTORE-TEST)
# happened to read LAST_SUCCESS while looking for a dump to restore. A failure that is recorded
# correctly and never looked at is, operationally, a silent failure — the same shape that let
# the identity timer go 9 days (docs/PROJECT_STATE.md §6.4) and the reentry feed 10 days.
#
# THE PATTERN THIS FOLLOWS is §9's: the MEASUREMENT is the check, a unit listing is only a
# claim. `systemctl list-timers` showed this timer healthy and `active (waiting)` throughout —
# it fires nightly and its last firing FAILED, which looks identical to a timer with nothing to
# do. So this script never asks systemd anything. It asks the DESTINATION when it last actually
# received a verified dump, and compares that against the 26-hour threshold §9 records.
#
# ⛔ AND IT DOES NOT FAIL OPEN (the BACKUP-HARDEN item-1 lesson, applied here from the start).
# An unreadable or unparseable LAST_SUCCESS is a FAILED MEASUREMENT, reported as such and
# exiting non-zero. It is never treated as "probably fine": a guard that stops guarding when
# its measurement fails is worse than no guard, because it also reports that it checked.
#
# EXIT STATUS: 0 = fresh. 1 = STALE or unmeasurable. Both print an explicit one-line verdict.
set -eu

OFFSITE_USER="${OFFSITE_USER:-serverq}"
OFFSITE_HOST="${OFFSITE_HOST:-10.10.20.57}"
OFFSITE_ROOT="${OFFSITE_ROOT:-scraplan-offsite}"
OFFSITE_SSH_KEY="${OFFSITE_SSH_KEY:-$HOME/.ssh/serverq}"

# 26 hours, from docs/PROJECT_STATE.md §9: a daily 04:15 timer that has not produced a verified
# copy in over a day and two hours has missed at least one night, whatever the timer looks like.
OFFSITE_MAX_AGE_HOURS="${OFFSITE_MAX_AGE_HOURS:-26}"

# The restore instructions that BACKUP-HARDEN item 2 ships beside the dumps.
RESTORE_README_NAME="${RESTORE_README_NAME:-READ_THIS_BEFORE_RESTORING.md}"

DEST="$OFFSITE_USER@$OFFSITE_HOST"
SSH_OPTS="-o BatchMode=yes -o IdentitiesOnly=yes -i $OFFSITE_SSH_KEY \
-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
-o StrictHostKeyChecking=yes"

# The key is pinned for the same reason backup_offsite.sh pins it: plain ssh works from a shell
# via the GNOME-keyring agent and fails from a systemd timer (§6.8).
# shellcheck disable=SC2086
remote() { ssh $SSH_OPTS "$DEST" "$@"; }

is_uint() { case "${1:-}" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac; }

stale()   { echo "offsite-freshness: ⛔ STALE: $*" >&2; exit 1; }
unknown() { echo "offsite-freshness: ⛔ UNMEASURABLE: $*" >&2; exit 1; }

is_uint "$OFFSITE_MAX_AGE_HOURS" && [ "$OFFSITE_MAX_AGE_HOURS" -ge 1 ] \
    || unknown "OFFSITE_MAX_AGE_HOURS must be a positive integer (got '$OFFSITE_MAX_AGE_HOURS')"

# --- 1. read the destination's own evidence ------------------------------------------- #
# ⚠ Not this host's journal, and not systemd. The destination is the only party that knows
# whether it actually holds a verified copy.
if ! stamp=$(remote "cat '$OFFSITE_ROOT/LAST_SUCCESS' 2>/dev/null"); then
    unknown "cannot reach $DEST or read $OFFSITE_ROOT/LAST_SUCCESS — the off-host copy's age is UNKNOWN, which is not the same as fine"
fi
stamp=$(printf '%s' "$stamp" | tr -d ' \t\r\n')
[ -n "$stamp" ] || unknown "$OFFSITE_ROOT/LAST_SUCCESS at $DEST is empty"

# --- 2. turn it into an age, refusing to guess ---------------------------------------- #
if ! then_epoch=$(date -u -d "$stamp" +%s 2>/dev/null); then
    unknown "LAST_SUCCESS at $DEST is not a parseable UTC timestamp: '$stamp'"
fi
is_uint "$then_epoch" || unknown "could not convert LAST_SUCCESS ('$stamp') to an epoch"

now_epoch=$(date -u +%s)
age_s=$((now_epoch - then_epoch))
max_s=$((OFFSITE_MAX_AGE_HOURS * 3600))
age_h=$((age_s / 3600))
age_m=$(((age_s % 3600) / 60))

# ⚠ A LAST_SUCCESS in the future means the two clocks disagree; treat it as unmeasurable rather
# than silently reporting a healthy negative age.
[ "$age_s" -ge 0 ] || unknown "LAST_SUCCESS at $DEST is in the FUTURE ('$stamp') — clock skew; age cannot be trusted"

# --- 3. the secondary signals §9 asks for --------------------------------------------- #
# Collected before the verdict so a stale run still reports them; none of these can turn a
# stale verdict into a fresh one.
dumps=$(remote "ls -1 '$OFFSITE_ROOT/db/' 2>/dev/null | grep -c '^opensky-.*\.sql\.gz$'" || true)
is_uint "$dumps" || dumps="?"

parts=$(remote "find '$OFFSITE_ROOT' -name '*.part' 2>/dev/null | wc -l" || true)
is_uint "$parts" || parts="?"

if remote "test -f '$OFFSITE_ROOT/$RESTORE_README_NAME'" 2>/dev/null; then
    readme="present"
else
    readme="⛔ MISSING"
fi

newest=$(remote "ls -1 '$OFFSITE_ROOT/db/' 2>/dev/null | grep '^opensky-.*\.sql\.gz$' | sort | tail -1" || true)
[ -n "$newest" ] || newest="(none)"

echo "offsite-freshness: destination     : $DEST:$OFFSITE_ROOT"
echo "offsite-freshness: LAST_SUCCESS    : $stamp  (${age_h}h ${age_m}m ago)"
echo "offsite-freshness: threshold       : ${OFFSITE_MAX_AGE_HOURS}h (docs/PROJECT_STATE.md §9)"
echo "offsite-freshness: dumps held      : $dumps   newest: $newest"
echo "offsite-freshness: restore README  : $readme"
echo "offsite-freshness: stray .part     : $parts"

# ⚠ Reported but NOT fatal: a .part is harmless by design (never promoted, never indexed), but a
# recurring one means the link or the peer is dropping mid-copy (§9).
[ "$parts" = "0" ] || echo "offsite-freshness: ⚠ a .part file is the signature of an interrupted transfer — investigate if it recurs" >&2

# ⚠ Also not fatal here: an undocumented set of dumps is item 2's failure, and the backup run
# itself already flips its exit status for that. Surfaced so this one check answers §9 whole.
[ "$readme" = "present" ] || echo "offsite-freshness: ⚠ the dumps at the destination carry no restore instructions — the obvious restore command destroys data (docs/RESTORE_PROCEDURE.md)" >&2

# --- 4. the verdict ------------------------------------------------------------------- #
if [ "$age_s" -gt "$max_s" ]; then
    stale "the last VERIFIED off-host copy is ${age_h}h ${age_m}m old, over the ${OFFSITE_MAX_AGE_HOURS}h threshold — at least one night has been missed. Check: journalctl --user -u scraplan-offsite.service -n 50"
fi

echo "offsite-freshness: ✅ FRESH — verified off-host copy is ${age_h}h ${age_m}m old (threshold ${OFFSITE_MAX_AGE_HOURS}h)"
