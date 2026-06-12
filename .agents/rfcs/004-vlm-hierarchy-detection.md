---
id: RFC-004
title: VLM-Based Document-Hierarchy Detection (research → design recommendation)
status: accepted
date: 2026-06-08
amended: 2026-06-12
plan-impact: yes
supersedes-decisions-in: []
---

> ## Amendment 1 (2026-06-08) — Open Question #1 LOCKED: flat docs are a SUCCESS, not an error
>
> **User decision, grounded in an 11-agent research workflow (`wf_f4d8007e-0a6`, ~411k tokens):
> a clean-text-layer document with no heading hierarchy is PROCESSED as a success, not rejected.**
> This **supersedes the `CONFIRMED_FLAT → FlatDocumentError → terminal flat_document` path in D1**
> (the row is updated below). Rationale: a grounded survey of 8 production systems (AWS Textract,
> Azure Document Intelligence, Google Document AI, Docling, Unstructured, LlamaParse, Reducto)
> found **every peer returns success** with a flat block list when there is no hierarchy —
> PageIndex was architecturally unique in erroring. Core reframe: **"flat" and "garbled" are
> orthogonal**; `validate_tree()` was conflating them.
>
> The four locked sub-decisions (seed a future **`FLAT-01`** contract at the RFC session):
> - **D1′ Outcome:** flat doc → job `status=done` + `content_class ∈ {flat_table, flat_kv,
>   flat_prose, flat_mixed}`, persisted to a NEW artifact `processed/<doc_id>.flat.json`.
>   `validate_tree()` is **never called** for a flat doc (no `structure[]` is built → it cannot
>   fake a tree past the gate → **HR5 holds**).
> - **D2′ Storage:** role-typed `blocks[]` (role ∈ title/prose/kv/table); tables stored as a row
>   matrix **and** verbalized `row_records` (headers repeated — the retrieval-optimal form);
>   single MinIO store (no new datastore in the HR2 cascade).
> - **D3′ Routing:** **deterministic** classifier (Docling layout model + grid / numbered-clause /
>   prose signals); **`VLM_MODE` stays `disabled` — the flat-doc fix ships WITHOUT the VLM.**
>   All 5 firing docs are **Class B (full text layer)**, so no VLM-as-OCR is needed. VLM signals
>   are an optional later refinement; Class A (text-absent) stays behind the D7 CER gate + ZDR tier.
> - **D4′ Gate semantics:** narrow terminal `low_quality_tree` to **`garbling` only**;
>   `node_count<3` / `depth<2` → flat route. `validate_tree()` (`helpers.py`) stays
>   **byte-for-byte unchanged** (HR5); the branch is on the returned *reason*, AFTER the call.
>
> **Doc→route map:** `Reitlehrer - Bereiter`, `Reitlehrer - Bereiter - Kutschfahrlehrer` →
> `flat_kv`; `GHV-TKV-Tarif` → `flat_mixed`; `Reiter-Unfallversicherung-Leistungsuebersicht`,
> `Unfallversicherung-Leistungsuebersicht` → `flat_table`. **All Class B** — the inherited
> "Class A — text-absent" labels for the two Reitlehrer one-pagers were **overturned by the panel**
> (both have a full, clean text layer; the failure is 0 Docling tree nodes, not missing text).
>
> **Phase 0 must now also cover** (in addition to the existing VLM probes): `GHV-TKV-Tarif` table
> fidelity (incl. **strikethrough = superseded** cells that PyPDF2 emits as live values); KV
> accuracy on the Reitlehrer numbered clauses; row de-dup/flatten on the Unfall leaflets; **zero
> hierarchical docs mis-route to flat** (Class C no-regress); garbling must **not** leak into the
> flat path. **Still open (deferred):** the `VLM_INCONCLUSIVE` vs `VlmCallError` boundary.
>
> Recorded in `.agents/state/PENDING_DECISIONS.md` (RFC-004 block) and memory
> [[flat-document-success-route]]. The body below is **unchanged except where this banner marks
> a supersession**; treat this amendment as authoritative on conflict.

> ## Amendment 2 (2026-06-08) — `VLM_INCONCLUSIVE` vs `VlmCallError` boundary LOCKED
>
> D1 conflated the two via "parse/**transport** failure (completed)". They sit at **different
> layers**; the dividing line is one question: **did an evaluable model completion come back?**
>
> - **`VlmCallError` (exception — NO evaluable completion).** Causes: network/TLS, HTTP 5xx, 429,
>   timeout, `RuntimeError("no running event loop")`, and permanent 4xx. **Split by retryability
>   (user decision — fail fast on permanent):**
>   - **Transient** (network, 5xx, 429, timeout) → reason **`vlm_call_failed`**, **retryable**
>     (`MAX_TRIES=2`) → DLQ on exhaustion.
>   - **Permanent** → **fail fast, terminal, no retry** (deterministic): 401/403 auth/config →
>     **`vlm_config_error`**; 400/413 bad-request/payload-too-large → **`vlm_request_invalid`**.
> - **`VLM_INCONCLUSIVE` (verdict — completion returned but UNUSABLE).** Causes: JSON
>   schema-parse failure, empty/garbage `headings[]`, model refusal, truncated output
>   (`finish_reason=length`), non-terminal stop reason (clean stop = `END_OF_SEQUENCE` /
>   `STOP_SEQUENCE`; `VlmStopReason.SUCCESS` does not exist). **Never retried** (`temperature=0`
>   ⇒ deterministic; a retry reproduces the same unusable answer). **Action (user decision —
>   conservative):** return `md` unchanged → `validate_tree()` rejects → terminal
>   **`low_quality_tree`** → PII-bounded human review (D6). It is **NOT** routed to the
>   Amendment-1 flat path: an inconclusive *structural* read must not silently persist a
>   possibly-flattened hierarchy as a flat doc.
>
> **Worker mapping delta (supersedes the D5 worker row):**
> `_CHILD_ERROR_REASON += {"VlmCallError":"vlm_call_failed"}` for transient, plus
> `{"VlmConfigError":"vlm_config_error", "VlmRequestInvalidError":"vlm_request_invalid"}` for
> permanent; `_TERMINAL_CHILD_REASONS += {vlm_config_error, vlm_request_invalid}`;
> `vlm_call_failed` stays retryable; `low_quality_tree` already terminal (unchanged).

> ## Amendment 3 (2026-06-08) — VLM track LOCKED: adopt D1/D2, pre-bake Granite (Q3), warn-only cost gate (Q4)
>
> Three VLM-track decisions adjudicated to **`accepted`** (user decision; all remain **gated on
> Phase 0** — no code moves until the throwaway experiment passes).
>
> - **D1 + D2 — ADOPTED, build scheduled behind Phase 0.** The deterministic-first
>   VLM-**escalation** cascade (D1, `default VLM_MODE=disabled`) and the hybrid engine (D2 —
>   frontier `gpt-4.1` vision **default** via `get_openai_client()`/`OPENAI_BASE_URL` ZDR lever;
>   self-hosted **Granite-Docling-258M** Apache-2.0 residency floor; no separate
>   `VLM_API_URL`/`VLM_API_KEY`) are accepted **as a scheduled build**, not merely as a deferred
>   design. **Honest scope note (load-bearing):** after Amendment 1, FLAT-01 already resolves all
>   **5** firing docs as genuinely-flat successes, so the cascade's distinct `HIERARCHY_RECOVERED`
>   path has **no proven demand in today's corpus** — it is insurance for a future
>   *flattened-hierarchy* doc class. The **one** in-corpus candidate is the **Reitlehrer numbered
>   clauses** (1 / 1.1 / 2 / 2.1 …), which *could* be a recoverable 2-level tree rather than
>   `flat_kv`. **Phase 0 carve (new):** on the 2 Reitlehrer docs, A/B **recovered-hierarchy vs
>   flat_kv** and pick the retrieval-better representation; this is the empirical test of whether
>   the cascade earns its keep at all. If Phase 0 shows zero recoverable hierarchy across the set,
>   the build narrows to "keep the design on the books, default-off, ship nothing."
> - **Q3 — PRE-BAKE the ~1 GB Granite-Docling weights into the image NOW (`HF_HUB_OFFLINE=1`).**
>   Accept the image-size / push-time / worker-RSS cost today so the inline residency fallback is
>   reproducible and air-gap-ready without a later rebuild. **Tension to manage (do not ignore):**
>   the worker is already capacity- and push-timeout-constrained ([[worker-deploy-rollout-failure]];
>   ghcr push-timeout incidents; memory cut 4Gi→512Mi). Pre-baking therefore **must** land with:
>   multi-stage layer isolation so the weights are a **cached, rarely-invalidated** layer; a
>   measured worker memory-limit bump sized to **real** peak RSS (the ~1.8–2.5 GB estimate is
>   `[UNVERIFIED]` — measure in Phase 0 before raising the limit); and the weights kept **inert at
>   boot** (Granite only instantiated when `VLM_MODE=inline`, never on the default path).
> - **Q4 — ADOPT the cost-weighted `3·FN + 1·FP` objective; encode as a WARN-ONLY / deferred
>   gate in `verify-gates.yaml`.** It is the eval **objective** and a documented release bar now,
>   **not** a hard CI gate: on the 5-doc set it reduces to "all 5 correct" (too thin) and would
>   gate on the still-`[DISPUTED LABELS]` Class-B annotations. **Promote warn-only → error** when
>   the labeled corpus is large enough to be meaningful — mirroring the RFC-003 standing
>   `validate_tree` threshold-promotion posture (the two promotions should be sequenced together).
>
> Net config-surface impact at default: **zero** — D1 ships `VLM_MODE=disabled`, Granite stays
> inert unless `VLM_MODE=inline`, the Q4 gate is warn-only. Nothing changes runtime behavior until
> VLM is explicitly enabled. Recorded in `.agents/state/PENDING_DECISIONS.md` (RFC-004 block) and
> memory [[vlm-hierarchy-detection-rfc004]].

> ## Amendment 4 (2026-06-12) — Open-questions research: Q2 RESOLVED+LANDED; Q1-residual & Q5 resolved (pending ratification); Q3/Q4 validated with corrections
>
> Grounded in a 10-agent research + adversarial-verify workflow (run `wf_4d4df1f5-8d8`; 5 research
> workers + 5 skeptics, ~501 K subagent tokens). The verify pass is load-bearing: it **refuted a
> fabricated citation, a math error, and an outdated compliance claim** — those corrections are
> folded in below and MUST be carried before any of this is cited as fact. Memory:
> [[rfc004-open-questions-research]].
>
> - **Q2 — MIGRATE `_read_pdf_outline` off PyMuPDF (AGPL) to pypdfium2: RESOLVED + LANDED.**
>   Implemented on branch `fix/rfc004-q2-agpl-outline-pypdfium2`: `pypdfium2.PdfDocument.get_toc()`
>   replaces `pymupdf.get_toc` in converters.py `_read_pdf_outline`. Two **load-bearing** 0-based→
>   1-based offsets (`bm.level + 1`, `dest.get_index() + 1`) preserve the pure-consumer contract; a
>   dest-less bookmark → page-0 sentinel. `pymupdf` dropped as a **direct** dep, `pypdfium2>=5.8.0`
>   added (pyproject + uv.lock regenerated); 4 new hermetic tests (PyPDF2-authored outline
>   round-trip) — full suite green (128 passed, 6 skipped). **Zero first-party AGPL now touches the
>   default / VLM-bound PDF path.** Residual (Q2 follow-up, NOT done): `pymupdf4llm` (the opt-in
>   fallback converter) still pulls `pymupdf` **transitively**, so the venv is not yet AGPL-free —
>   full elimination needs `pymupdf4llm` moved to an optional extra. Do **not** buy an Artifex
>   commercial license: the permissive swap was ~25 lines. (HR4 line below updated.)
> - **Q1 residual — flat-doc query surface: UNIFIED, no new MCP tool (pending ratification).**
>   Industry standard across AWS Textract / Azure DI / Google DocAI / Docling / Unstructured /
>   Reducto / LlamaParse is **one** retrieval surface with type tags at ingest, not a per-type query
>   API. Adapt flat `blocks[]` into the existing tree-RAG via a flat-doc adapter in `_search_one_doc`
>   (helpers.py) + a flat branch in `get_document`/`get_document_structure` (documents.py); the
>   Amendment-1 `row_records` verbalization is already the retrieval optimum. A 6th MCP tool would
>   force doc-type routing onto clients for no retrieval gain. **Verify correction:** the
>   arXiv:2604.01733 "unified beats separate" citation is **fabricated** (quote absent; the paper
>   actually finds BM25 > dense on financial text) — DROP it; the conclusion stands on the vendor
>   evidence (HR1: no accuracy-superiority claim is made or needed).
> - **Q5 — EU-residency + ZDR for vision (cross-cutting HR3, pending ratification).** No provider
>   offers it out-of-the-box on a standard tier. Priority ladder via a new `VLM_PROVIDER`:
>   (1) **AWS Bedrock EU inference profile**, non-Fable Claude, `data_retention_mode:none` (technical
>   400-block, strongest); (2) **Azure OpenAI GPT-4o + Modified Abuse Monitoring + EU region**;
>   (3) **Vertex AI Gemini EU + ZDR contract amendment** (24 h default logging ≠ ZDR);
>   (4) **self-hosted** (only path clear of CLOUD-Act exposure; Granite-258M alone insufficient as a
>   general VLM). **Hard constraints (confirmed):** Claude **Fable 5 / Mythos 5 are ZDR-PROHIBITED on
>   every platform** (30-day retention, no carve-out) → never a VLM target for PII; the **Anthropic
>   direct API has no EU geography** (us/global only). **Verify correction:** the research's "gpt-4.1
>   via *direct* OpenAI violates HR3 (no EU)" is **outdated** — OpenAI added EU inference residency
>   (Jan 2026, GPT-4o vision incl.), so it is no longer categorical; Azure-MAM is still preferred on
>   assurance grounds (contractual ZDR > inference residency; CPU processing may leave region). Also:
>   Bedrock EU region sets are **model-specific** and London (eu-west-2) is post-Brexit **UK** GDPR,
>   not EU — resolve the ARN's regions at runtime, don't hardcode. (Supersedes the D2 bare-`gpt-4.1`
>   default and the HR3 routing note for the EU corpus.)
> - **Q3 — pre-bake + engine: VALIDATED, one blocking correction.** Pre-baking sub-2 GB weights via
>   a dedicated multi-stage layer is industry-standard; Granite-Docling-258M is the right inline
>   engine (only sub-500M emitting explicit `<section_header_level_N>` DocTags; Docling-native
>   preset; Apache-2.0). **Correction:** the "~1.8–2.5 GB" estimate is the **ONNX** figure measured
>   on a **GPU** box; the default **PyTorch/Transformers** path is **~4.2 GB**, and the current
>   **512 Mi** worker loads **neither**. Phase 0 MUST measure real **CPU-only** peak RSS and size the
>   limit to peak + 30 %. (Qwen2.5-VL *is* a Docling preset `qwen`, not "custom plumbing" — but
>   7B/17 GB rules it out regardless.)
> - **Q4 — cost-weighted eval: VALIDATED, two corrections.** Asymmetric `3·FN+1·FP`, MCC-primary,
>   κ≥0.6 IAA, and dropping TEDS for 3–5-node trees are all industry-aligned. **Corrections:**
>   (a) the claim "MCC on n=5 = 1.0 or near-zero for one error" is **mathematically wrong** — one
>   error gives **MCC ≈ 0.61–0.67** (a coarse 6-step signal); rewrite the comment to "coarse 6-step,
>   CIs span ~[0,1], treat pass/fail until n≥30"; (b) Landis-Koch "substantial" starts at **0.61**,
>   so κ≥0.6 sits on the moderate/substantial boundary — cite as "conventional"; add a
>   **prevalence-bias** caveat (skewed STRUCTURED/FLAT marginals deflate κ even at 90 % observed
>   agreement — report raw agreement alongside κ). The **3:1 ratio needs a one-sentence domain
>   justification** (operator backfill on a false-reject vs detectable query failure on a
>   false-accept) or a self-documenting F-beta(β≈1.73).
>
> **Three artifacts to correct before they are cited anywhere:** the fabricated Q1 arXiv:2604.01733
> reference; the Q4 MCC-at-n=5 math; the Q5 "direct OpenAI has no EU option" claim. Status: Q2
> **`accepted` + landed**; Q1-residual / Q5 / Q3–Q4 corrections **proposed**, awaiting the RFC session.

> ## Amendment 5 (2026-06-12) — Phase 0 EXECUTED → VERDICT: ship FLAT only, VLM stays `disabled`; RFC ACCEPTED
>
> The Phase 0 throwaway experiment (`issue/phase0_vlm_probe.py`, renders the firing pages with
> pypdfium2, probes both engines) has **run on both engines**. The go/no-go bar is **NOT met**;
> the deterministic FLAT route (FLAT-01..05, built + gates-green 2026-06-12) is the whole shippable
> outcome. This Amendment **resolves the last open gate** and flips the RFC `proposed → accepted`.
> The `[UNVERIFIED — Phase 0]` thin-evidence flags below are now **MEASURED**.
>
> **Engine A — `gpt-4.1` vision (frontier, D2 default):** Reitlehrer recovers a clean depth-2 tree
> at BOTH 144/200 DPI with correct German text (no ﬂ-ligature corruption) — the one *positive*
> signal. **BUT `GHV-TKV-Tarif` FAILS the FP==0 bar:** at 144 DPI gpt-4.1 hallucinates a depth-2
> "hierarchy" from tariff-**grid cells** (the D6 false-positive risk), and the "table cells are not
> hierarchy" prompt rule held only at 200 DPI. **DPI-unstable for 3/5 docs** → not reliably flat;
> the cost-weighted `3·FN + 1·FP == 0` bar fails on the false-accept.
>
> **Engine B — Granite-Docling-258M, inline CPU (D2 residency floor):** measured on the target
> CPU path (transformers, fp32, 200 DPI). **NO-GO on all three axes** — and the two operational
> axes are model properties, not doc-specific, so one doc settles them:
> - **CPU peak RSS = 2,938 MB (~2.9 GB)** — the **512 Mi** worker cannot load it; even a generous
>   bump is infeasible. **This is the Q3 answer:** the inherited "~1.8 GB" figure was **ONNX-on-GPU,
>   not CPU**; CPU transformers is ~2.9 GB resident. The "pre-bake + bump to measured RSS" plan
>   (Amendment 3) is therefore moot — there is no acceptable inline-CPU memory envelope.
> - **Latency = 2,307 s (~38 min) / single page** on CPU — impossible under any `CHILD_TIMEOUT`.
> - **Quality:** 6 headers all at **level 1** (no depth-2) + **degenerate DocTags** (runaway
>   `<image>` tokens) — does not even recover the hierarchy the depth<2 class needs.
>
> **Verdict (locks D1's default and the Rollout gate):**
> - **`VLM_MODE` stays `disabled` in prod.** D1/D2 are **accepted as design-on-the-books**, not a
>   build: the `VLM-01` cascade contract family is **NOT to be built** — Phase 0 did not pass.
> - **FLAT-01..05 is the shipped outcome** and covers all 5 firing docs as flat successes.
> - **Garbling is the sole terminal `low_quality_tree` reason** (Amendment 1, now load-bearing).
> - The **Reitlehrer recovered-hierarchy-vs-`flat_kv` A/B** (Amendment 3 carve) is **not pursued**:
>   even where gpt-4.1 recovers a tree, the grid-cell FP risk + the all-engines-fail Granite floor
>   mean the cascade cannot be flipped on safely; `flat_kv` ships and is sufficient.
> - **If VLM is ever revisited** it requires **GPU + a ZDR/EU endpoint** (HR3) — never the inline
>   CPU floor. That is a fresh RFC, not this one.
> - **DECISION (2026-06-12, user-LOCKED) — Granite-Docling-258M is rejected for ALL future
>   implementations.** Not the inline residency floor, not pre-baked into the image (**Q3 REVERSED**),
>   not a fallback engine, not to be reconsidered without new model weights. The authenticated
>   HF-token re-run **reproduced** the NO-GO (peak RSS **2,829 MB**, **2,198 s/page**, 6 headers all
>   level-1, degenerate `<image>` DocTags) — operational death is a model property, not a doc
>   artifact, so the reproduction is expected and confirmatory. Any future on-prem/residency VLM
>   must be a *different*, GPU-class model selected in a fresh RFC; D2's "self-hosted residency
>   floor" slot is now **empty**, not Granite.
>
> Repro + raw numbers: `issue/phase0_results.json`, `issue/phase0_granite.log`; durable notes in
> memory `rfc004-phase0-vlm-probe.md` + `rfc004-flat-family-built.md`.

## Context

`validate_tree()` (`helpers.py`) correctly rejects genuinely-flat documents, but it
**cannot distinguish a genuinely-flat document from one whose real hierarchy the text
extractor flattened.** Both surface as the same terminal `low_quality_tree` error
(`node_count<3` or `depth<2`). The shipped heuristic recovery chain
([[node-count3-hierarchical-overprune]], [[depth2-flatprose-outline-class]],
RFC-003 Amendment 4) only recovers depth a document *declares* — via numbering prefixes
or a PDF outline. A document that carries neither, yet is visually structured, is
**false-rejected**. A document that is genuinely flat is rejected for the *right* reason
but with a misleading `low_quality_tree` label and no honest terminal status of its own.

This RFC records the output of a **deep-research session** (24-agent workflow,
2026-06-08: 8 grounding+survey agents → design brief → 3 competing methodologies each
adversarially judged on engineering / ML-accuracy / compliance lenses → principal-author
report → completeness critic → revision). It is a **`proposed` design recommendation**,
not a locked decision: per AGENT_DRIVEN_DEVELOPMENT.md §4.2, the choices below feed a
future **RFC session** that adjudicates them into `accepted` decisions and seeds the
`VLM-01` contract. **No source code moves on the strength of this document.** The gate
before any implementation is **Phase 0** (§Phase 0 — Pre-implementation gate), which is
deliberately a throwaway experiment, not a code change.

> **Auditability note (AGENT_DRIVEN_DEVELOPMENT.md §16, [[verify-source-before-asserting-defects]]).**
> Every `file:line` below was reported by the research pass's source/venv introspection on
> 2026-06-08. Line numbers drift; an implementer **must re-confirm each anchor against the
> tree at Stage-2 time** before acting on it. Where a claim is a vendor/market assertion or
> an un-reproduced model behavior it is tagged **[UNVERIFIED]** and must not gate a budget,
> a routing choice, or a release until independently confirmed.

### What the research pass measured (corrections to inherited assumptions)

One synthesis agent ran the **production `pdf_to_markdown_docling()` + real
`validate_tree()` over all 27 corpus PDFs** (throwaway probe mirroring
`issue/verify_corpus.py`, not committed). This **overturned three numbers** the design
had inherited as assumptions:

1. **The escalation set is 5/27 docs (≈18.5%), not the assumed ~8/27 (~30%).** This
   **meets the ≤20% escalation budget with no upstream doc-type filter** — a constraint
   conflict the earlier draft could not resolve is retired.
2. **Only 2 of 3 Reitlehrer one-pagers are false-rejected**, not 3.
   `Reitlehrer – Schäden am Berittpferd` **passes today** (Docling recovered numbering →
   9 nodes, depth 3) and is out of scope.
3. **`Downloadbereich` and `Tarifblatt-Privat` pass the gate today** (`recov=True`) and
   never escalate — so the escalation math has **no dependency** on a Downloadbereich
   filter (it is reframed as a separate, lower-priority product-quality cleanup).

### The 5 firing documents (MEASURED 2026-06-08)

| Firing doc                                        | Today's gate reason | Docling nodes | Class             |
| ------------------------------------------------- | ------------------- | ------------- | ----------------- |
| `Reitlehrer - Bereiter`                         | `node_count<3`    | 0             | A — text-absent  |
| `Reitlehrer - Bereiter - Kutschfahrlehrer`      | `node_count<3`    | 0             | A — text-absent  |
| `GHV-TKV-Tarif`                                 | `node_count<3`    | 1             | A — flat grid    |
| `Reiter-Unfallversicherung-Leistungsuebersicht` | `depth<2`         | 4             | B — text-present |
| `Unfallversicherung-Leistungsuebersicht`        | `depth<2`         | 6             | B — text-present |

**The two classes need different fixes and must not be lumped together** (the earlier
draft's central error):

- **Class A — text-absent / unheaded** (`node_count<3`, 0 headings, often 0 nodes). The
  layout model emitted no headings and the heading **text itself may be missing** from
  Docling's markdown. Recovery would need the VLM **as OCR**, making German-clause text
  fidelity (the project's own `ﬂ`-ligature history) the make-or-break risk. **High risk.**
- **Class B — text-present-but-flat** (`depth<2`, several nodes). Body text is extracted
  but all at one heading level. The VLM only needs to **re-tag levels** on existing lines
  — no OCR, no fidelity risk. **Low risk; ships independently.**

## Decisions (LOCKED 2026-06-12, Amendment 5 — D1/D2 accepted as design-on-the-books; VLM stays `disabled`, cascade not built; FLAT-01..05 shipped)

### D1 — Architecture: deterministic-first, VLM-escalation cascade, **default OFF**

Add a two-stage cascade, hooked in the converter child immediately before `return md`
(reported `converters.py:940`):

- **Stage 0 (free, 100% of docs):** the existing heuristic chain. If
  `_has_recoverable_structure(md)` is true → return. **The VLM is never imported or
  loaded.** All 18 Cat A/B/C docs satisfy this (measured §Evaluation) — none load the VLM.
- **Stage 1 trigger — the narrow Cat-D signature:**
  ```python
  if settings.vlm_mode != "disabled" and not _has_recoverable_structure(md) and not toc:
      ...escalate...
  ```

  Measured to fire on exactly **5/27** docs, all with `toc==0` and `recov==False`. The
  `not toc` clause is load-bearing: an outline-bearing doc already recovered via the
  outline page-spine ([[depth2-flatprose-outline-class]]).

**Three-way verdict** (the VLM **emits a heading tree**, never builds the final PageIndex
tree, never writes to MinIO):

| Verdict                 | Condition                              | Pipeline action                                                                                                                      |
| ----------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `HIERARCHY_RECOVERED` | `max_depth ≥ 2 AND node_count ≥ 3` | inject `#`/`##` (or synthesize body — Class A) → **unchanged** `md_to_tree` → **unchanged** `validate_tree()` |
| `CONFIRMED_FLAT`      | no multi-level heading structure       | **[SUPERSEDED by Amendment 1]** ~~raise `FlatDocumentError` → terminal `flat_document`; nothing persisted~~ → **route to flat extraction (D3′): `route_and_extract_flat()` → `save_flat_doc()` → `processed/<doc_id>.flat.json`, job `status=done` + `content_class`. `validate_tree()` is NOT called (no tree built; HR5 holds).** |
| `VLM_INCONCLUSIVE`    | **[clarified by Amendment 2]** completion returned but UNUSABLE (parse/refusal/truncation — **NOT** transport; transport is `VlmCallError`) | return `md` unchanged → `validate_tree` rejects → terminal `low_quality_tree`; PII-bounded human-review (D6). Never retried; **not** routed to the Amendment-1 flat path |

**Class A/B decision rule (mandatory):** before invoking the VLM as OCR, check whether
Docling's markdown already contains the body text for the firing pages. **Class B**
(present) → VLM proposes *levels only*. **Class A** (near-empty) → recovery must
**synthesize** body text from VLM output, and the German-OCR fidelity gate (D7) applies.

### D2 — Engine: hybrid (frontier API default, self-hosted residency floor)

- **Default recovery engine: `gpt-4.1` vision**, routed through the *existing*
  `get_openai_client()` / `OPENAI_BASE_URL` ZDR lever (reported `client.py:40`, Azure-routed
  when `_is_azure_url()` matches). Chosen because German 7pt typography is exactly where a
  258M model is least proven, and because it adds **~0 worker RSS** on the 512 Mi–4 Gi
  worker ([[worker-deploy-rollout-failure]]). `response_format=json_schema`, `temperature=0`.
- ~~**Residency floor: self-hosted Granite-Docling-258M** (`VLM_MODE=inline`, Apache-2.0),
  the *only* surveyed open model that emits real heading levels
  (`section_header_level_1..6` DocTags, **[VERIFIED in docling_core]**), for air-gapped /
  no-ZDR deployments.~~ **REVERSED 2026-06-12 (Amendment 5, user-LOCKED): Granite-Docling-258M is
  rejected for ALL future implementations** — Phase 0 measured it operationally dead on CPU
  (2.8–2.9 GB RSS, ~38 min/page, level-1-only, degenerate output). The residency-floor slot is now
  **empty**; any future on-prem VLM is a *different* GPU-class model chosen in a fresh RFC.
- **Ruled out:** SmolDocling (no level attribute); Qwen2.5-VL-3B / InternVL3 (exceed
  4 Gi); Nougat (CC-BY-NC); Donut / LayoutLMv3 / GraphDoc (no hierarchy / weights /
  license). Claude (Bedrock-EU) and Gemini-2.5-Flash (Vertex-EU) are viable alternatives
  on the *same* lever — pick post-Phase-0; all EU-ZDR-for-vision claims are **[UNVERIFIED]**.

**Deliberately NO separate `VLM_API_URL` / `VLM_API_KEY`.** Routing reuses
`OPENAI_BASE_URL` / `OPENAI_API_KEY` so **no path can bypass ZDR** (Hard Rule 3).

### D3 — Two **blocking** integration fixes (verified by the research pass; re-confirm at Stage 2)

1. **Exception swallowing.** A bare `except Exception as conv_exc` in the converter loop
   (reported `client.py:129`) would silently eat the new `VlmCallError` raised from
   `pdf_to_markdown_docling()`. **[Amendment 1]** `FlatDocumentError` is **dropped** (the flat
   path **returns** markdown/blocks, it does not raise), so only `VlmCallError` needs the
   re-raise; the swallow-then-fall-through-to-AGPL hazard still applies to it. In the **default
   `PDF_CONVERTER=docling`** chain `[docling, pymupdf4llm]` it then falls through to
   **pymupdf4llm (AGPL → Hard Rule 4 violation)** *and* loses the verdict signal. **Fix:**
   re-raise the named exceptions before the bare except. *(The existing
   `LowQualityTreeError` is raised at `client.py:222`, outside this loop, and is
   unaffected — the earlier draft conflated the two paths.)*
2. **Async/sync boundary.** `pdf_to_markdown_docling()` is synchronous, invoked via
   `await asyncio.to_thread(conv_fn, ...)` (reported `client.py:126`); inside that worker
   thread there is no event loop, so `await get_openai_client()...create()` raises
   `RuntimeError("no running event loop")`. **Fix:** wrap the call in `asyncio.run(...)`
   inside the sync helper — the pattern `_run_md_to_tree` already uses
   (reported `client.py:367`).

Plus verified gotchas to honor: `openai_base_url` defaults to the **non-ZDR**
`https://api.openai.com/v1` (D6 startup assertion); `toc`/`doc_sha256` are not in scope at
the hook site (recompute / second `_read_pdf_outline`); `VlmStopReason.SUCCESS` does not
exist (use `END_OF_SEQUENCE`/`STOP_SEQUENCE`); the inline spec is **fp32 ~1 GB not
500 MB** (`load_in_8bit` never applied, `bitsandbytes` absent — pin bf16/CPU explicitly).

### D4 — Heading-text match is load-bearing, built in the PoC (not deferred)

The VLM emits verbatim text read from the *image*; injection happens against *Docling's
text-layer markdown*. For the two Class-A Reitlehrer docs Docling has **0 headings / ~0
nodes**, so naive substring match injects nothing. Required before any recovery code:
normalize both sides (dash + **ligature** normalization — the `ﬂ`→`Haftpficht` trap);
fuzzy / longest-common-substring fallback with a tuned threshold; a **per-heading
match-failure counter as a required metric**; and, for Class A, the **synthesis path**
that builds body text from VLM output (gated by D7).

> **Correction (Hard Rule 5):** the VLM's self-reported `max_depth`/`node_count` is a
> **cheap necessary filter, not a binding guarantee.** The built tree (after heading-match
> may drop headings, or synthesis may over-inject) can differ from what the VLM proposed.
> The **sole binding guard remains the unchanged `validate_tree()` over the actually-built
> tree.** The earlier draft's "the pre-check mirrors the gate so the VLM can't propose a
> rejectable tree" guarantee is **false and withdrawn.**

### D5 — Surfaces touched (seeds the `VLM-01` contract)

| Module            | Change                                                                                                                                                                                                                             |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `converters.py` | new `_vlm_recover_or_classify(pdf_path, md, toc, doc_sha256) -> str` at the hook site; runs in the converter child                                                                                                               |
| `helpers.py`    | new `VlmCallError` (transient) **+ `VlmConfigError`, `VlmRequestInvalidError` (permanent, Amdt 2)** beside `LowQualityTreeError`. `validate_tree()` **byte-for-byte unchanged** (HR5). **[Amendment 1]** `FlatDocumentError` **dropped** (flat is no longer an error); add `route_and_extract_flat(md, layout) -> (content_class, blocks)` — the deterministic grid/numbered-clause/prose router (D3′) |
| `worker.py`     | **[AMENDED by Amendments 1 & 2]** `flat_document` is **no longer a terminal error** — flat docs return a non-error `status=done` + `content_class` branch (Amdt 1). **Error taxonomy (Amdt 2):** `_CHILD_ERROR_REASON += {"VlmCallError":"vlm_call_failed", "VlmConfigError":"vlm_config_error", "VlmRequestInvalidError":"vlm_request_invalid"}`; `_TERMINAL_CHILD_REASONS += {vlm_config_error, vlm_request_invalid}` (permanent, fail-fast); `vlm_call_failed` stays retryable (`MAX_TRIES=2`); `VLM_INCONCLUSIVE` rides the existing terminal `low_quality_tree` path (no new reason). ~~`FlatDocumentError→flat_document`~~ dropped |
| `config.py`     | `vlm_mode` (disabled\|api\|inline, default **disabled**), `vlm_model` (`gpt-4.1`), `vlm_max_pages_per_doc` (5), `vlm_artifacts_path`, `vlm_cache_ttl_seconds`. **No** `VLM_API_URL`/`VLM_API_KEY` (D2). **[+ Amendment 1]** `flat_doc_routing` bool (default **true**) gating the flat route (kill-switch, no redeploy) |
| `metrics.py`    | `VLM_HIERARCHY_RECOVERIES`, `VLM_FLAT_CONFIRMED`, `VLM_HEADING_MATCH_FAILURES`, `VLM_SYNTHESIZED_BODY_CER` (shadow/Phase-0). **[Amendment 1]** add `FLAT_DOCS_TOTAL{content_class}`; `LOW_QUALITY_TREES{reason}` now counts **garbling only**                                                                                                |
| `storage.py`    | extend the HR2 erasure cascade (D6). **[Amendment 1]** add `save_flat_doc`/`get_flat_doc` for `processed/<doc_id>.flat.json`, include it in `list_docs`, and append it as **erasure-cascade step (4)**                                                                                                                                                                                                |

Rasterization is **pypdfium2 only** (BSD-3/Apache-2, already a Docling transitive dep) —
PIL image held in RAM, never written to disk; **no `pymupdf.get_pixmap()` in the VLM
path** (Hard Rule 4).

### D6 — Compliance deltas (the critic's top findings, closed)

- **Hard Rule 2 — erasure must cascade to the NEW derived stores.** `delete_doc()`
  (reported `storage.py:95-166`) today purges `uploads/` → `processed/*.json` →
  `*.meta.json` → Redis doc cache → hash-cache. **[Amendment 1]** the flat-doc path adds
  **(4′) `processed/<doc_id>.flat.json`** — a new derived store that **must** join the cascade
  (it can carry PII clause/table text). The VLM path adds stores that **must be
  appended**: (6) the Redis `vlm_hierarchy:{doc_sha256}:{model}` verdict cache — keyed on
  `sha256`, **its value can encode heading TEXT (PII)** — `DEL` every
  `vlm_hierarchy:{sha}:*`; (7) any persisted `VLM_ARTIFACTS_PATH` review artifacts;
  (8) the human-review log entries — purgeable by `doc_id`/`sha`. `ARCHITECTURE.md` /
  `DESIGN.md` erasure sections update **in lockstep**, not after.
- **Hard Rule 3 — transport AND content.** Default OFF. **A hard startup assertion ships
  with the feature:** if `vlm_mode != "disabled"` and `OPENAI_BASE_URL` is the default /
  not on a ZDR allowlist → `ConfigurationError` (closes the "configmap omitted the env
  var" silent fallback). The human-review log stores **`doc_id` + `sha` + verdict + reason
  + match-failure count only — never page images, never `headings[].text`** (reviewer
    re-renders from the in-residency raw upload). The cache value carries PII → TTL-bounded
    **and** in the cascade. **Second egress flagged:** after `HIERARCHY_RECOVERED` the
    injected-heading markdown re-enters `md_to_tree` with `if_add_node_summary="yes"` /
    `if_add_node_text="yes"` (reported `client.py:340-353`) on the **same lever** — recovered
    German clause text now flows to the summary LLM; the single ZDR assertion covers it iff
    `OPENAI_BASE_URL` is correctly pointed (a reason the single-lever design is safer).
- **Hard Rule 4 — pypdfium2-only raster (D5).** Honest flag: the `not toc` check calls
  `_read_pdf_outline()` → PyMuPDF `get_toc` (AGPL, reported `converters.py:381`) — a
  **pre-existing, read-only** exposure, not expanded here. Recommend migrating that TOC read
  to pypdfium2 so zero AGPL touches a VLM-bound doc (legal review, not settled).
- **Hard Rule 5 — gate unchanged.** `validate_tree()` is byte-for-byte unchanged and
  remains the sole authority before any MinIO write; `CONFIRMED_FLAT` raises **before**
  `md_to_tree`, persisting nothing. **Residual risk:** over-recovery on a German tariff
  grid (`GHV-TKV-Tarif`) could yield a plausible depth-2 sibling tree that passes the gate
  and persists garbage. The "grids fail depth≥2" mitigation **does not reliably hold**; the
  real guard is the prompt rule ("table/grid column labels and navigation links are NOT
  hierarchy → `CONFIRMED_FLAT`"), whose **FP==0 reliability on `GHV-TKV-Tarif` must be
  empirically confirmed before `VLM_MODE` leaves `disabled`.**

### D7 — German-OCR text-fidelity gate (Class-A synthesis only)

When recovery **synthesizes** body text, mis-OCR'd clause bodies would produce
**semantically garbled but byte-clean text that passes `validate_tree`'s garbling check**
(which catches only null/replacement/control bytes, not semantic garble) — and persist
silently, an HR5-adjacent hole. Mandatory for the synthesis path: a **Character Error Rate
(CER)** metric vs a hand-keyed reference for the 2 Class-A Reitlehrer docs (if CER is
unacceptable on a model, synthesis does not ship for it — likely restricting synthesis to
the frontier API, not the inline 258M); synthesized nodes **tagged**
`text_source: vlm_synthesized` in metadata so downstream consumers can tell extractor-
faithful from VLM-OCR'd text. Class B **never** synthesizes.

## Phase 0 — Pre-implementation gate (throwaway experiment; gates the whole design)

Before writing any recovery code, render the 5 firing pages at **144 and 200 DPI** and run
**both** `gpt-4.1` vision and inline Granite-Docling. Answer, per class:

- **Class B first (cheap, low-risk):** can the VLM propose correct *levels* for the
  already-extracted lines of the two Leistungsübersicht leaflets (no OCR)? If yes, a
  **Class-B-only partial win ships even if Class A fails.**
- **Class A (hard):** for the 2 text-absent Reitlehrer docs — (a) does the VLM emit a
  correct 2-level tree? (b) is synthesized-body **CER** acceptable (D7)? (c) reproduce the
  "feeding docling PDF-bytes flattens levels" claim **directly** (render-vs-bytes A/B) —
  the research pass **could not verify** the GitHub issue it was attributed to.
- If Class A fails for both models, **do not build the synthesis path**; ship Class-B
  re-leveling + the honest `flat_document` split for `GHV-TKV-Tarif`.
- **Reitlehrer A/B — recovered-hierarchy vs `flat_kv` (added by Amendment 3; note Amendment 1
  reclassified both Reitlehrer docs to Class B — full text layer, 0 Docling nodes, NOT
  text-absent).** Their numbered clauses (1 / 1.1 / 2 / 2.1 …) *may* form a recoverable 2-level
  tree. Build both representations, measure retrieval quality, and pick the better one. **This is
  the empirical test of whether the D1/D2 cascade earns its keep**: if neither Reitlehrer nor any
  other firing doc yields a hierarchy that beats its flat form, the adopted build narrows to
  "design on the books, `VLM_MODE=disabled`, ship nothing" — FLAT-01 alone covers the corpus.

## Evaluation plan

**Gold set + no-regress (MEASURED 2026-06-08).** The production converter + real gate over
all 27 PDFs proves `vlm_fired == False` for **all 18 Cat A/B/C docs** (each has `recov==T`,
so the `not recov and not toc` trigger cannot fire) — and for the 4 Cat-D docs that pass
today (`Downloadbereich`, `Reitlehrer – Schäden`, `Hundehalter-Unfall-Leistung`,
`Tarifblatt-Privat`). There is **no Cat A/B/C doc in a `recov=F and toc=0` edge state.**

**Metrics:** MCC (primary; on the 5-doc set this reduces honestly to "all 5 correct");
per-node precision/recall + exact-match on the 2 Class-A reference trees (**TEDS dropped
for the small corpus** — it collapses on 3–5-node trees; reserved for borrowed datasets
only); **CER** (D7); escalation rate (**measured 18.5%, target ≤20% MET**); heading
match-failure rate (must be ~0); **Cohen's κ ≥ 0.6** two-annotator STRUCTURED/FLAT labels
— **mandatory** because the 2 Leistungsübersicht docs have **disputed labels** (FLAT per
brief, possibly STRUCTURED).

**Go/no-go bar (before flipping `VLM_MODE` off `disabled` in prod):** Class-A 2/2 recover

+ CER acceptable; Class-B 2/2 correct per κ-validated labels; **`GHV-TKV-Tarif` →
  `flat_document` (FP==0)**; Cat A/B/C 18/18 no-regress (already proven); cost-weighted
  `3·FN + 1·FP == 0` on the 5-doc set (false-reject penalized 3× false-accept).

## Rollout

- **Phase 1 — PoC, default OFF.** Build `_vlm_recover_or_classify` + both D3 fixes + the D6
  startup ZDR assertion + the HR2 cascade extension + the D4 heading-match strategy & its
  counter + the Class-A/B rule + the `flat_document` worker mapping. Validate behind
  `VLM_MODE=eval` against the go/no-go bar.
- **Phase 2 — Shadow.** `VLM_MODE=api` in staging on a confirmed ZDR endpoint, logging what
  it *would* have done vs the real gate. Measure escalation rate, latency, match-failure
  rate, **metered token cost**, FP/FN; confirm inline real peak RSS + per-page latency on
  the target k8s node.
- **Phase 3 — Gated prod.** Flip on only after the bar passes, ZDR+EU endpoint confirmed
  (or inline weights baked + `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`), HR2 cascade
  shipped, and the backfill is queued.
- **Backfill.** Turning the feature on does **not** retro-recover docs already terminated
  `low_quality_tree` (never persisted); the SHA-dedup cache (reported `client.py:103`)
  short-circuits re-upload. Procedure: identify terminal `low_quality_tree` docs → **evict
  their filename→sha256 hash-cache entries** (else re-upload is a no-op) → re-enqueue.
- **Kill-switch under cache.** `VLM_MODE=disabled` is a **forward** switch — it stops new
  calls but never rewrites history. To revert VLM-affected docs: `delete_doc(doc_id)` (now
  incl. `vlm_hierarchy:{sha}:*`) → re-ingest with VLM off. The cache is **model-slug-keyed**,
  so a *model* rollback cleanly misses the old cache and recomputes.

## Open questions (ALL RESOLVED — Q1/Q2 landed, Q3/Q4 measured, Phase 0 locked by Amendment 5)

1. ~~Should `flat_document` be a terminal **error** or a **success-ish** status routed to
   table/key-value extraction?~~ **RESOLVED 2026-06-08 (Amendment 1): success-ish.** Flat docs
   are processed into a non-tree `processed/<doc_id>.flat.json` artifact (`status=done` +
   `content_class`), routed by a deterministic table/KV/prose classifier with the VLM off. The
   `/upload/status` contract in `DESIGN.md` gains a `content_class` field on the `done` response.
   ~~Remaining sub-item for the RFC session: confirm whether `flat_document`/`content_class` should
   ALSO be queryable via the existing RAG tools or needs a dedicated flat-doc query surface.~~
   **RESOLVED 2026-06-12 (Amendment 4): UNIFIED surface** — adapt flat `blocks[]` into the existing
   RAG via a flat-doc adapter in `_search_one_doc`; **no** dedicated/6th MCP tool.
2. ~~Migrate `_read_pdf_outline` off PyMuPDF (AGPL) to pypdfium2 so zero AGPL touches a
   VLM-bound doc? (Legal, owner-owned — relates to RFC-003 standing AGPL §13 gate.)~~ **RESOLVED +
   LANDED 2026-06-12 (Amendment 4):** migrated to `pypdfium2.get_toc()` on branch
   `fix/rfc004-q2-agpl-outline-pypdfium2`; `pymupdf` dropped as a direct dep. Residual: `pymupdf4llm`
   fallback still pulls PyMuPDF transitively (follow-up: make it an optional extra).
3. ~~Pre-bake the ~1 GB Granite weights into the image now (offline/air-gap) or defer until a
   residency deployment needs the inline fallback?~~ ~~**RESOLVED 2026-06-08 (Amendment 3):
   pre-bake NOW (`HF_HUB_OFFLINE=1`)** — air-gap-ready, behind multi-stage layer isolation, kept
   inert at boot (only instantiated when `VLM_MODE=inline`), with a worker memory-limit bump
   sized to *measured* peak RSS (Phase 0).~~ **REVERSED 2026-06-12 (Amendment 5, user-LOCKED): do
   NOT pre-bake — Granite-Docling-258M is rejected for ALL future implementations** (Phase 0
   measured CPU RSS 2.8–2.9 GB / ~38 min per page / level-1-only). Nothing to bake; the inline
   residency engine is unselected and is a fresh-RFC question.
4. ~~Adopt the cost-weighted `3·FN + 1·FP` objective as a governance gate
   (`verify-gates.yaml`), alongside the RFC-003 deferred `validate_tree` threshold promotion?~~
   **RESOLVED 2026-06-08 (Amendment 3): adopt as the eval objective + a WARN-ONLY / deferred
   `verify-gates.yaml` gate**; promote warn-only→error (sequenced with the RFC-003 `validate_tree`
   threshold promotion) once the labeled corpus outgrows the thin 5-doc "all 5 correct" set.

> **D1/D2 adoption (Amendment 3): ACCEPTED — build scheduled behind Phase 0.** See the Amendment 3
> banner for the honest scope note (FLAT-01 already resolves the 5 firing docs; the cascade's
> `HIERARCHY_RECOVERED` path is insurance for a future flattened-hierarchy class, with the
> Reitlehrer numbered clauses as the one in-corpus candidate → new Phase-0 A/B carve).

## Thin-evidence flags (must not gate budget/routing/release until confirmed)

- **[UNVERIFIED — Phase 0]** Whether any VLM recovers the 2 Class-A Reitlehrer trees at
  7 pt / 2-column, and whether synthesized-body CER is acceptable.
- **[UNVERIFIED — Phase 0]** Heading-text match (Class B) vs forced synthesis (Class A —
  measured 0 Docling headings); and the docling-PDF-bytes-flatten rationale.
- **[UNVERIFIED]** Inline CPU latency under `max_new_tokens=8192` vs `CHILD_TIMEOUT`; real
  peak RSS (~1.8–2.5 GB estimate, not measured on the target node).
- **[PARTIALLY RESOLVED 2026-06-12 — Amendment 4 Q5]** EU-ZDR-for-vision availability surveyed:
  provider ladder Bedrock-EU(ZDR) > Azure-OpenAI-MAM-EU > Vertex-EU(+ZDR amendment) > self-hosted;
  Fable 5/Mythos 5 ZDR-PROHIBITED everywhere; OpenAI added EU inference residency (Jan 2026). Still
  **[UNVERIFIED — vendor contract]**: per-account ZDR approval, MAM approval timelines, cloud pricing.
- **[UNVERIFIED]** Borrowed-dataset licenses / venues / arXiv IDs (incl. a future-dated
  `arXiv:2603.11044` the pass flagged as suspect).
- **[DISPUTED LABELS]** The 2 Class-B Leistungsübersicht docs — reannotate (κ) before
  trusting the eval.

## Hard-Rule compliance summary

- **HR1** — nothing here claims the VLM-augmented pipeline beats vector RAG (or any
  baseline) on accuracy. Positioning is purely architectural: replace a blunt
  `low_quality_tree` rejection with a gated, residency-safe, inspectable disambiguation.
- **HR2** — three new derived stores named into the erasure cascade (D6) before prod.
- **HR3** — single ZDR lever, default OFF, hard startup assertion, PII kept out of logs,
  second summary-hop egress covered (D6).
- **HR4** — pypdfium2-only raster **and** outline/TOC read (PyMuPDF `get_toc` migrated to
  `pypdfium2.get_toc()`, Amendment 4 — zero first-party AGPL on the default/VLM-bound path).
  Residual: the opt-in `pymupdf4llm` fallback still pulls PyMuPDF transitively (follow-up: optional
  extra).
- **HR5** — `validate_tree()` unchanged and sole binding guard; `CONFIRMED_FLAT` persists
  nothing; tariff-grid FP risk has an empirical FP==0 release gate (D6).

## Provenance

- Research workflow run `wf_a210f515-a91` (2026-06-08): 24 agents, ~1.62 M subagent tokens,
  894 tool uses, ~40 min. Survey families: frontier VLM APIs; open self-hostable
  doc-structure models; Docling-native `VlmPipeline`; hierarchy-specific datasets/SOTA;
  hybrid heuristic+VLM escalation. Critic verdict on the pre-revision draft: `revise`
  (medium) — every finding folded into this RFC.
- Measured corpus tables produced by a sibling probe of `issue/verify_corpus.py` (not
  committed). Reproduce before relying on any per-doc figure.
- Related memory: [[node-count3-hierarchical-overprune]], [[depth2-flatprose-outline-class]],
  [[verify-source-before-asserting-defects]], [[worker-deploy-rollout-failure]].
