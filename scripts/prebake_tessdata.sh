#!/usr/bin/env bash
# Pre-bake Tesseract traineddata for the OCR escalation path (Fix-3 / Fix-5).
#
# WHY: egress-limited workers cannot fetch tessdata at runtime
# (TESSDATA_ALLOW_DOWNLOAD=0). ensure_tessdata() DROPS any language whose
# <lang>.traineddata is absent under TESSDATA_PREFIX, so a missing 'ara' silently
# degrades Arabic OCR to deu,eng -> Latin mojibake on Arabic script (the مرسوم
# 13/2022 failure). This script bakes the needed languages into a local dir
# (default .tessdata/, gitignored) so the container image / dev box ships them.
#
# Arabic uses tessdata_best (markedly better on legal Arabic) with a fallback to
# the standard repo; eng/deu/osd come from the standard fast repo.
#
# Usage:
#   scripts/prebake_tessdata.sh                 # bake ara,eng,deu,osd into .tessdata/
#   TESSDATA_DEST=/srv/tessdata scripts/prebake_tessdata.sh
#   TESSDATA_LANGS="ara eng" scripts/prebake_tessdata.sh
#
# Then point the app at it:   export TESSDATA_PREFIX="$PWD/.tessdata"
set -euo pipefail

DEST="${TESSDATA_DEST:-$PWD/.tessdata}"
LANGS="${TESSDATA_LANGS:-ara eng deu osd}"
BEST_BASE="https://github.com/tesseract-ocr/tessdata_best/raw/main"
MAIN_BASE="https://github.com/tesseract-ocr/tessdata/raw/main"

mkdir -p "$DEST"
echo "Pre-baking tessdata -> $DEST"
echo "Languages: $LANGS"

# A tessdata dir is NOT just <lang>.traineddata: Tesseract also resolves output
# format configs (configs/tsv, configs/txt, ...) under TESSDATA_PREFIX. If those
# are absent, `tesseract ... tsv` silently degrades to plain text (no header row),
# which makes Docling's TSV parser raise KeyError('text') and the OCR stage fail.
# So a pre-baked dir MUST also carry configs/, tessconfigs/ and pdf.ttf. Copy them
# from the system tesseract install (the files are tiny, static, AGPL-free data).
SYS_TESSDATA="$(tesseract --list-langs 2>&1 | sed -n 's/.*in "\(.*\)".*/\1/p' | head -1)"
if [ -n "$SYS_TESSDATA" ] && [ -d "$SYS_TESSDATA" ]; then
  for support in configs tessconfigs pdf.ttf; do
    src="$SYS_TESSDATA/$support"
    if [ -e "$src" ] && [ ! -e "$DEST/$support" ]; then
      cp -R "$src" "$DEST/$support"
      echo "  [cp  ] $support <- $SYS_TESSDATA"
    fi
  done
else
  echo "  [warn] could not locate system tessdata configs; '$DEST' may lack configs/" >&2
  echo "         (set TESSDATA_PREFIX only on a dir that has configs/tsv, or tsv OCR will fail)" >&2
fi

fetch() {
  # fetch <url> <dest-file>  ; returns non-zero on failure (curl --fail)
  curl --fail --location --silent --show-error "$1" -o "$2"
}

for lang in $LANGS; do
  out="$DEST/$lang.traineddata"
  if [ -s "$out" ]; then
    echo "  [skip] $lang (already present, $(du -h "$out" | cut -f1))"
    continue
  fi
  # Arabic: prefer tessdata_best for accuracy; everything else (and the ara
  # fallback) comes from the standard repo.
  if [ "$lang" = "ara" ]; then
    echo "  [get ] ara (tessdata_best)..."
    if fetch "$BEST_BASE/$lang.traineddata" "$out"; then
      echo "  [ ok ] ara <- tessdata_best ($(du -h "$out" | cut -f1))"
      continue
    fi
    echo "  [warn] tessdata_best ara failed; falling back to standard repo"
  fi
  echo "  [get ] $lang (standard)..."
  if fetch "$MAIN_BASE/$lang.traineddata" "$out"; then
    echo "  [ ok ] $lang ($(du -h "$out" | cut -f1))"
  else
    rm -f "$out"
    echo "  [FAIL] could not fetch $lang.traineddata" >&2
    exit 1
  fi
done

echo
echo "Done. Verify with:"
echo "  TESSDATA_PREFIX=\"$DEST\" tesseract --list-langs"
echo
echo "Wire it in (.env):   TESSDATA_PREFIX=$DEST"
