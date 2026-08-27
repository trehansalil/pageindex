---
zone_name: Garble Detection Cross-Cutting Kernel
severity: critical
bug_count: 5
status: improved
audit_date: 2026-08-27
audit_run: POST-RUN20
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/converters/ocr_langs.py
tags:
  - zone-spec
  - critical
  - garble
  - detection
scorecard_verdict: needs_another_cycle
scorecard_date: 2026-08-27
scorecard_run: POST-RUN20
wave: 1
---
## Mechanism

detect_garble is the single most cross-cutting decision primitive in the codebase (11 direct callers, hop-2 fan-out into apply_promotions, _execute_ocr_retry, _attempt_tesseract_raster_recovery, tree_validation). It functions as a shared kernel feeding three independently-evolving subsystems: tree-quality gating, OCR-skip decisions for picture regions, and OCR-retry keep-best arbitration. Its heuristic prongs (garble_prongs) have complementary structural blind spots: CMap-corrupted German text passes latin_gibberish when expected_script='Latn'; digit-ratio is diluted below 60% by markdown formatting symbols; token_repetition fires false-positive on legitimate tables with pipe/currency symbols. The tessdata language-fallback path silently substitutes Latin OCR for Arabic, producing mojibake that passes every prong. Any change to detect_garble's threshold logic has wide, only-partially-visible blast radius across all three consumer subsystems.

The generative mechanism operates through fan-out from a single decision surface with prong-level blind spots:
- a. When tessdata silently substitutes ['deu','eng'] for missing Arabic traineddata, the resulting Latin mojibake passes all prongs — not PUA, not glued mixed-script, not digit-heavy, rarely hits 30% token repetition (chain 5).
- b. The duplicate _tree_is_garbled/_flat_text_is_garbled implementations repeat the 500-char digit-ratio floor independently, so a fix in one does not propagate to the other (chain 8).
- c. Token_repetition fired false-positive on tables with pipe/currency symbols; the fix (exclude non-alphanumeric tokens) did NOT address numeric-junk or Latin-script mojibake that still pass undetected (chain 15).
- d. validate_tree's GATE_TABLE evaluates garbling first (severity=0), but the signal computation means a minimal-tree garbled document gets reason='node_count_low' (severity=1) instead of 'garbling', and OCR escalation only fires for reason in ('garbling','node_garbling'), so recovery never triggers (chain 18).
- e. The bidi coherence check's presentation-form signal is destroyed by NFKC normalization before it runs, making it a zero-sensitivity null detector (chain 4).

## Code Evidence

`detect_garble` at garble.py:494-572 delegates to garble_prongs after short-circuit checks. `garble_prongs` at garble.py:318-405 implements 9 prongs: digit_ratio gated by `if len(norm) > cfg.garble_digit_floor` (line 380), token_repetition at `if (most_common_count / len(tokens)) > 0.30` (line 386), latin_gibberish gated by garble_latin_gibberish_enabled. `ensure_tessdata` at ocr_langs.py:92-196 now raises TessdataUnavailableError for non-Latin missing traineddata (Zone-3 fix), but Latin languages still silently dropped. `validate_tree` at tree_validation.py:262-354 iterates GATE_TABLE; GATE_TABLE at gates.py:321-408 places GARBLING at severity=0 and NODE_COUNT_LOW at severity=1.

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/helpers/garble.py | Garble detection prongs & decision logic |
| src/pageindex_mcp/helpers/tree_validation.py | Tree quality gate evaluation |
| src/pageindex_mcp/helpers/gates.py | Gate severity & ordering |
| src/pageindex_mcp/converters/ocr_langs.py | Tessdata fallback handling |
