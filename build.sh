#!/usr/bin/env bash
set -euo pipefail
python -m pip install -r requirements.txt
# Schema changes are an explicit release operation, never a build side effect.
