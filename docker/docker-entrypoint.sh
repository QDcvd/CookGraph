#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -d /workspace/frontend && ! -e /workspace/frontend/node_modules ]]; then
  ln -s /opt/minicook/frontend/node_modules /workspace/frontend/node_modules 2>/dev/null || true
fi

if [[ -d /workspace ]]; then
  mkdir -p /workspace/models
  if [[ ! -e /workspace/models/gte-large-zh && -d /opt/minicook/models/gte-large-zh ]]; then
    ln -s /opt/minicook/models/gte-large-zh /workspace/models/gte-large-zh 2>/dev/null || true
  fi
fi

exec "$@"
