#!/usr/bin/env bash
set -euo pipefail

# Start PaddleOCR-VL 1.6 via llama-server (OpenAI-compatible API)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$SCRIPT_DIR/models"
LLAMA_DIR="$SCRIPT_DIR/llama.cpp"
LLAMA_SERVER="$LLAMA_DIR/build/bin/llama-server"

PORT="${PADDLEOCR_VL_PORT:-8203}"
HOST="${PADDLEOCR_VL_HOST:-127.0.0.1}"

MAIN_MODEL="$MODEL_DIR/PaddleOCR-VL-1.6-Q8_0.gguf"
MMPROJ="$MODEL_DIR/PaddleOCR-VL-1.6-mmproj-f16.gguf"

if [ ! -f "$LLAMA_SERVER" ]; then
    echo "ERROR: llama-server not found at $LLAMA_SERVER"
    echo "Run ./setup.sh first"
    exit 1
fi

if [ ! -f "$MAIN_MODEL" ] || [ ! -f "$MMPROJ" ]; then
    echo "ERROR: Model files not found in $MODEL_DIR"
    echo "Run ./setup.sh first"
    exit 1
fi

echo "Starting PaddleOCR-VL 1.6 (llama-server) on $HOST:$PORT ..."
exec "$LLAMA_SERVER" \
    -m "$MAIN_MODEL" \
    --mmproj "$MMPROJ" \
    --port "$PORT" \
    --host "$HOST" \
    --temp 0 \
    --ctx-size 4096 \
    --n-gpu-layers 99
