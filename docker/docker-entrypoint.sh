#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -d /workspace/frontend && ! -e /workspace/frontend/node_modules ]]; then
  ln -s /opt/minicook/frontend/node_modules /workspace/frontend/node_modules 2>/dev/null || true
fi

exec "$@"
