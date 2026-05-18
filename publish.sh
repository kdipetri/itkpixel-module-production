#!/usr/bin/env bash
set -euo pipefail

REMOTE_USER="kdipetri"
REMOTE_HOST="lxplus.cern.ch"
REMOTE_DIR="/eos/user/k/kdipetri/www/moduleProduction"
REMOTE="${REMOTE_USER}@${REMOTE_HOST}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shared socket — all operations reuse a single authenticated connection
SOCKET="/tmp/ssh-mux-${REMOTE_USER}-lxplus"

cleanup() {
    ssh -O exit -S "$SOCKET" "$REMOTE" 2>/dev/null || true
}
trap cleanup EXIT

echo "Connecting to ${REMOTE} ..."
ssh -fNM -S "$SOCKET" -o ControlPersist=120 -o ServerAliveInterval=15 "$REMOTE"

ssh -S "$SOCKET" "$REMOTE" "mkdir -p ${REMOTE_DIR}/plots ${REMOTE_DIR}/inventory"

rsync -az --progress -e "ssh -S $SOCKET" \
    "${SCRIPT_DIR}/index.html" \
    "${REMOTE}:${REMOTE_DIR}/"

rsync -az --progress --delete -e "ssh -S $SOCKET" \
    "${SCRIPT_DIR}/plots/" \
    "${REMOTE}:${REMOTE_DIR}/plots/"

rsync -az --progress -e "ssh -S $SOCKET" \
    "${SCRIPT_DIR}/plots/monthly_modules.png" \
    "${REMOTE}:${REMOTE_DIR}/plots/"

rsync -az --progress --delete -e "ssh -S $SOCKET" \
    "${SCRIPT_DIR}/inventory/" \
    "${REMOTE}:${REMOTE_DIR}/inventory/"

echo "Done. Site available at https://cern.ch/${REMOTE_USER}/moduleProduction"
