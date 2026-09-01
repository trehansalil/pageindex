# QF3 Diagnosis Report: Doc 17 Garble False Positive on Bilingual Content

## Summary

The false positive on bilingual Arabic/English text fires via **two independent mechanisms**, both in `_is_garbled_blob` (Latin-gibberish sub-check) and `_has_sparse_mojibake`. The Latin-gibberish check is the **primary** culprit; `_has_sparse_mojibake` is a **secondary** contributor that fires only when Arabic and Latin characters are typographically adjacent (no space).

---

## Sub-Check Analysis (helpers.py `_is_garbled_blob`, line 616)

| # | Sub-check | Lines | Fires on bilingual Ar/En? | Reason |
|---|-----------|-------|---------------------------|--------|
| 1 | Empty | 623-624 | NO | Text exists |
| 2 | Null/replacement bytes | 625-626 | NO | Real text, no encoding corruption |
| 3 | GLYPH< markers | 627-628 | NO | Real text, no glyph markers |
| 4 | Control-char ratio >5% | 629-631 | NO | Real text has no control chars |
| 5 | PUA ratio >3% | 633-635 | NO | Standard Arabic Unicode, not PUA |
| 6 | Digit ratio >60% (>500 chars) | 637-640 | NO | SLA text is not numeric-heavy |
| 7 | Token repetition >30% (>20 tokens) | 645-649 | NO | Diverse SLA vocabulary |
| 8 | **Latin-gibberish in non-Latin context** | 650-662 | **YES -- PRIMARY CULPRIT** | See detailed analysis below |

### `_has_sparse_mojibake` (line 684)

| Sub-check | Fires? | Reason |
|-----------|--------|--------|
| `_MIXED_SCRIPT_RE` match ratio >2% | **POSSIBLY -- SECONDARY** | Fires only if Arabic-Latin characters are typographically adjacent (no whitespace separator); common in parenthetical references, acronyms glued to Arabic text |

---

## Primary Culprit: Latin-Gibberish Check (lines 650-662)

### Code Path
```python
if (
    expected_script
    and expected_script != "Latn"
    and os.environ.get("GARBLE_LATIN_GIBBERISH_ENABLED", "true").lower() != "false"
):
    latin_ratio_threshold = float(os.environ.get("GARBLE_LATIN_RATIO", "0.4"))
    nonsense_threshold = float(os.environ.get("GARBLE_NONSENSE_RATIO", "0.7"))
    ratio, latin_tokens = _latin_token_ratio(blob)
    if ratio > latin_ratio_threshold and len(latin_tokens) >= 5:
        nonsense = sum(1 for t in latin_tokens if t.lower() not in _COMMON_WORDS)
        if nonsense / len(latin_tokens) > nonsense_threshold:
            return True
```

### Why It Fires

1. **`expected_script` is "Arab"**: `_script_from_filename` (line 725) calls `detect_ocr_langs(filename)` which returns `["ara", ...]` for Arabic-named files, causing `expected_script = "Arab"`. Even if the filename does not signal Arabic, `_infer_script` on the flattened text would return "Arab" if Arabic characters constitute >50% of the alphabetic content.

2. **`expected_script != "Latn"` is True**: "Arab" != "Latn".

3. **`ratio > 0.4` fires**: `_latin_token_ratio` counts whitespace-split tokens and `[A-Za-z]{2,}` matches. In bilingual text with ~30-50% English content, the Latin token ratio easily exceeds 0.4.

4. **`len(latin_tokens) >= 5` is True**: Any SLA document has dozens of English words.

5. **`nonsense / len(latin_tokens) > 0.7` fires**: `_COMMON_WORDS` (line 570-604) contains ~160 entries: English/German stopwords plus a small set of technical terms. **SLA-domain vocabulary is NOT covered**: words like "service", "level", "agreement", "availability", "uptime", "penalty", "performance", "compliance", "response", "resolution", "incident", "escalation", "maintenance", "monitoring", "bandwidth", "latency", "capacity", "infrastructure", "provider", "customer", "contract", "warranty", "termination", "liability", "indemnification" are all absent from `_COMMON_WORDS`. In a typical SLA, >70% of Latin tokens would be domain-specific terms not in the common-words set, exceeding the 0.7 nonsense threshold.

### The Fundamental Design Flaw

The Latin-gibberish check assumes that Latin tokens in a non-Latin-script document are garbled unless they match common English/German stopwords. This assumption breaks for **any bilingual document** where the Latin portion carries domain-specific content (not just stopwords). The `_COMMON_WORDS` set would need to be impractically large to cover all legitimate English vocabulary, making the "nonsense ratio" approach structurally unsound for bilingual text.

---

## Secondary Culprit: `_has_sparse_mojibake` (line 684)

### Pattern
```python
_MIXED_SCRIPT_RE = re.compile(
    r"[U+0600-U+06FF][\x21-\x7E]{1,8}[U+0600-U+06FF]"   # Arabic-Latin-Arabic
    r"|[\x21-\x7E]{1,8}[U+0600-U+06FF][\x21-\x7E]{1,8}"  # Latin-Arabic-Latin
)
```

### When It Fires on Bilingual Text

This regex matches characters that are **directly adjacent without spaces**. In well-formatted bilingual text (Arabic sentence, space, English sentence), it would NOT match. However, it DOES match in these common bilingual patterns:

- **Parenthetical acronyms**: `الخدمة(SLA)مستوى` -- Arabic-Latin-Arabic glued
- **Inline technical terms**: Arabic text with `HTTP` or `API` immediately after Arabic chars
- **Punctuation-bridged**: `خدمة.Service` where a period (in `[\x21-\x7E]`) bridges the scripts
- **Mixed-direction rendering artifacts**: BiDi text may lose spaces at script boundaries during PDF extraction

The threshold is low (2% of whitespace-split tokens), so even a handful of these patterns in a 100+ token document can trigger it.

---

## Amplification via `_garble_check_nodes` (line 738)

Even if the whole-tree blob check (`_tree_is_garbled`) does not fire, the per-node check amplifies the false positive:

- When `expected_script="Arab"` is passed (from filename), line 755 forces `node_script = expected_script` for **every node**, overriding per-node script inference.
- Purely English nodes (e.g., an English clause in the SLA) are checked with `expected_script="Arab"`, causing 100% Latin token ratio and near-100% nonsense ratio.
- If >10% of nodes are flagged (default `GARBLE_NODE_RATIO_THRESHOLD=0.10`), `validate_tree` returns `("node_garbling")` even when the bulk check passes.

---

## Affected Functions

| Function | File | Line | Role |
|----------|------|------|------|
| `_is_garbled_blob` | helpers.py | 616 | Primary: Latin-gibberish sub-check (lines 650-662) |
| `_has_sparse_mojibake` | helpers.py | 684 | Secondary: mixed-script regex threshold |
| `_garble_check_nodes` | helpers.py | 738 | Amplifier: forces filename-derived script on all nodes |
| `_tree_is_garbled` | helpers.py | 765 | Aggregator: ORs both checks |
| `validate_tree` | helpers.py | 772 | Gate: returns "garbling" or "node_garbling" |
| `classify_verdict` | helpers.py | 863 | Verdict: "garbling" -> FAIL |
| `_script_from_filename` | helpers.py | 725 | Script source: filename -> "Arab" |
| `_infer_script` | helpers.py | 702 | Script source: text-inferred for per-node |

---

## Representative Test Strings

### Pure Arabic (should NOT be flagged)
```
هذه اتفاقية مستوى الخدمة بين الأطراف المتعاقدة لتحديد مستوى الخدمة المطلوب
```
Expected: `_is_garbled_blob(text, expected_script="Arab")` returns False (no Latin tokens).

### Pure English (should NOT be flagged)
```
This Service Level Agreement defines the performance metrics and availability targets for the infrastructure services provided under this contract.
```
Expected: `_is_garbled_blob(text, expected_script="Latn")` returns False (Latin-gibberish check skipped when expected_script="Latn").

### Bilingual Arabic+English -- Doc 17 case (currently FALSE POSITIVE)
```
هذه اتفاقية مستوى الخدمة Service Level Agreement تحدد معايير الأداء performance metrics ومستويات التوفر availability targets للبنية التحتية infrastructure services المقدمة بموجب هذا العقد contract
```
Expected with fix: should NOT be flagged.
Currently: `_is_garbled_blob(text, expected_script="Arab")` returns True because Latin tokens like "Service", "Level", "Agreement", "performance", "metrics", "availability", "targets", "infrastructure", "services", "contract" are NOT in `_COMMON_WORDS`, so nonsense ratio > 0.7.

### Actually garbled text (MUST still be flagged)
```
هذه اتفاقية مستوى الخدمة xKjQ7 mZpR3 vBnL8 تحدد معايير الأداء wQxR5 yTnM2 ومستويات التوفر kLpZ9 jHnW4 للبنية التحتية
```
Expected: `_is_garbled_blob(text, expected_script="Arab")` returns True because Latin tokens are genuinely nonsensical.

---

## Root Cause Summary

The Latin-gibberish sub-check in `_is_garbled_blob` (lines 650-662) uses a closed `_COMMON_WORDS` set (~160 entries) as a whitelist for "legitimate" Latin tokens in non-Latin documents. Any Latin word not in that set is counted as "nonsense." For bilingual documents with domain-specific English vocabulary, the nonsense ratio exceeds the 0.7 threshold, causing a false positive. The check cannot distinguish between genuine English content (e.g., "infrastructure", "availability", "compliance") and garbled Latin fragments (e.g., "xKjQ7", "mZpR3").

## Why QF3a/QF3b (original RFC-021 proposals) Were No-Ops

- **QF3a** proposed filtering markdown tokens (`---`, `###`) from `_is_garbled_blob`, but `_LATIN_TOKEN_RE = r"[A-Za-z]{2,}"` already excludes them (they contain no 2+ letter sequences), so the filter matches nothing.
- **QF3b** proposed a "common words" guard on `_has_sparse_mojibake`, but `_MIXED_SCRIPT_RE` matches runs of at most 8 non-space ASCII characters. `split()` on such short matches yields at most 1 token, so a "3+ common words" test can never fire.

## Correct Fix Direction

The fix must target the **Latin-gibberish sub-check** itself (lines 650-662). Options:

1. **Script-aware bilingual detection**: Before the nonsense check, call `_infer_script` on the blob. If it returns None (ambiguous/mixed), or if Latin ratio is between 0.15 and 0.85 (indicating genuine bilingualism rather than sparse garble), skip the Latin-gibberish check entirely.
2. **Consonant-cluster gibberish detector**: Instead of checking against `_COMMON_WORDS`, check whether Latin tokens have plausible English morphology (vowel presence, consonant-cluster length). "infrastructure" has vowels; "xKjQ7" does not.
3. **Per-node script override fix**: In `_garble_check_nodes`, when `expected_script` is set but `_infer_script(text)` returns a different script, use the inferred script instead of the filename-derived one. This prevents English nodes in a bilingual document from being force-checked as Arabic.
