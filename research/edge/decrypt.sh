#!/usr/bin/env bash
# decrypt.sh — read back research/edge/results/survivors.json.enc (see CRITERIA.md E4 and
# README "Edge research program"). The passphrase is never stored anywhere in this repo; only
# Cay's own copy of EDGE_PASSPHRASE (also stored as the GitHub Actions secret run_edge.py
# encrypts with) can read this file.
#
# Usage:
#   EDGE_PASSPHRASE=... ./decrypt.sh [path-to-survivors.json.enc] [output-path]
#
# Defaults: in  = research/edge/results/survivors.json.enc (relative to this script)
#           out = ./survivors.json (current directory)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IN="${1:-$HERE/results/survivors.json.enc}"
OUT="${2:-survivors.json}"

: "${EDGE_PASSPHRASE:?set EDGE_PASSPHRASE first, e.g. EDGE_PASSPHRASE=... ./decrypt.sh}"

if [ ! -f "$IN" ]; then
  echo "no encrypted file at $IN — nothing to decrypt (EDGE_PASSPHRASE may have been unset the" >&2
  echo "last time run_edge.py ran, or no run has happened yet)" >&2
  exit 1
fi

openssl enc -d -aes-256-cbc -pbkdf2 -salt -pass env:EDGE_PASSPHRASE -in "$IN" -out "$OUT"
echo "decrypted -> $OUT"
