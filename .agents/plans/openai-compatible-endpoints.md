# Plan: First-class OpenAI-compatible endpoint support (provider abstraction)

## Goal
Make an OpenAI-compatible endpoint (vLLM / Together / Groq / OpenRouter / local, and Azure)
a deterministic, validated, documented configuration — reusing `OPENAI_API_KEY` +
`OPENAI_BASE_URL` — across **both** LLM paths.

## Verified preconditions (the "check compatibility here first" requirement)
- Query path uses the OpenAI SDK and already honors `base_url`/Azure. ✓
- Ingestion path uses the **fork → litellm**; the fork takes only the API key and passes
  no `api_base`. litellm resolves `api_base = api_base or litellm.api_base or
  OPENAI_BASE_URL or OPENAI_API_BASE` (`litellm/main.py:2035`). Env propagates to the
  subprocess via `worker.py:209 env=os.environ.copy()`.
- Conclusion: a "compatible" endpoint == OpenAI provider + custom `base_url`; it already
  works *implicitly* but must be made *explicit* (set `litellm.api_base` ourselves) and
  Azure-on-ingestion must be wired, not left to break silently.

## Design — explicit provider model, reusing OPENAI_* creds

### 1. `config.py` (cross-cutting; pure data only — no LLM import)
- Add `llm_provider: str` to `Settings`, read from `LLM_PROVIDER` env (default `"auto"`).
  Allowed values: `auto | openai | compatible | azure`.
- Keep `openai_api_key`, `openai_base_url`, `azure_api_version` as-is.

### 2. `client.py` (provider/service layer — owns all openai/litellm construction)
- `resolve_llm_provider() -> str`: normalize `auto` → `azure` if `_is_azure_url(base_url)`
  else `openai`; pass `openai`/`compatible`/`azure` through. Keep `_is_azure_url`.
- Generalize `get_openai_client()` to switch on the resolved provider:
  - `azure` → `AsyncAzureOpenAI(azure_endpoint=base_url, api_version=...)`
  - `openai`/`compatible` → `AsyncOpenAI(base_url=base_url, api_key=...)`
- Add `configure_litellm() -> None`: deterministically point the fork's bare litellm calls
  at the resolved endpoint (the new robustness):
  - `openai`/`compatible`: set `litellm.api_base = settings.openai_base_url`,
    `litellm.api_key = settings.openai_api_key`.
  - `azure`: set `litellm.api_base`, and the azure env litellm requires
    (`AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION`); document that
    `PAGEINDEX_MODEL` must use the `azure/<deployment>` form for ingestion.
- Add `validate_llm_config() -> None`: fail fast with a clear message when
  provider=azure and `azure_api_version`/base_url missing, or key empty in non-test runs.

### 3. `converters_cli.py` (ingestion subprocess entry — provider layer)
- Call `configure_litellm()` (and `validate_llm_config()`) once at startup, before
  `client.index()`. This is what removes the reliance on litellm env luck and makes the
  compatible/azure endpoint authoritative for ingestion.

### 4. Contract + tests
- New `.agents/contracts/llm-01.yaml` (module: `client`) with contract IDs, e.g.
  `LLM-01-C1` (provider resolution), `LLM-01-C2` (compatible → AsyncOpenAI w/ base_url),
  `LLM-01-C3` (azure → AsyncAzureOpenAI), `LLM-01-C4` (`configure_litellm` sets
  `litellm.api_base`), `LLM-01-C5` (validation fails fast). IDs greppable in tests.
- Extend `tests/test_client.py` (monkeypatch `pageindex_mcp.client.settings`):
  resolve_llm_provider matrix; compatible base_url honored; azure branch; configure_litellm
  sets `litellm.api_base`/`api_key`; validate_llm_config raises on bad azure config.
- Extend `tests/test_config.py`: `LLM_PROVIDER` read + default `auto` (reload pattern).
- Add `tests/test_converters_cli*.py` assertion (or extend existing) that the CLI entry
  calls `configure_litellm()` before indexing.

### 5. Docs (consistency sweep)
- `.env.example`: add `LLM_PROVIDER`, uncomment/clarify `OPENAI_BASE_URL` as the
  compatible-endpoint lever; note azure needs `azure/` model prefix on ingestion.
- `README.md`: complete the env table (`OPENAI_BASE_URL`, `LLM_PROVIDER`, `AZURE_API_VERSION`,
  `PAGEINDEX_*`).
- `ARCHITECTURE.md`: add an env-var catalog table; update ADR-005 (provider routing) to
  describe openai/compatible/azure and that ingestion (litellm) is now explicitly configured.
- `PRD.md`/`DESIGN.md`: note compatible endpoint under NFR-DR2 / residency.
- `CLAUDE.md` HR3 already names `OPENAI_BASE_URL` as the routing lever — no change needed.

## Out of scope
- Separate/second credential set (user chose: reuse `OPENAI_API_KEY` + `OPENAI_BASE_URL`).
- Per-model provider routing; changing default models.
- Editing the upstream fork (we configure litellm from our side instead).

## Verification (per-gate, not eval.sh)
```
bash scripts/gates/static.sh        # ruff + format + layer-isolation (no_llm_outside_provider)
bash scripts/gates/unit.sh          # pytest + coverage + assertion density
bash scripts/gates/contracts.sh --built-only   # LLM-01-* greppable in tests
bash scripts/gates/dag.sh           # graph acyclic / node resolution
```
Plus a live smoke against a real OpenAI-compatible base (e.g. a local/OpenRouter `/v1`)
to confirm both ingestion and query hit the custom endpoint.

## Execution (parallel, per requested model routing)
- Wave A (parallel): (1) config+client+converters_cli code [Sonnet], (2) docs sweep [Haiku].
- Wave B: tests + contract YAML [Sonnet], then run gates [Sonnet]; fix failures [Sonnet].
- Final synthesis/verification summary [Opus]. Fable reserved for none unless deep research
  needed (not required here).
