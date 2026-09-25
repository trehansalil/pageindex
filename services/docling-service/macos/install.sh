#!/usr/bin/env bash
# Run docling-service natively on an Apple Silicon Mac, reachable only over
# Tailscale. Native, not Docker: Docker on macOS runs in a VM that sees a
# fraction of the cores, and the GPU does not help (docling keeps TableFormer
# on the CPU under MPS). An M4 Pro converts table-dense pages at ~1.65 s/page
# with 3 processes, about 8x a cx33.
#
# Run from the unpacked bundle (pyproject.toml, uv.lock, src/, services/),
# with the bearer token in ./token. Re-running updates in place.
#   ./services/docling-service/macos/install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LABEL=com.pageindex.docling-service
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
cd "$ROOT"

for tool in brew uv tailscale; do
  command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
[ -s token ] || { echo "missing ./token (copy it from the server)" >&2; exit 1; }
chmod 600 token
command -v tesseract >/dev/null || brew install tesseract

# Same tessdata revision and checksums as the Dockerfile.
mkdir -p tessdata logs
rev=ced78752cc61322fb554c280d13360b35b8684e4
while read -r lang sum; do
  f="tessdata/$lang.traineddata"
  if ! echo "$sum  $f" | shasum -a 256 -c - >/dev/null 2>&1; then
    curl -fsSL -o "$f" "https://github.com/tesseract-ocr/tessdata/raw/$rev/$lang.traineddata"
    echo "$sum  $f" | shasum -a 256 -c -
  fi
done <<'SUMS'
deu 896b3b4956503ab9daa10285db330881b2d74b70d889b79262cc534b9ec699a4
eng daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047
ara 2005976778bbc14fc56a4ea8d43c6080847aeee72fcc2201488f240daca15c5b
osd e19f2ae860792fdf372cf48d8ce70ae5da3c4052962fe22e9de1f680c374bb0e
SUMS
rm -rf tessdata/configs
cp -R "$(brew --prefix)/share/tessdata/configs" tessdata/configs

uv sync --frozen --no-dev --python 3.12
uv pip install "fastapi>=0.115.0" "uvicorn[standard]>=0.30.0" "httpx>=0.27.0"
[ -d models ] || uv run python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('models'), progress=True, with_layout=True, with_tableformer=True, with_code_formula=False, with_picture_classifier=False, with_smolvlm=False, with_rapidocr=False, with_easyocr=False)"

# launchd starts this at login and restarts it when it exits (e.g. Tailscale
# was not up yet, so there was no address to bind). It listens on the
# Tailscale address only, never on the LAN or the internet, and caffeinate
# keeps the Mac from idle-sleeping mid-conversion. Port 8090, since the local
# compose stack's docling-service holds 8080.
cat > run.sh <<RUN
#!/usr/bin/env bash
set -euo pipefail
cd "$ROOT"
ip=\$(tailscale ip -4 2>/dev/null | head -1)
[ -n "\$ip" ] || { echo "tailscale not up" >&2; sleep 30; exit 1; }
export DOCLING_SERVICE_BEARER_TOKEN=\$(cat token)
export DOCLING_ARTIFACTS_PATH="$ROOT/models" TESSDATA_PREFIX="$ROOT/tessdata"
export DOCLING_DO_OCR=1 DOCLING_MAX_CONCURRENT=1 DOWNLOAD_TIMEOUT_S=120
export DOCLING_BLOCK_PRIVATE_URLS=1  # no NetworkPolicy fences a Mac's egress
export MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export HF_HUB_OFFLINE=1 BUILD_SHA=\$(cat BUILD_SHA 2>/dev/null || echo unknown)
exec caffeinate -i .venv/bin/uvicorn app:app --app-dir services/docling-service \\
  --host "\$ip" --port 8090 --workers 1
RUN
chmod +x run.sh

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$ROOT/run.sh</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$(brew --prefix)/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/service.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/service.log</string>
</dict></plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
url="http://$(tailscale ip -4 2>/dev/null | head -1):8090/health"
for _ in $(seq 60); do
  if curl -fsS "$url" 2>/dev/null; then
    echo; echo "docling-service up at $url; logs: $ROOT/logs/service.log"; exit 0
  fi
  sleep 3
done
echo "not healthy after 3 min; see $ROOT/logs/service.log" >&2; exit 1
