#!/usr/bin/env bash
set -euo pipefail

# PaddleOCR-VL 1.6 local setup via llama.cpp (GGUF + Metal on macOS)
#
# Downloads and builds llama.cpp with Metal acceleration, then fetches
# the PaddleOCR-VL-1.6 GGUF model files from Hugging Face.
#
# Requirements: cmake, git, a C++ compiler (Xcode CLI tools on macOS)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$SCRIPT_DIR/models"
LLAMA_DIR="$SCRIPT_DIR/llama.cpp"

echo "=== PaddleOCR-VL 1.6 Setup ==="

# 1. Build llama.cpp with Metal
if [ ! -d "$LLAMA_DIR" ]; then
    echo "--- Cloning llama.cpp ---"
    git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA_DIR"
fi

echo "--- Building llama.cpp with Metal ---"
cd "$LLAMA_DIR"
cmake -B build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
cmake --build build --config Release -j "$(sysctl -n hw.ncpu)" 2>&1 | tail -5
echo "Build complete: $(ls -la build/bin/llama-server 2>/dev/null || echo 'NOT FOUND')"
cd "$SCRIPT_DIR"

# 2. Download GGUF model files
mkdir -p "$MODEL_DIR"
HF_BASE="https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF/resolve/main"

MAIN_MODEL="$MODEL_DIR/PaddleOCR-VL-1.6-Q8_0.gguf"
MMPROJ="$MODEL_DIR/PaddleOCR-VL-1.6-mmproj-f16.gguf"

if [ ! -f "$MAIN_MODEL" ]; then
    echo "--- Downloading main model (Q8_0, ~1GB) ---"
    curl -L -o "$MAIN_MODEL" "$HF_BASE/PaddleOCR-VL-1.6-Q8_0.gguf" --progress-bar
else
    echo "Main model already downloaded"
fi

if [ ! -f "$MMPROJ" ]; then
    echo "--- Downloading mmproj (vision encoder, ~200MB) ---"
    curl -L -o "$MMPROJ" "$HF_BASE/PaddleOCR-VL-1.6-mmproj-f16.gguf" --progress-bar
else
    echo "mmproj already downloaded"
fi

echo ""
echo "=== Setup complete ==="
echo "Model dir: $MODEL_DIR"
echo "llama-server: $LLAMA_DIR/build/bin/llama-server"
echo ""
echo "Start with: ./start.sh"
