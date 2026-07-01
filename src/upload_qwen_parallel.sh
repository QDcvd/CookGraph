#!/usr/bin/env bash
set -euo pipefail

SRC="${1:-/mnt/c/Users/jinhenghao/Downloads/Qwen3.6-27B-Q4_K_M.gguf}"
REMOTE="${REMOTE:-ubuntu@100.101.72.21}"
REMOTE_DIR="${REMOTE_DIR:-/home/ubuntu/.lmstudio/models/lmstudio-community/Qwen3.6-27B-GGUF}"
MODEL_NAME="${MODEL_NAME:-Qwen3.6-27B-Q4_K_M.gguf}"
CHUNK_SIZE="${CHUNK_SIZE:-512M}"
PARALLEL="${PARALLEL:-6}"
CHUNK_DIR="${CHUNK_DIR:-$HOME/Downloads/qwen_chunks}"

if [ ! -f "$SRC" ]; then
  echo "Source file not found: $SRC" >&2
  exit 1
fi

mkdir -p "$CHUNK_DIR"

echo "[1/6] Creating chunks in $CHUNK_DIR"
if compgen -G "$CHUNK_DIR/${MODEL_NAME}.part.*" > /dev/null; then
  echo "Chunks already exist, skipping split."
else
  split -b "$CHUNK_SIZE" "$SRC" "$CHUNK_DIR/${MODEL_NAME}.part."
fi

echo "[2/6] Creating checksum"
sha256sum "$SRC" | awk -v name="$MODEL_NAME" '{print $1 "  " name}' > "$CHUNK_DIR/SHA256SUMS"

echo "[3/6] Preparing remote directory"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR/chunks'"

echo "[4/6] Uploading chunks with rsync resume, parallel=$PARALLEL"
export REMOTE REMOTE_DIR
find "$CHUNK_DIR" -maxdepth 1 -type f -name "${MODEL_NAME}.part.*" -print0 \
  | sort -z \
  | xargs -0 -n 1 -P "$PARALLEL" bash -c '
      set -euo pipefail
      f="$1"
      echo "Uploading/resuming: $(basename "$f")"
      rsync -avP --append-verify "$f" "$REMOTE:$REMOTE_DIR/chunks/"
    ' _

echo "[5/6] Uploading checksum"
rsync -avP "$CHUNK_DIR/SHA256SUMS" "$REMOTE:$REMOTE_DIR/chunks/SHA256SUMS"

echo "[6/6] Merging and verifying on remote"
ssh "$REMOTE" "
  set -euo pipefail
  cd '$REMOTE_DIR'
  cat chunks/${MODEL_NAME}.part.* > '$MODEL_NAME'
  sha256sum -c chunks/SHA256SUMS
"

echo "Done: $REMOTE:$REMOTE_DIR/$MODEL_NAME"
