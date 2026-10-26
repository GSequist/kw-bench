#!/bin/sh
# Start the memory sampler in the background, then run the container command.
python3 /opt/kwb/procmem.py sample /var/tmp/kwb-procmem.jsonl >/dev/null 2>&1 &
exec "$@"
