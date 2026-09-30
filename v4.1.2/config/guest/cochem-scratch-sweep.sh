#!/usr/bin/env bash
# CoChem Scratch Sweeper Cron Routine (SRS-412-02 Section 8 Failure Modes: Scratch Leak)
# Inspects /tmp mounts every 60 seconds and unlinks unmounted orphan namespaces.
set -euo pipefail

SCRATCH_BASE="/tmp"
RUNNING_IDS=$(docker ps -q --no-trunc 2>/dev/null || true)

for dir in "$SCRATCH_BASE"/cochem_scratch_*; do
    [ -d "$dir" ] || continue
    # Check if mount is listed in current mountinfo
    if grep -qs "$dir" /proc/self/mountinfo; then
        continue
    fi
    # Check if directory corresponds to an active container
    is_active=0
    for cid in $RUNNING_IDS; do
        if [[ "$dir" == *"$cid"* ]]; then
            is_active=1
            break
        fi
    done
    if [ "$is_active" -eq 0 ]; then
        echo "[ORPHAN_PURGE] Removing unmounted orphan scratch directory: $dir"
        rm -rf "$dir"
    fi
done
