# Stress Test Redesign: Two-Phase MCP Load Testing

## Problem

The existing `test.py` stress test has two critical bugs:
1. **Wrong `Accept` header** — sends only `text/event-stream`, but the MCP streamable-http spec requires both `application/json` and `text/event-stream`. Every request returns 406 without hitting real server logic.
2. **Too aggressive defaults** — ramps to 200 concurrency with no backpressure, crashing the server before safety thresholds can react.

The test was measuring how fast the server rejects malformed requests, not actual MCP performance.

## Design

Replace the single raw-POST ramp with a two-phase approach that tests connection capacity and tool execution separately.

### Phase 1 — Connection Capacity

**Goal**: Find the maximum number of concurrent MCP sessions before degradation.

**Mechanics**:
- Each worker performs a proper MCP handshake: `POST /mcp` with `Accept: application/json, text/event-stream` → parse `initialize` response → capture `Mcp-Session-Id` header + `mcp_affinity` cookie → send `notifications/initialized`
- Ramp from 2 to 50 concurrent sessions (step size 2)
- At each level, measure: session creation latency, success rate, server CPU/memory via `kubectl top node`
- Safety thresholds (stop test): CPU >= 200%, memory >= 90%, error rate >= 20%, P99 >= 10s
- 100ms backpressure delay between requests per worker
- Threshold monitoring every 500ms during both warmup and measurement phases
- 5s cooldown between concurrency steps

**Output**: Recommended max concurrent sessions, latency percentiles, throughput (sessions/sec).

### Phase 2 — Tool Execution Load

**Goal**: At a known-safe concurrency, measure real tool call throughput and find bottlenecks.

**Mechanics**:
- Pre-establish N MCP sessions (N from Phase 1 result, or user-specified via `--concurrency`)
- Each session maintains its own `Mcp-Session-Id` + `mcp_affinity` cookie for sticky routing
- Each worker randomly selects a tool per request from a weighted mix:
  - 20% `recent_documents` — lightweight pagination, no external calls
  - 20% `get_document` (doc_id: `585a254f`) — reads from MinIO/cache
  - 20% `get_page_content` (doc_id: `585a254f`, pages: `"1"`) — single page extraction
  - 20% `get_document_structure` (doc_id: `585a254f`) — hierarchy extraction
  - 20% `find_relevant_documents` — hits GPT models, expensive and slow
- Queries for `find_relevant_documents` rotate through: `["work experience", "education", "technical skills"]`
- Token-cost safeguard: max total `find_relevant_documents` calls (default 50, configurable via `--max-llm-calls`)
- Same safety thresholds and monitoring as Phase 1
- Ramp up request rate within existing sessions (not session count)

**Output**: Per-tool latency breakdown, overall RPS, error rate by tool, which tool is the bottleneck, recommended operating concurrency.

### CLI Interface

```
python3 test.py phase1                      # connection capacity only
python3 test.py phase2 --concurrency 10     # tool load at fixed concurrency
python3 test.py both                        # phase1, then phase2 using phase1's result
python3 test.py both --max-llm-calls 100    # raise GPT call cap
python3 test.py phase1 --dry-run            # show plan without executing
```

Shared flags (same as current):
- `--start`, `--step`, `--max-concurrency` — ramp control
- `--step-duration`, `--warmup` — timing
- `--cpu-limit`, `--mem-limit`, `--error-rate-limit`, `--p99-limit` — safety thresholds
- `--request-delay` — backpressure between requests
- `--dry-run` — show plan only

Phase 2 specific:
- `--concurrency` — fixed session count (overrides Phase 1 result)
- `--max-llm-calls` — cap on `find_relevant_documents` invocations (default 50)
- `--doc-id` — document ID to use for tool calls (default `585a254f`)

### MCP Session Management

Each worker manages its own session lifecycle:
1. `POST /mcp` with initialize request → parse SSE response for result
2. Extract `Mcp-Session-Id` from response headers
3. Extract `mcp_affinity` cookie from `Set-Cookie` header
4. Send `notifications/initialized` with session ID + cookie
5. All subsequent tool calls include both the session ID header and affinity cookie

Sessions that receive errors are re-established automatically. The affinity cookie ensures Traefik routes all requests within a session to the same pod (required for stateful MCP).

### Safety Measures (carried from current fix)

- Metric polling every 1s via `kubectl top node`
- Threshold checks every 500ms during warmup AND measurement (not just between steps)
- 100ms backpressure delay between requests per worker
- `force_close=True` on connection pool to prevent idle connection pileup
- 5s cooldown between concurrency steps
- Graceful Ctrl+C handling with summary output

### Summary Table Format

Phase 1 output: same as current (concurrency, requests, RPS, latency percentiles, CPU, memory).

Phase 2 output adds per-tool breakdown:
```
Tool                    Reqs    Avg     P50     P95     P99   Err%
recent_documents          45  0.012s  0.011s  0.019s  0.029s  0.0%
get_document              42  0.015s  0.013s  0.022s  0.035s  0.0%
get_page_content          38  0.018s  0.015s  0.028s  0.040s  0.0%
get_document_structure    41  0.014s  0.012s  0.020s  0.032s  0.0%
find_relevant_documents   34  2.150s  1.800s  3.500s  5.200s  2.9%
```
