#!/bin/bash
# Reference solution: derives every finding from the extract itself (no access to ground truth).
set -euo pipefail
python3 /solution/oracle.py /app/sap /app/out
