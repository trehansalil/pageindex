# Unused-Argument Triage (ARG001 / ARG002) — 2026-09-07

- **Workflow:** `arg-unused-triage` (task `w80p3c7yc`, run `wf_9f9e528d-423`)
- **Cost:** 7 agents · 0 errors · 169 tool calls · 646K tokens · ~16 min
- **Base HEAD:** `894e13b` · **Branch:** `ICR-97-rfc44-recovery-dispatch-wiring`
- **Closes:** the ARG bucket flagged as "counted but never enumerated" by
  `DEAD_CODE_AUDIT_2026-09-05.md`, `..._2026-09-06.md` and `..._2026-09-07.md` §5.4 — three consecutive runs.

## 1. The finding

**236 of 239 sites are interface-conformance noise. 3 were real defects.** That
is 98.7% noise.

This was never a dead-code problem. It is a lint-configuration problem, and that
is precisely why three audits could not close it: **`ARG` is not in the ruff
`select` list** (`pyproject.toml:134-146` carries only E, W, F, I, UP, B, C4,
SIM, C901, PLR0913, PLR0915, RUF; `grep -n ARG pyproject.toml` returns nothing).
Every one of the 239 sites is **latent** — it fires only under an explicit
`--select ARG` and breaks no CI gate today. There was no forcing function, so
there was nothing to remediate and only something to decide.

### Count correction

The triage agent reported 238 and attributed the gap to a summary line. That is
wrong, and the corrected figure is **239**: its verification command scanned
`src/ tests/ scripts/ *.py` and omitted `services/`, which holds exactly one site
(`services/docling-service/app.py:84`, the FastAPI `app` lifespan parameter). The
default `ruff check --select ARG001,ARG002` — what the gate would run — reports
`Found 239 errors.`

| Location | Sites | Verdict |
|---|---:|---|
| `tests/**` | 181 | INTERFACE / PASSTHROUGH — 0 defects |
| `src/pageindex_mcp/helpers/gates.py` | 36 | INTERFACE — 0 defects |
| `src/pageindex_mcp/client/**` | 10 | 9 INTERFACE · 1 defect |
| scattered `src/` singletons | 11 | 9 INTERFACE · 2 defects |
| `services/docling-service/app.py` | 1 | INTERFACE (FastAPI lifespan) |
| **Total** | **239** | **236 noise · 3 defects** |

> **Out of scope, flagged:** `ARG005` (unused *lambda* argument) fires a further
> **269** times repo-wide and is entirely untriaged. Every remedy below enables
> `ARG001` and `ARG002` **by name**, never the `ARG` family, so none of it is
> silently imported.

## 2. Why `gates.py` alone accounts for 36

Not 36 independent defects — the cells of a 10×5 matrix. Ten gate functions must
all match one canonical signature:

```python
_GateFn = Callable[[TreeSignals, list, ScriptContext, int | None, RtlDecision | None], tuple[bool, str]]
```

declared at `helpers/types.py:262-265` (typing the `GateSpec.gate_fn` field at
`types.py:297`) and re-declared at `gates.py:259-262`. The gates are registered in
`GATES: list[GateSpec]` (`gates.py:361-448`), flow into `GATE_TABLE`
(`gates.py:454-458`), and are invoked from exactly one place — positionally, with
all five arguments:

```python
# helpers/tree_validation.py:426-427
for gate_fn, defect in GATE_TABLE:
    fires, detail = gate_fn(sig, structure, _script_ctx, page_count, _rtl_decision)
```

Every parameter is consumed by *some* gate — `sig` (`gates.py:45`), `structure`
(`:82`), `expected_script` (`:84`), `page_count` (`:249`), `rtl_decision`
(`:120`) — so none is vestigial at the contract level. A gate that does not need
an argument must still accept it. Deleting any one breaks the positional
dispatch.

The same shape explains `tests/` (pytest injects fixtures and parametrize ids
positionally by name; `@patch` prepends mock positionals; every fake must mirror
the signature it is monkeypatched over) and the `client/` recovery methods
(`GateSpec.recovery_fns` at `types.py:300` holds method-*name strings* invoked
reflectively from `indexer.py:1490`).

## 3. Applied — the 3 real defects

Each was re-verified by an AST scan of every call site, not by reading bodies,
and each survived an adversarial skeptic that failed to break it.

| Site | Parameter | Callers to change |
|---|---|---|
| `client/recovery.py:707` | `ext` | 1 — `indexer.py:1502` |
| `converters/normalize.py:89` | `expected_script` | 0 — all 19 call sites already pass one argument |
| `helpers/garble.py:531` | `title` | 0 — none of 34 call sites passes it; keyword-only with default |

`_recover_flat_prefer` is the one recovery method **not** in any `recovery_fns`
tuple (`gates.py:369/:377/:385/:393/:409`), so the reflective 6-arity contract
does not bind it; it is called directly and only at `indexer.py:1502`.

**One judgement call worth reversing if you disagree.** `reconstruct_bidi_order`
carried a docstring stating *"`expected_script` is accepted for call-site
compatibility but is unused."* The measurement falsifies the rationale — no call
site has ever passed it, so it was compatibility with nothing — and the parameter
plus that paragraph were removed. Restoring both is a two-line revert.

Verification: 239 → 236 ARG sites. Repo-wide ruff unchanged at 420 findings
before and after; the four touched files unchanged at 14.

## 4. NOT applied — the lint-enablement decision

This flips a CI gate, so it is yours to make, not mine. It is a four-step change
and **steps 1 and 4 must be the same commit**.

> **Ordering constraint.** `RUF100` (unused-noqa) is already enabled via the
> `"RUF"` family (`pyproject.toml:145`) under the zero-violation policy at
> `pyproject.toml:111`. The repo currently contains **zero** `# noqa: ARG`
> comments. Any noqa added *before* `ARG` is in `select` immediately becomes a
> RUF100 violation and fails the static gate.

**Step 1 — `pyproject.toml:144-145`**, add to `[tool.ruff.lint].select`:

```toml
    "PLR0915",  # too-many-statements            → gates.static.max_function_lines
    "ARG001",   # unused function argument       (NOT the "ARG" family: ARG005
    "ARG002",   # unused method argument          fires 269x on lambdas, untriaged)
    "RUF",      # ruff-specific (incl. RUF100 unused-noqa)
```

**Step 2 — `pyproject.toml:164`**, silences 181 in one line:

```toml
"tests/**" = ["PLR0913", "PLR0915", "C901", "ARG001", "ARG002"]
```

**Step 3 — `pyproject.toml:172`**, silences 36:

```toml
"src/pageindex_mcp/helpers/gates.py" = ["C901", "ARG001"]
```

**Step 4 — 19 targeted `# noqa` comments** (9 client, 9 scattered, 1 services).
Deliberately *not* a blanket `src/pageindex_mcp/client/**` ignore: those nine
have five different reasons, and a file-tree ignore would erase exactly the
signal a future audit needs.

```
client/recovery.py:521 file_path        # noqa: ARG002  # recovery_fns arity (gates.py:409) — dispatched at indexer.py:1490
client/recovery.py:525 script_context   # noqa: ARG002  # recovery_fns arity — dispatched at indexer.py:1490
client/recovery.py:583 file_path        # noqa: ARG002  # recovery_fns arity — dispatched at indexer.py:1490
client/recovery.py:586 expected_script  # noqa: ARG002  # recovery_fns arity — dispatched at indexer.py:1490
client/recovery.py:587 script_context   # noqa: ARG002  # recovery_fns arity — dispatched at indexer.py:1490
client/recovery.py:219 ext              # noqa: ARG002  # shared-tail convention; mirrored by test double tests/test_verdict.py:2219
client/recovery.py:710 script_context   # noqa: ARG002  # TODO half-landed threading — wire indexer.py:1502, then drop
client/indexer.py:958  pdf_classification # noqa: ARG002  # mirrors _persist_tree_result; masks flat-path inspector_class gap
client/indexer.py:1368 mode             # noqa: ARG002  # base-class override of PageIndexClient.index
converters/formats.py:181 match         # noqa: ARG001  # re.sub repl-callback contract — formats.py:187
metrics/sync.py:132    request          # noqa: ARG001  # Starlette handler contract — Route registered at server.py:65
worker/lifecycle.py:98 ctx              # noqa: ARG001  # arq cron contract — registered at lifecycle.py:163-164
helpers/tree_split.py:262 min_segments  # noqa: ARG001  # splitter-family arity — dispatched at tree_split.py:440-446
converters/pipeline.py:687 kwargs       # noqa: ARG001  # ConverterChainEntry (pdf_path, **kwargs) chain — indexer.py:551
tracing.py:54          kwargs           # noqa: ARG001  # langfuse mask= callable contract — installed at tracing.py:113
picture_plane.py:353   document_type    # noqa: ARG001  # RFC-044 R4.2 retention; consumer lands in design-rfc044 Phase C
helpers/types.py:404   recovery_method  # noqa: ARG001  # RFC-041 D3: 10 kwarg call sites; logging consumer pending
helpers/types.py:405   recovery_succeeded # noqa: ARG001  # RFC-041 D3: same 10 sites; logging consumer pending
services/docling-service/app.py:84 app  # noqa: ARG001  # FastAPI lifespan contract
```

Ledger: 181 + 36 + 19 + 3 removed = **239**. Enforcement stays ON everywhere
else in `src/`.

> **Separately:** the repo currently carries **420** ruff findings under its own
> documented zero-violation policy (`pyproject.toml:111`). That predates this
> work and is untouched by it, but it means the static gate's threshold and the
> tree already disagree.

## 5. Three follow-ups this triage surfaced

These are behavioural, not lint, and should not ride in the same commit.

1. **Flat persist path never sets `inspector_class`** while the tree path does —
   `client/indexer.py:1090-1094` vs `:1239`/`:1305`. This is exactly what the
   unused `pdf_classification` at `indexer.py:958` masks.
2. **`_split_on_atx_headings` hardcodes `len(starts) < 2`** at
   `helpers/tree_split.py:270`, where its three sibling splitters honour the
   caller's `min_segments` floor. Likely a behavioural bug — and the reason that
   site gets a noqa rather than a deletion.
3. **`helpers/types.py:404-405`** accept `recovery_method` / `recovery_succeeded`
   at 10 live keyword call sites but never log them. RFC-041 D3 mandates the
   logging; implement it rather than removing the parameters (removal is a hard
   build break at 10 sites).

---

*Generated from `arg-unused-triage` run `wf_9f9e528d-423`. Per-agent results in
`journal.jsonl` under the session's `subagents/workflows/` directory.*
