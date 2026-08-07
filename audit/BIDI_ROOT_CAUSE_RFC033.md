# BiDi Root Cause & Remediation Plan — RFC-033 D2 (Parts A + B)

**Date:** 2026-08-07
**Scope:** Explain and remediate the two failures observed in the RFC-033 Task 9.1 scoped re-ingest.
**Status:** Plan only. No `src/` change, no tasks-file change, no re-ingest, no MinIO write was made producing this document.
**Inputs:** Task 9.1 re-ingest results; three adversarial refutation runs (H1 surviving; H2, H3, H4 refuted); four cached stored trees; one live read-only capture of the remote Docling service output; two direct measurements performed while writing this plan (§0.1).

---

## 0. Executive summary

| | Failure | Verdict | Confidence |
|---|---|---|---|
| **F1** | D2 Part A heading guard did not prevent reversal for سياسة حوكمة on a fresh ingest | **RESOLVED** — deployment/topology defect, not a defect in the guard | High (live remote capture + local reproduction) |
| **F2** | `_check_bidi_coherence` returns `bidi_ok=True` on visibly-reversed trees (0% detection) | **RESOLVED** — detector-design defect with two independent, each-sufficient causes | High (direct instrumentation of the shipped functions) |

**F1 in one sentence:** the heading reversal is produced by *our own* `reconstruct_bidi_order()` running inside a **stale copy of our code deployed to the remote Scaleway Docling service**, and the D2 Part A guard can never reach it because (a) the guard is **uncommitted working-tree-only code** — `git log -S"_heading_is_logical_order"` finds it in **no commit** — and (b) the worker **never re-normalizes** markdown returned over the remote route.

**F2 in one sentence:** `_check_bidi_coherence`'s only failure signal is Arabic **presentation-form** morphology (`FINAL FORM` / `INITIAL FORM` in `unicodedata.name`), but `get_display()`-reversed text is composed entirely of **canonical U+06xx** letters — so the detector is a *null detector* on this entire input class; and independently, its line selector at `helpers.py:1029` counts only U+0600–U+06FF, which means lines that *do* carry presentation forms are discarded before the signal is ever consulted, leaving the gate blind to its own design-target population as well.

**Headline recommendation (Task 9.2):** **Keep `BIDI_COHERENCE_ENFORCE` defaulted to `"true"`, but do not treat the Task 9.1 measurement as evidence for it.** The measurement is not a low false-positive rate — it is a **zero true-positive rate**. The default is safe only because the detector currently cannot fire; it is worthless until §3.2 lands. Full justification and required wording in §5.

---

### 0.1 Two measurements I performed while writing this plan

Both are pure local computation on already-captured bytes — no ingest, no LLM, no network, no `src/` change.

**M-A — the proposed F1 fix demonstrably repairs the actual failing document.**
Input: `h1_remote_fresh.md` (31,314 bytes), the live read-only capture of what `DOCLING_SERVICE_URL/convert/pdf` returned for `سياسة حوكمة و إدارة البيانات - Copy.pdf`.

```
headings: 23
first5 RAW REMOTE                      : ['ةقيثولا فيرعت لودج', 'دامتعإلا لودج', 'تا رادصإلا لودج',
                                          'ميهافملاو تاحلطصملا', 'تايوتحملا سرهف']
first5 AFTER reconstruct_bidi_order    : ['جدول تعريف الوثيقة', 'جدول الإعتماد', 'جدول الإصدار ات',
                                          'المصطلحات والمفاهيم', 'فهرس المحتويات']
reconstruct_bidi_order idempotent      : True
_pre_inference_normalize idempotent    : True
first5 AFTER _pre_inference_normalize  : (identical to reconstruct_bidi_order output)
```

All 23 headings are repaired, and both functions are idempotent on this input. This is the empirical basis for fix **F1-D** (§3.1).

**M-B — a title-level statistic that separates the corpus perfectly, using code that already exists.**
Applying the *existing* working-tree predicate `_heading_is_logical_order` (converters.py:1424, RFC-033 D2 Part A) to every node title of the four cached trees:

| doc_id | titles | Arabic titles | not-logical | rate |
|---|---|---|---|---|
| 48839446 (known corrupt) | 27 | 26 | **24** | **0.923** |
| 29109613 (known corrupt) | 24 | 23 | **22** | **0.957** |
| 32126145 (known clean) | 24 | 23 | 0 | **0.000** |
| cc4533aa (known clean) | 262 | 261 | 0 | **0.000** |

A ≥0.92 separation with zero overlap, from a predicate we already wrote and already ship in the working tree. This is the empirical basis for fix **F2-C** (§3.2). Sampling frame: **n=4 trees** (2 corrupt / 2 clean) — see §5 and §6.

---

## 1. Root cause of F1 — RESOLVED

### 1.1 What actually happens

```
worker (host, working tree)                    Scaleway Docling service (stale image)
──────────────────────────────                 ─────────────────────────────────────
client.index()
  chain = pdf_markdown_converters()            services/docling-service/app.py:148
  _use_remote = docling_service_url            └── from pageindex_mcp.converters
              and self._staging_key                    import pdf_to_markdown_docling   ← OUR CODE
  client.py:823  ─── POST /convert/pdf ───────►      _pre_inference_normalize()          converters.py:2923
                                                       └── reconstruct_bidi_order()      converters.py:1450
                                                             heading branch, NO GUARD  ← reverses everything
  client.py:832  ◄── markdown (headings ALREADY REVERSED, body logical) ───
  client.py:919-940  md_content ──► temp .md ──► _run_md_to_tree()
       ▲
       └── NO _pre_inference_normalize HERE. NO reconstruct_bidi_order HERE.
           The working-tree guard is never given the bytes.
```

Three independently verified facts pin this down:

1. **The remote service runs our converter, not vanilla Docling.** `services/docling-service/app.py:148` does `from pageindex_mcp.converters import pdf_to_markdown_docling`. So `_pre_inference_normalize` → `reconstruct_bidi_order` executes **on the far side of the network**, inside whatever code version that image was built from.

2. **The deployed image predates the guard, and the guard exists in no commit at all.**
   - `git show HEAD:src/pageindex_mcp/converters.py | grep -c _heading_is_logical_order` → **0**
   - `git log --oneline -S"_heading_is_logical_order" -- src/pageindex_mcp/converters.py` → **empty**
   - `git diff --stat` shows converters.py +188 lines uncommitted.

   HEAD's heading branch is unconditional: `out.append(m.group(1) + get_display(m.group(2)))`. No build of the remote image can contain the guard, even in principle.

   Independent dating of the deployed build: the live remote markdown carries **20 GFM-padded `|----|` separators and 0 minimal `| --- |` separators**. `_repair_docling_tables` — the only producer of the minimal form (converters.py:2459/2486) — landed **2026-08-04** in `08b6eea`; `reconstruct_bidi_order` landed 2026-07-17 (`f513b92`); the microservice landed 2026-07-30 (`dcf89d1`). The deployed image is therefore a build from the **2026-07-30 .. 2026-08-04** window: unconditional heading flip, no table repair.

3. **The signature in the live remote output is exactly heading-only reversal.** Two independent read-only probes POSTed the already-staged PDF and both got markdown whose 23 ATX headings are reversed while `_text_is_logical_order(body)` is `True` — the precise fingerprint of the heading branch firing with `reorder_body=False`. No general Docling bidi bug produces that. Stored tree titles for 48839446 match the raw remote headings **23/23** and the locally-normalized text **1/23**.

### 1.2 Why حقوق الإنسان (cc4533aa) came out correct — and why the obvious story is wrong

The tempting explanation (H2: "its Docling output was visual-order, so the blind flip repaired it") was **tested and refuted**:

- Locally-run Docling on the same PDF produces **logical-order** markdown (`_text_is_logical_order=True` post-NFKC, `sampled=8 orig=38 disp=1`), headings reading `المحتويات`, `ملاحظة` — plainly correct.
- Simulating the stale unconditional flip on that markdown yields `تايوتحملا`, which appears **nowhere** in the 262-node stored tree.
- Arabic word-bigram overlap: pristine ∩ stored = **70**; `get_display(pristine)` ∩ stored = **0**. Perfect asymmetry against H2.
- Positive control: the same stale simulation on locally-produced سياسة حوكمة markdown reproduces the stored 48839446 titles **byte-for-byte, 8/8 in order** — so the method is sensitive and "local Docling ≠ remote Docling" is closed for the failing document.
- The chunked route (`page_count 161 > MAX_DOCLING_PAGES=150`, config.py:19) was eliminated: `_pdf_to_markdown_docling_chunked` recurses into `pdf_to_markdown_docling` per chunk, so D7 runs on every chunk.

**What that leaves genuinely open:** *why* cc4533aa escaped the flip on the deployed route. Both probes got **HTTP 504** from the remote service on that 403k-char / 161-page PDF, so nobody observed its remote output. The live possibilities — a silent fallback to a different converter after the remote timed out, a different code version serving that request, or a local-route execution — are **not distinguished by any evidence I have**. See §6, item U-1. This gap does not weaken F1's root cause; it is a second, unexplained *escape*, not an unexplained *failure*.

### 1.3 Two compounding defects surfaced by the same trace

- **C-1 — no extraction provenance is persisted.** `processed/<doc_id>.meta.json` records no extraction route, no converter name, no converter/image version, no page count, no pdf-inspector classification (storage.py:423 `_META_FIELDS`; client.py:1885-1897 meta dict). No raw markdown is persisted anywhere in MinIO. This entire diagnosis had to be reconstructed from table-separator fingerprints and live re-probing of a production service. That is not a sustainable diagnostic posture.
- **C-2 — live AGPL exposure path (Hard Rule 4).** `pdf_markdown_converters()` (converters.py:2998) *always* seeds the chain with `("pymupdf4llm", _pdf_to_markdown_no_pics)`. When the remote Docling call raises — and it demonstrably 504s on large Arabic PDFs — `client.py:886-917` walks to the next chain entry, which is **pymupdf4llm (AGPL-3.0)**. I could neither confirm nor exclude that this fired for the cc4533aa run, because the subprocess converter's logs do not reach `.run/*.log`. Under Hard Rule 4 this must be closed regardless of whether it fired.

---

## 2. Root cause of F2 — RESOLVED (two independent, each-sufficient causes)

The detector's 0% rate is **not** a sampling artifact and **not** a dilution effect. Both were tested to destruction.

### 2.1 Cause 1 — the only failure signal cannot exist in the failure mode (null detector)

`_check_bidi_coherence` (helpers.py:991) fails a run only when `_reversed_morphology` fires, and that helper (helpers.py:1005-1020) tests for the literal substrings `"FINAL FORM"` / `"INITIAL FORM"` in `unicodedata.name()`. Those names occur only for Arabic **Presentation Forms** (U+FB50–U+FEFF).

But `get_display()` consumes canonical U+06xx letters and emits canonical U+06xx letters. Measured:

- `_check_bidi_coherence('ةقيثولا فيرعت لودج')` → `(True, '')` — on the bare heading, on 5 repeated lines, and on a 3-reversed/2-clean mix.
- **Control that settles it:** the *entire tree* with **every single line string-reversed** → `(True, '')`. A detector with any nonzero capability must fail that.
- Census: **0** presentation-form codepoints in 21,043 chars of 48839446, and 0 in the titles of all four docs. `unicodedata.name` on every char of the reversed heading is plain `ARABIC LETTER *` (`ة` = U+0629 TEH MARBUTA, …).
- `n_samples ∈ {5, 50, 500, 100000}` → `(True, '')` in every configuration. The 0% rate would be 0% at `n_samples=∞`.

The docstring's stated premise — *"the character sequence was reversed before NFKC normalisation locked in the wrong presentation form"* — **does not hold for a single document in this corpus.** NFKC maps presentation forms *to* canonical with **0 survivors**, so upstream normalization can only deepen, never relieve, the blindness.

### 2.2 Cause 2 — the run selector and the failure signal are mutually exclusive by encoding range

Independently of Cause 1, the line selector at **helpers.py:1029** counts Arabic as `"؀" <= c <= "ۿ"` — **U+0600–U+06FF only** — and skips any line below a 0.4 ratio. **Zero** presentation-form codepoints fall in that range (verified: 0 of U+FB50–U+FEFF).

Consequence, measured directly:

```
--- 'canonical reversed x5'   (carries no signal, IS sampled)
    arabic_chars=16 total=16 ratio=1.000 -> selected_as_run=True
    runs=5 failed=0                       -> (True, '')
--- 'presentation-form x5'    (carries the ONLY signal, is NOT sampled)
    arabic_chars=0  total=16 ratio=0.000 -> selected_as_run=False
    NO RUNS SELECTED -> short-circuits at helpers.py:1039 to (True,'')
```

And on the docstring's own design target — shaped-then-character-reversed true visual-order text `'ﻞﻭﺩﺟ ﻒﻴﺭﻌﺗ ﺔﻘﻴﺜﻭﻠﺍ'`, where `_word_has_reversed_morphology` fires on **every** token — the gate still returns `(True, '')`, because the line is discarded by the selector first.

**So the gate is blind to legacy visual-order presentation-form PDFs too.** Fixing only the morphology signal would leave that path dead; fixing only the selector would leave a signal the morphology prong cannot see for canonical reversal. **Both must be fixed.**

The gate is not literally dead code: a pathological mixed line — `'جدول تعريف الوثيقة والاعتماد ﻢودج'` (≥40% canonical to pass the selector, plus one token with U+FEE2 MEEM FINAL FORM at index 0) — does return `(False, 'visual_order_garble')`. The rate is 0% by **encoding-range mismatch**, not by absence of a code path. Real extraction essentially never emits such a line.

### 2.3 The second gate, `_tree_is_rtl_reversed`, fails for a *different* reason — do not conflate

`_tree_is_rtl_reversed` (helpers.py:1192) also returns `False` on both corrupt docs, but neither the 8-sample cap (line 1240) nor the `len(stripped) < 10` filter (line 1224) is load-bearing. Removing both makes the margin **worse**:

| doc | cap=8 / minlen=10 | unlimited / minlen=0 |
|---|---|---|
| 48839446 | sampled 8, orig 9, disp 4 | sampled 301, orig 830, disp 86 |
| 29109613 | sampled 8, orig 14, disp 2 | sampled 254, orig 753, disp 67 |

The real mechanism is **score asymmetry in a sum**. `_arabic_readability_score` (converters.py:1547) is unbounded above on correct lines (max observed **28 on one line**; histogram 0→28) while a reversed line contributes only `orig 0 / disp 1`. Population isolation on 48839446: 45 reversed lines total `0 vs 65`; 256 correct lines total `830 vs 21`. **One correct line scoring 28 cancels 28 reversed lines.** The comparison fails at any sample size, which is exactly why unlimited sampling does not rescue it.

The separating statistic is a **rate**, not a sum: fraction of qualifying lines where `disp > orig` = 0.150 / 0.118 (corrupt) vs 0.000 / 0.001 (clean) — a >100× dynamic range where the sum-comparison has none. The title-level rate (§0.1 M-B) is better still: 0.923 / 0.957 vs 0.000 / 0.000.

Corruption is also **not** a needle in a haystack: 45 of 301 qualifying lines (**15.0%**) are individually detectable as reversed and the gate still passes.

---

## 3. Concrete code changes

All diffs below are **sketches** showing intent and anchor points, not final patches. Every change is additive-or-widening (HR5-tightening convention: a gate may flag more, never less).

### 3.1 F1 — stop the remote route from corrupting headings

#### F1-A — Commit the D2 Part A guard. It is currently in no commit at all.

`converters.py:1424` (`_heading_is_logical_order`) and the guarded branch at `converters.py:1485-1494` exist **only in the working tree**. The tasks file marks Task 1.11 and 1.12 `[x]`, but:

- `git log -S"_heading_is_logical_order"` → no commit.
- `grep -rl "_heading_is_logical_order" tests/` → **no test file references it anywhere**. Task 1.12's property tests **do not exist**.

Nothing is deployable and nothing is protected until this lands with tests (§4.1). **This is the highest-priority action item in this plan.**

#### F1-B — Re-normalize remote-returned markdown locally (the fix that actually repairs the failing doc)

`src/pageindex_mcp/client.py` — after the remote branch produces `md_content` (the three call sites at **client.py:832-841-853**, and the retry site at **client.py:1129-1136**), before `md_content` is written to the temp `.md` at **client.py:936-940**.

```diff
--- a/src/pageindex_mcp/client.py
@@ ~918 (immediately before `if md_content is not None:` handling, or inside it)
     if md_content is not None:
+        # RFC-033 D2 Part A (remote route): markdown returned by the external
+        # Docling service was normalized by THAT image's copy of
+        # reconstruct_bidi_order, which may predate the heading guard. Re-run the
+        # guarded normalizer locally so the working-tree guard is always the last
+        # word. Idempotent: a logical-order heading is byte-identical through the
+        # guard (Property 1), so this is a no-op on an up-to-date remote.
+        if _use_remote and used_converter and "docling" in used_converter:
+            if os.environ.get("REMOTE_MD_RENORMALIZE", "true").lower() == "true":
+                _before = md_content
+                md_content = reconstruct_bidi_order(md_content)
+                if md_content != _before:
+                    REMOTE_MD_RENORMALIZED.labels(reason="bidi_heading").inc()
+                    logger.warning(
+                        "Remote Docling markdown for %s required local bidi "
+                        "re-normalization — the deployed converter image is stale. "
+                        "Rebuild and redeploy services/docling-service.", filename,
+                    )
```

Measured effect on the *actual* captured remote output (§0.1 M-A): **23/23 headings repaired, idempotent**. Note that `reconstruct_bidi_order` is used rather than the full `_pre_inference_normalize`, because the heading-injection steps that precede it at `converters.py:2917-2922` are *not* idempotent-by-construction and must not be double-applied; the narrow call is sufficient and provably idempotent here.

New metric in `src/pageindex_mcp/metrics.py`, alongside `PDF_PRIMARY_CONVERTER_FAILURES` (metrics.py:173):

```python
REMOTE_MD_RENORMALIZED = Counter(
    "pageindex_remote_md_renormalized_total",
    "Remote Docling markdown that local re-normalization had to change — a "
    "non-zero value means the deployed converter image is behind this build.",
    ["reason"],
)
```

#### F1-C — Make the remote image's version observable and enforce skew

`services/docling-service/app.py:137` — add next to `/health`:

```diff
+@app.get("/version")
+async def version():
+    """Build provenance so the caller can detect code skew (RFC-033 F1)."""
+    return {
+        "git_sha": os.environ.get("BUILD_GIT_SHA", "unknown"),
+        "built_at": os.environ.get("BUILD_TIMESTAMP", "unknown"),
+        "converter_contract": CONVERTER_CONTRACT_VERSION,
+    }
```

`services/docling-service/Dockerfile` — accept `ARG BUILD_GIT_SHA` / `ARG BUILD_TIMESTAMP` and `ENV` them.

`src/pageindex_mcp/converters.py` — introduce a monotonically-bumped `CONVERTER_CONTRACT_VERSION` constant near the top; bump on any change to `_pre_inference_normalize` / `reconstruct_bidi_order` semantics.

`src/pageindex_mcp/client.py:545` (`_remote_pdf_to_markdown`) — fetch `/version` once per process, cache it, log it at INFO, and thread it into the job so it reaches the meta sidecar (F1-E). If the remote contract version is **lower** than the local one, emit a loud `logger.error` + `DOCLING_VERSION_SKEW.inc()`. Do **not** hard-fail the job on skew: with F1-B in place the output is repaired anyway, and failing here would trade a quality defect for an availability defect.

Then: **rebuild and redeploy the Scaleway image from the commit produced by F1-A.** F1-B is a safety net for the skew, not a substitute for fixing it.

#### F1-D — Close the AGPL fallback path (Hard Rule 4)

`converters.py:2998`:

```diff
-    chain: list[tuple[str, Callable[[str], tuple[str, list[PictureResult]]]]] = [
-        ("pymupdf4llm", _pdf_to_markdown_no_pics)
-    ]
+    # HR4: the AGPL route is opt-in only. It is NOT a silent fallback for a
+    # failing/timing-out Docling call — that turned a remote 504 into an
+    # unlogged AGPL execution (RFC-033 F1, compounding defect C-2).
+    allow_agpl = os.getenv("ALLOW_AGPL_FALLBACK", "false").strip().lower() == "true"
+    chain: list[tuple[str, Callable[[str], tuple[str, list[PictureResult]]]]] = []
+    if allow_agpl or primary == "pymupdf4llm":
+        chain.append(("pymupdf4llm", _pdf_to_markdown_no_pics))
```

and in `client.py`, when `_use_remote` and the remote call raises, prefer a **local Docling** attempt before advancing the chain, rather than falling straight through. Also route the `converters_cli` subprocess's stderr into `.run/` so a converter fallback is visible after the fact.

#### F1-E — Persist extraction provenance (closes C-1)

`src/pageindex_mcp/storage.py:423` — extend `_META_FIELDS`, and mirror in `save_doc_meta` (storage.py:481-495, omit-when-absent so legacy sidecars stay byte-identical):

```diff
 _META_FIELDS = (
     ...
     "flat_char_count",
+    "extraction_route",        # "remote" | "local" | "page_index"
+    "converter_name",          # used_converter
+    "converter_contract",      # CONVERTER_CONTRACT_VERSION seen at extraction
+    "remote_build_sha",        # from /version, "" when local
+    "page_count",
+    "inspector_class",         # RFC-032 pdf-inspector Tier-1 classification
     *_FACET_FIELDS,
 )
```

`client.py:1885-1897` — populate them in the `meta` dict from `used_converter`, `_use_remote`, `pdf_page_count` (already computed at client.py:788), and the inspector result.

Optionally persist the pre-tree markdown to `processed/<doc_id>.md` behind `PERSIST_RAW_MARKDOWN=false` (default off; **must** be added to the Hard-Rule-2 erasure cascade in `storage.py:249-332` if enabled).

### 3.2 F2 — make the detector able to detect

#### F2-A — Widen the run selector to include presentation forms (`helpers.py:1029`)

```diff
--- a/src/pageindex_mcp/helpers.py
@@ -1029,7 +1029,12 @@ def _check_bidi_coherence(...)
-        arabic_chars = sum(1 for c in stripped if "؀" <= c <= "ۿ")
+        # RFC-033 F2 cause 2: this bound was U+0600-06FF only, which EXCLUDES
+        # Arabic Presentation Forms (U+FB50-U+FEFF) — the exact range the only
+        # failure signal below lives in. Selector and signal were mutually
+        # exclusive, so a line carrying the signal scored ratio 0.0 and was
+        # discarded before _reversed_morphology was ever consulted.
+        arabic_chars = sum(1 for c in stripped if _AR_RE.match(c))
```

`_AR_RE` (helpers.py:1022) already spans `[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]`, i.e. U+0600–U+06FF, U+0750–U+077F, U+FB50–U+FDFF, U+FE70–U+FEFF. Reuse it so the two can never drift apart again.

This alone restores the gate on its **design-target** population (legacy visual-order presentation-form PDFs). It does **not** fix this corpus — that needs F2-B.

#### F2-B — Add a canonical-order reversal signal (`helpers.py:1042`)

```diff
@@ -1042,6 +1042,20 @@
-    failed = sum(1 for tokens in runs if any(_reversed_morphology(w) for w in tokens))
-    if failed / len(runs) > 0.50:
-        return False, "visual_order_garble"
+    # Prong 1 (existing): presentation-form morphology — visual-order legacy PDFs.
+    failed = sum(1 for tokens in runs if any(_reversed_morphology(w) for w in tokens))
+    if failed / len(runs) > 0.50:
+        return False, "visual_order_garble"
+
+    # Prong 2 (RFC-033 F2 cause 1, ADDITIVE): get_display()-reversed text is
+    # composed of canonical U+06xx letters and carries NO presentation-form
+    # signal at all — prong 1 is a null detector on it. Compare per-run
+    # readability forward vs reversed; a run that reads BETTER reversed is
+    # reversed. Rate, not sum: _arabic_readability_score is unbounded above on
+    # correct lines, so a sum lets a few good lines cancel many bad ones.
+    from bidi.algorithm import get_display
+    rev = sum(
+        1 for tokens in runs
+        if _arabic_readability_score(get_display(" ".join(tokens)).split())
+           > _arabic_readability_score(tokens)
+    )
+    if rev / len(runs) > _BIDI_REVERSED_RUN_RATE_THRESHOLD:
+        return False, "logical_order_reversed"
     return True, ""
```

with, near helpers.py:1048:

```python
# RFC-033 F2: fraction of sampled Arabic runs that read better reversed.
# Measured separation on the Task 9.1 frame (n=4 trees): 0.150 / 0.118 on the
# two known-corrupt docs vs 0.000 / 0.001 on the two known-clean docs. Default
# 0.05 sits an order of magnitude below the corrupt cluster and above the clean
# one. Env-overridable for calibration; see §5/§6 for the sampling frame this
# was set from — it is NOT a corpus-wide FP estimate.
_BIDI_REVERSED_RUN_RATE_THRESHOLD = float(
    os.environ.get("BIDI_REVERSED_RUN_RATE_THRESHOLD", "0.05")
)
```

Also raise the default `n_samples` (helpers.py:991) from 5 to a much larger value — the measurement showed 173 qualifying runs available on 48839446 while only 5 were inspected, and the cost is trivial local computation. Sampling was **not** the cause, but with a working signal, more samples make the rate stable.

Both prongs return a distinct reason string. `logical_order_reversed` must be routed **exactly like** `bidi_degraded` (verdict-only) — see F2-D.

#### F2-C — Add a title-level prong; it is the strongest signal available (`helpers.py:1322`)

The corruption is confined to headings, and `_flatten_tree_text` (helpers.py:554) dilutes them into the body. Evaluate titles separately, reusing the already-written D2 Part A predicate:

```python
def _tree_titles_bidi_reversed(nodes: list) -> tuple[bool, float]:
    """RFC-033 F2: title-level bidi reversal rate.

    The observed failure mode is heading-only reversal (the remote converter's
    unguarded heading branch, F1), which every full-text aggregate dilutes.
    Reuses converters._heading_is_logical_order — the same predicate the D2
    Part A guard uses — so guard and detector can never disagree about what
    "logical order" means.

    Measured (n=4 cached trees): 0.923 / 0.957 on known-corrupt vs
    0.000 / 0.000 on known-clean. Zero overlap.
    """
    from .converters import _heading_is_logical_order, _is_arabic_char

    titles = [str(n.get("title") or "") for n in _walk_nodes(nodes)]
    ar = [t for t in titles if t.strip() and any(_is_arabic_char(c) for c in t)]
    if len(ar) < _BIDI_TITLE_MIN_SAMPLE:      # default 5 — do not judge tiny trees
        return False, 0.0
    bad = sum(1 for t in ar if not _heading_is_logical_order(t))
    rate = bad / len(ar)
    return rate > _BIDI_TITLE_REVERSAL_RATE_THRESHOLD, rate   # default 0.30
```

Wire into `validate_tree` in the **verdict-only** block at helpers.py:1322-1334, OR-ed with `_check_bidi_coherence`:

```diff
     _bidi_ok, _bidi_reason = _check_bidi_coherence(full_text)
+    _titles_reversed, _title_rate = _tree_titles_bidi_reversed(structure)
+    if _titles_reversed:
+        _bidi_ok, _bidi_reason = False, f"title_bidi_reversed(rate={_title_rate:.2f})"
     if not _bidi_ok:
         if os.environ.get("BIDI_COHERENCE_ENFORCE", "true").lower() == "true":
```

`_heading_is_logical_order` is **conservative by construction**: it returns `True` for non-Arabic titles, and when both orderings score 0 it falls back to `_word_has_reversed_morphology` and still defaults to "logical" absent a positive signal. False positives are structurally unlikely — but see §6 U-3 for the frame this claim rests on.

#### F2-D — Keep every new signal on the verdict-only side of the line (hard requirement)

Three things must hold, and one of them is currently a latent trap:

1. **Do NOT add these prongs to `_tree_is_rtl_reversed` (helpers.py:1192).** That gate returns `"rtl_reversal"` (helpers.py:1306), which `client.py:1828-1839` maps to **`raise LowQualityTreeError`**. Making it more sensitive would convert these documents into ingestion failures — precisely the outcome the RFC's Risks section and the tasks-file note at line 227 forbid. A repair-first path exists (client.py:1282-1304), but on a false positive `reconstruct_bidi_order` correctly leaves a logical title untouched, re-validation still fails, and the job **raises**. Sensitivity there is a hard-failure amplifier.

2. **Latent trap — `"visual_order_garble"` is still in the raising tuple at `client.py:1828-1836`.** `validate_tree` currently returns `"bidi_degraded"`, so it never fires today. But the moment anyone "simplifies" `validate_tree` to return `_bidi_reason` directly, bidi enforcement silently becomes persistence-gating and violates the RFC. **Add a guard test (§4.4) and a comment; consider removing the string from the tuple entirely** since no code path produces it.

3. **`_bidi_degraded` must remain the only consequence.** Add the new reasons to the same set at helpers.py:1572:

```diff
-    _bidi_degraded = validate_reason == "bidi_degraded"
+    _bidi_degraded = validate_reason is not None and (
+        validate_reason == "bidi_degraded"
+        or validate_reason.startswith("title_bidi_reversed")
+        or validate_reason == "logical_order_reversed"
+    )
```

#### F2-E — Emit the measurement as a metric

Add `BIDI_REVERSAL_RATE` (Histogram/Gauge, labelled by `prong`) so the rate is observable across a full corpus cycle **before** anyone argues for persistence-gating. Task 9.1 had to be reconstructed by hand because no such series existed.

---

## 4. Tests to add — TDD, RED first

New file **`tests/test_rfc033_d2_bidi.py`**, following the conventions of `tests/test_rfc030_d4_d5.py` (module docstring naming the RFC + task, module-level Arabic fixtures with an explanatory comment, `class TestX` / `def test_y`, AAA body, direct import of the real functions).

Every test below must be written **and observed to fail** before the corresponding §3 change is made.

### 4.1 Task 1.11/1.12 — the guard's missing property tests (these do not exist today)

```python
class TestHeadingGuardLeavesLogicalOrderUntouched:
    def test_logical_order_arabic_heading_is_byte_identical(self):
        # RED today only if the guard is reverted; this is the regression lock.
        src = "# جدول تعريف الوثيقة\n\nنص عربي منطقي الترتيب هنا.\n"
        assert reconstruct_bidi_order(src) == src

    def test_reversed_arabic_heading_is_repaired(self):
        src = "# ةقيثولا فيرعت لودج\n"
        assert reconstruct_bidi_order(src) == "# جدول تعريف الوثيقة\n"

    def test_reconstruct_bidi_order_is_idempotent_on_reversed_headings(self):
        src = "# ةقيثولا فيرعت لودج\n# تايوتحملا سرهف\n"
        once = reconstruct_bidi_order(src)
        assert reconstruct_bidi_order(once) == once

    def test_latin_only_document_is_byte_identical(self):
        src = "# Section One\n\nPlain English body.\n"
        assert reconstruct_bidi_order(src) == src

    def test_heading_marker_prefix_survives_repair(self):
        # Depth inference runs right after D7 and must still see the '###'.
        assert reconstruct_bidi_order("### ةقيثولا فيرعت لودج\n").startswith("### ")
```

### 4.2 F1 — remote markdown is re-normalized locally

Fixture: copy the captured live remote output to `tests/fixtures/rfc033_remote_docling_siyasa.md` (31,314 bytes, 23 reversed ATX headings, logical-order body). Committing it makes the failure permanently reproducible without touching the remote service.

```python
class TestRemoteMarkdownRenormalization:
    def test_captured_remote_markdown_has_reversed_headings(self):
        # Characterization: proves the fixture still carries the defect.
        md = _FIXTURE.read_text(encoding="utf-8")
        headings = _ATX_RE.findall(md)
        assert len(headings) == 23
        assert "ةقيثولا فيرعت لودج" in headings

    def test_local_renormalization_repairs_all_remote_headings(self):
        md = _FIXTURE.read_text(encoding="utf-8")
        fixed = reconstruct_bidi_order(md)
        headings = _ATX_RE.findall(fixed)
        assert "جدول تعريف الوثيقة" in headings
        assert not any(h.startswith("ةقيثولا") for h in headings)

    def test_local_renormalization_is_idempotent(self):
        md = _FIXTURE.read_text(encoding="utf-8")
        once = reconstruct_bidi_order(md)
        assert reconstruct_bidi_order(once) == once

    @pytest.mark.asyncio
    async def test_remote_route_renormalizes_before_md_to_tree(self, monkeypatch):
        """RED: client.index() writes remote md_content to the temp .md unchanged."""
        # Arrange: stub _remote_pdf_to_markdown to return the fixture; capture
        # the bytes handed to _run_md_to_tree.
        # Assert: captured markdown contains 'جدول تعريف الوثيقة'
        #         and does NOT contain 'ةقيثولا فيرعت لودج'.

    @pytest.mark.asyncio
    async def test_renormalization_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("REMOTE_MD_RENORMALIZE", "false")
        # Assert the captured markdown is byte-identical to the fixture.
```

### 4.3 F2 — the detector must actually detect

```python
class TestBidiCoherenceDetectsCanonicalReversal:
    def test_reversed_canonical_heading_line_is_flagged(self):
        # RED: currently returns (True, '') — cause 1, null detector.
        text = "\n".join(["ةقيثولا فيرعت لودج"] * 5)
        ok, reason = _check_bidi_coherence(text)
        assert ok is False
        assert reason == "logical_order_reversed"

    def test_fully_reversed_tree_text_is_flagged(self):
        # The capability control: a detector with nonzero capability MUST fail here.
        text = "\n".join(line[::-1] for line in _CLEAN_ARABIC_TEXT.splitlines())
        assert _check_bidi_coherence(text)[0] is False

    def test_clean_logical_order_arabic_is_not_flagged(self):
        # FP guard — must stay green through every change.
        assert _check_bidi_coherence(_CLEAN_ARABIC_TEXT) == (True, "")

    def test_latin_text_is_not_flagged(self):
        assert _check_bidi_coherence("The quick brown fox.\n" * 5) == (True, "")


class TestBidiCoherenceSelectorIncludesPresentationForms:
    def test_visual_order_presentation_form_line_is_flagged(self):
        # RED: cause 2 — selector counts only U+0600-06FF, so this line scores
        # ratio 0.000 and is discarded before _reversed_morphology is consulted.
        text = "\n".join(["ﻞﻭﺩﺟ ﻒﻴﺭﻌﺗ ﺔﻘﻴﺜﻭﻠﺍ"] * 5)
        assert _check_bidi_coherence(text) == (False, "visual_order_garble")

    def test_selector_range_matches_the_signal_range(self):
        # Structural lock: the selector must not use a narrower range than _AR_RE.
        src = inspect.getsource(_check_bidi_coherence)
        assert '"؀" <= c <= "ۿ"' not in src


class TestTitleLevelReversalRate:
    def test_reversed_title_tree_exceeds_threshold(self):
        tree = _tree_with_titles(["ةقيثولا فيرعت لودج", "دامتعإلا لودج",
                                  "تا رادصإلا لودج", "ميهافملاو تاحلطصملا",
                                  "تايوتحملا سرهف"])
        flagged, rate = _tree_titles_bidi_reversed(tree)
        assert flagged is True
        assert rate > 0.9

    def test_clean_title_tree_scores_zero(self):
        tree = _tree_with_titles(["جدول تعريف الوثيقة", "جدول الإعتماد",
                                  "جدول الإصدار ات", "المصطلحات والمفاهيم",
                                  "فهرس المحتويات"])
        flagged, rate = _tree_titles_bidi_reversed(tree)
        assert flagged is False
        assert rate == 0.0

    def test_tree_below_min_sample_is_never_flagged(self):
        assert _tree_titles_bidi_reversed(_tree_with_titles(["جدول تعريف الوثيقة"]))[0] is False

    def test_latin_titles_are_never_flagged(self):
        assert _tree_titles_bidi_reversed(
            _tree_with_titles(["Introduction", "Scope", "Definitions",
                               "Obligations", "Annex A"]))[0] is False
```

### 4.4 Hard-rule guards — bidi is verdict-only, forever

```python
class TestBidiEnforcementIsVerdictOnlyNotPersistenceGating:
    def test_validate_tree_returns_bidi_degraded_not_visual_order_garble(self):
        ok, reason = validate_tree(_REVERSED_TITLE_TREE)
        assert ok is False
        assert reason == "bidi_degraded" or reason.startswith("title_bidi_reversed")

    def test_bidi_reason_is_not_in_the_low_quality_raise_set(self):
        """Structural lock on client.py:1828 — the raising tuple must never
        contain a bidi reason, or RFC-033 D2 Part B silently becomes
        persistence-gating (CLAUDE.md HR5 / tasks-file note line 227)."""
        raise_set = _extract_raise_reasons(Path("src/pageindex_mcp/client.py"))  # ast
        assert "bidi_degraded" not in raise_set
        assert "logical_order_reversed" not in raise_set
        assert not any(r.startswith("title_bidi_reversed") for r in raise_set)
        assert "visual_order_garble" not in raise_set   # RED today

    def test_classify_verdict_caps_at_marginal_for_every_bidi_reason(self):
        for reason in ("bidi_degraded", "logical_order_reversed",
                       "title_bidi_reversed(rate=0.92)"):
            verdict, vr = classify_verdict(_GOOD_STRUCTURE, "", reason)
            assert verdict == "MARGINAL"
            assert vr == "bidi_degraded"

    def test_bidi_reason_never_downgrades_a_worse_verdict(self):
        verdict, _ = classify_verdict(_GARBLED_STRUCTURE, "", "garbling")
        assert verdict == "FAIL"

    def test_tree_is_rtl_reversed_is_unchanged_by_this_rfc(self):
        """_tree_is_rtl_reversed feeds the RAISING path. Adding the new prongs
        there would turn these documents into ingestion failures."""
        assert _tree_is_rtl_reversed(_REVERSED_TITLE_TREE) is False
```

### 4.5 AGPL guard (Hard Rule 4)

```python
class TestAgplFallbackIsOptIn:
    def test_pymupdf4llm_absent_from_default_chain(self, monkeypatch):
        monkeypatch.delenv("ALLOW_AGPL_FALLBACK", raising=False)
        monkeypatch.setenv("PDF_CONVERTER", "docling")
        assert "pymupdf4llm" not in [n for n, _ in pdf_markdown_converters()]  # RED

    def test_pymupdf4llm_present_when_explicitly_allowed(self, monkeypatch):
        monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "true")
        assert "pymupdf4llm" in [n for n, _ in pdf_markdown_converters()]
```

---

## 5. RECOMMENDATION on Task 9.2 — `BIDI_COHERENCE_ENFORCE`

### Recommendation

> **Keep the default at `"true"` — but strike the current justification from the code comment and the tasks file, because it is backwards.**

### Why the current justification is wrong

`helpers.py:1310-1321` currently justifies the promotion as follows:

> *"promoted on Task 9.1's scoped re-ingest measurement of `bidi_coherence_violations` … that measurement is a LOWER BOUND on the clean-doc false-positive rate"*

Task 9.1 did not measure a false-positive rate. **The detector fired zero times — including on documents that are provably corrupt.** What was measured is:

| | value |
|---|---|
| Sampling frame | **5 Arabic documents of 17**, selected *because they already showed reversed headings* |
| Known-corrupt documents in frame | ≥3 (48839446, 29109613, 38f1fefe-mixed) |
| Times `_check_bidi_coherence` returned `bidi_ok=False` | **0** |
| True-positive rate on known-corrupt documents | **0 / 3 = 0%** |
| False-positive rate | **unmeasurable** — a detector that never fires cannot false-positive |

Reading "0 violations" as "safe to promote" is a **null-detector fallacy**: it mistakes an inability to fire for calibration. The frame is *enriched for corruption*, so 0 violations is not a lower bound on the FP rate — it is a direct measurement that the gate has **no sensitivity at all** (§2.1's whole-tree-reversed control settles this independently of any sampling argument).

### Why "true" is nevertheless the right default

1. **It is currently a no-op.** With 0% sensitivity, flipping it to `"false"` changes nothing observable. There is no verdict churn to avoid.
2. **The blast-radius fear was about the wrong thing.** The tasks-file warning (line 184) worried that Part B would mass-cap documents at MARGINAL *for damage Part A exists to prevent*. §1 shows that damage comes from the **remote route**, and F1-A/F1-B/F1-C close it at the source. Once fixed, the population that would be capped is the population that is genuinely corrupt.
3. **Setting it to `"false"` now creates a trap.** After §3.2 lands, the detector acquires real sensitivity. An operator who flipped it off "for safety" would silently ship a working detector into audit-only mode. Leaving it `"true"` means sensitivity arrives *enforced* — and it is verdict-only, so the worst case is a PASS capped to MARGINAL. That is exactly the intended trade.
4. **The verdict-only contract holds under the change.** Confirmed at helpers.py:1324-1330 (returns `"bidi_degraded"`, never raises) and helpers.py:1568-1577 (`_pass()` caps at MARGINAL). §4.4 locks it structurally.

### Required wording (replaces the comment at helpers.py:1310-1321 and the Task 9.2 justification)

> `BIDI_COHERENCE_ENFORCE` defaults to `"true"`. **The Task 9.1 scoped re-ingest measured 0 `bidi_coherence_violations` across 5 Arabic documents (of 17 in the corpus), selected because they already exhibited reversed headings.** That is **not** a false-positive rate: ≥3 of those 5 documents are independently confirmed bidi-corrupt, so the measurement is a **0% true-positive rate on a corruption-enriched frame** — the detector had no sensitivity, not good calibration (root cause: RFC-033 F2, `audit/BIDI_ROOT_CAUSE_RFC033.md` §2). The default is `"true"` because at 0% sensitivity it is a no-op, and because enforcement is verdict-only (caps a PASS at MARGINAL, never raises `LowQualityTreeError`, never gates persistence). **No claim is made here about the corpus-wide false-positive rate; none has been measured.** Persistence-gating remains blocked on RFC-033 D2's stated bar: <2% FP across a full corpus cycle, on an unbiased frame.

### Sequencing (do not reorder)

1. F1-A — commit the guard + its missing property tests (§4.1).
2. F1-C — `/version` + skew detection; **rebuild and redeploy the remote image**.
3. F1-B — local re-normalization safety net (§4.2).
4. F1-D, F1-E — AGPL gate, provenance in meta.
5. F2-A, F2-B, F2-C — detector fixes (§4.3), landing with `BIDI_COHERENCE_ENFORCE=true` **already** default.
6. Full corpus cycle → measure `BIDI_REVERSAL_RATE` on an **unbiased** frame (all 17 Arabic docs + the German/English corpus as negative controls).
7. Only then reopen persistence-gating.

Landing 5 before 2 would cap documents at MARGINAL for damage the pipeline is still inflicting — the exact failure the tasks file's batch separation was written to prevent.

---

## 6. What remains genuinely unknown

| # | Unknown | Why it is unresolved | What would settle it |
|---|---|---|---|
| **U-1** | Why حقوق الإنسان (cc4533aa) escaped the stale remote flip | The remote service returned **HTTP 504** on that 403k-char / 161-page PDF for both probes. Nobody has observed its remote output. H2's explanation ("its Docling output was visual-order, the blind flip repaired it") was tested and **refuted** — local Docling emits logical order, the flip would have *broken* it, and the flipped forms appear nowhere in the stored tree. The live candidates — silent fallback to another converter after the timeout, a different code version serving that request, or a local-route execution — are not distinguished by any evidence. | After F1-E lands, re-ingest cc4533aa **once** and read `extraction_route` / `converter_name` / `remote_build_sha` from the meta sidecar. Zero-cost once provenance exists; costs 38 min of LLM budget today, which is why it was not done. |
| **U-2** | Whether the AGPL (`pymupdf4llm`) route actually executed for the cc4533aa run | The `converters_cli` subprocess's converter logs do not reach `.run/*.log`, and nothing about the route is persisted. Given the remote 504s on exactly this document, it is a **live** Hard-Rule-4 exposure path — but I can neither confirm nor exclude that it fired. | Route subprocess stderr into `.run/` (F1-D) and read `converter_name` from the sidecar (F1-E). Independently: `AGPL_FALLBACK_TOTAL` is already scraped — check whether it is non-zero for the Run-15 window. |
| **U-3** | False-positive rate of the proposed title-level detector (F2-C) on the real corpus | Measured on **n=4 cached trees** (2 corrupt / 2 clean). Perfect separation (0.92/0.96 vs 0.00/0.00) on n=4 is encouraging, not conclusive. Bilingual Arabic/Latin documents, poetry, transliterated names, and tables-as-titles are all untested. The 0.30 threshold is chosen from a 4-point sample. | Run `_tree_titles_bidi_reversed` **read-only over every cached tree in MinIO** — no ingest, no LLM, minutes of compute — and publish the rate distribution before enabling the prong. This is cheap and should be done **before** F2-C lands. |
| **U-4** | Whether 38f1fefe (اتفاقية الامم المتحدة, MIXED signature) behaves like the corrupt or the clean cluster | Its tree was never cached to the scratchpad; all F2 measurements are on the other four. Its "mixed" signature (`تايوتحملا` beside a correct `المادة 1`) suggests a partial flip that neither cluster models. | Fetch that one tree read-only and run the §0.1 M-B measurement on it. Minutes, no cost. |
| **U-5** | The exact commit the remote image was built from | Dated to the **2026-07-30 .. 2026-08-04** window by the table-separator fingerprint (20 GFM-padded `\|----\|`, 0 minimal `\| --- \|`; `_repair_docling_tables` landed 2026-08-04 in `08b6eea`). The service exposes only `/health` → `{"status":"ok"}`, with no version field. | F1-C's `/version` endpoint. Until then the window is the best available bound and it is sufficient for the F1 conclusion — every candidate in that window has the unconditional flip. |
| **U-6** | Whether `_pre_inference_normalize` is idempotent in general | Verified idempotent **on the one captured remote markdown** (§0.1 M-A). Not proven for the heading-injection steps that precede it (converters.py:2917-2922). F1-B deliberately calls only `reconstruct_bidi_order` to avoid depending on the unproven property. | A property test over the full `doc_store/` markdown corpus asserting `f(f(x)) == f(x)`. Local, no LLM. |
| **U-7** | Whether any *non-Arabic* document is affected by the stale remote image | Every probe targeted Arabic PDFs. The stale build also lacks `_repair_docling_tables`, so German/English table-heavy documents ingested via the remote route in that window may carry unrepaired table markup. Not investigated. | Compare `\|----\|` vs `\| --- \|` separator counts across stored trees ingested in the window — read-only, cheap. |

### Explicit non-claims

- I did **not** re-ingest anything, run any LLM, write to MinIO, restart the server/worker, touch k3s, or modify `src/` or the tasks file.
- I did **not** verify the remote service's behaviour on حقوق الإنسان (it 504s).
- The F2 measurements are on **cached tree JSONs** in the scratchpad, not freshly-fetched MinIO objects.
- No statement in this document about a corpus-wide false-positive rate is made, because none has been measured.

---

## 7. Change inventory (file:line index)

| Fix | File:line | Change |
|---|---|---|
| F1-A | `src/pageindex_mcp/converters.py:1424,1485-1494` | **Commit** `_heading_is_logical_order` + the guarded heading branch (currently uncommitted) |
| F1-A | `tests/test_rfc033_d2_bidi.py` (new) | Task 1.12 property tests — **do not exist today** (§4.1) |
| F1-B | `src/pageindex_mcp/client.py:~918` (after 832/841/853, before 936-940; mirror at 1129-1136) | Re-run `reconstruct_bidi_order` on remote markdown behind `REMOTE_MD_RENORMALIZE` |
| F1-B | `src/pageindex_mcp/metrics.py:~173` | `REMOTE_MD_RENORMALIZED` counter |
| F1-C | `services/docling-service/app.py:137` | `/version` endpoint |
| F1-C | `services/docling-service/Dockerfile` | `ARG BUILD_GIT_SHA` / `BUILD_TIMESTAMP` |
| F1-C | `src/pageindex_mcp/converters.py` (top) | `CONVERTER_CONTRACT_VERSION` |
| F1-C | `src/pageindex_mcp/client.py:545` | Fetch/cache `/version`, log + `DOCLING_VERSION_SKEW` on skew |
| F1-D | `src/pageindex_mcp/converters.py:2998` | `ALLOW_AGPL_FALLBACK` gate (default `false`) — HR4 |
| F1-E | `src/pageindex_mcp/storage.py:423,481-495` | `extraction_route`, `converter_name`, `converter_contract`, `remote_build_sha`, `page_count`, `inspector_class` |
| F1-E | `src/pageindex_mcp/client.py:1885-1897` | Populate the new meta fields |
| F2-A | `src/pageindex_mcp/helpers.py:1029` | Selector uses `_AR_RE` (incl. U+FB50–U+FEFF) instead of `"؀"<=c<="ۿ"` |
| F2-B | `src/pageindex_mcp/helpers.py:1042-1045` | Additive canonical-reversal prong → `logical_order_reversed`; `_BIDI_REVERSED_RUN_RATE_THRESHOLD` |
| F2-B | `src/pageindex_mcp/helpers.py:991` | Raise default `n_samples` well above 5 |
| F2-C | `src/pageindex_mcp/helpers.py:~1190` (new fn), wired at `1322-1334` | `_tree_titles_bidi_reversed` |
| F2-D | `src/pageindex_mcp/helpers.py:1572` | Widen `_bidi_degraded` to all bidi reasons |
| F2-D | `src/pageindex_mcp/client.py:1828-1836` | Remove `"visual_order_garble"` from the raising tuple; add the structural test |
| F2-D | `src/pageindex_mcp/helpers.py:1192-1241` | **No change** — must stay on the raising path and stay insensitive |
| F2-E | `src/pageindex_mcp/metrics.py` | `BIDI_REVERSAL_RATE` |
| 9.2 | `src/pageindex_mcp/helpers.py:1310-1324` | Replace the justification comment with §5's wording; default stays `"true"` |
