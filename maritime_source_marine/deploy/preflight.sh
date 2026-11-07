#!/usr/bin/env bash
# Preflight gate for the mtscraper service (runs as ExecStartPre).
#
# Refuses to start collection when the archive has nowhere safe to go. The host
# this was built for sat at 94% full, so an unguarded 24/7 archiver would take
# the whole machine down with it, not just itself.
#
# Exits non-zero to abort the start; systemd then retries per RestartSec, so the
# service self-heals once space is freed or the drive is remounted.
set -euo pipefail

: "${MT_OUTPUT_DIR:?MT_OUTPUT_DIR is not set — check the EnvironmentFile}"
MIN_FREE_GB="${MT_MIN_FREE_GB:-5}"

# An unmounted drive leaves its mountpoint behind as an ordinary empty directory,
# so "the parent exists" proves nothing — writes would land on the root disk
# silently and the archive would look healthy while filling the wrong filesystem.
# Only an explicit mount assertion catches that, hence MT_REQUIRE_MOUNT.
if [[ -n "${MT_REQUIRE_MOUNT:-}" ]]; then
    if ! mountpoint -q "$MT_REQUIRE_MOUNT"; then
        echo "preflight: $MT_REQUIRE_MOUNT is not a mounted filesystem" >&2
        echo "preflight: the target drive is unplugged or unmounted — refusing to" \
             "start, because writing here would silently fill the internal disk" >&2
        exit 1
    fi
    case "$MT_OUTPUT_DIR" in
        "$MT_REQUIRE_MOUNT"/*) ;;
        *)
            echo "preflight: misconfigured — MT_OUTPUT_DIR ($MT_OUTPUT_DIR) is not" \
                 "under MT_REQUIRE_MOUNT ($MT_REQUIRE_MOUNT)" >&2
            exit 1
            ;;
    esac
fi

parent="$(dirname "$MT_OUTPUT_DIR")"
if [[ ! -d "$parent" ]]; then
    echo "preflight: parent directory does not exist: $parent" >&2
    echo "preflight: is the target drive mounted?" >&2
    exit 1
fi
mkdir -p "$MT_OUTPUT_DIR"

if [[ ! -w "$MT_OUTPUT_DIR" ]]; then
    echo "preflight: output directory is not writable: $MT_OUTPUT_DIR" >&2
    exit 1
fi

avail_kb="$(df -Pk "$MT_OUTPUT_DIR" | awk 'NR==2 {print $4}')"
avail_gb=$(( avail_kb / 1024 / 1024 ))

if (( avail_gb < MIN_FREE_GB )); then
    echo "preflight: only ${avail_gb}G free on $(df -P "$MT_OUTPUT_DIR" | awk 'NR==2 {print $1}')," \
         "below the ${MIN_FREE_GB}G floor — refusing to start" >&2
    echo "preflight: free space, or point MT_OUTPUT_DIR at a larger disk" >&2
    exit 1
fi

echo "preflight: ok — ${avail_gb}G free, archiving to $MT_OUTPUT_DIR"
