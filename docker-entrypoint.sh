#!/bin/sh
set -eu

# Railway volumes are mounted after the image is built and may be root-owned.
# Prepare the persistent directory before dropping to the unprivileged process.
chown -R canary:canary /data
exec gosu canary "$@"
