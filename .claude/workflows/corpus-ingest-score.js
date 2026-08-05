export const meta = {
  name: 'corpus-ingest-score',
  description: 'Merged Phase 1+2: Wipe, restart, ingest each doc and score it immediately — incremental review',
  phases: [
    { title: 'Setup', detail: 'Wipe derived stores + restart services' },
    { title: 'Baseline', detail: 'Collect prior audit and doc inventory' },
    { title: 'Ingest + Score', detail: 'Per-doc pipeline: process → fetch meta → judge verdict', model: 'opus' },
    { title: 'Gate G0', detail: 'Verify all docs reached terminal state' },
    { title: 'Diff', detail: 'Compare current vs prior run verdicts' },
    { title: 'Report', detail: 'Write audit scorecard to audit/', model: 'fable' },
  ],
}

// Model fallback chain: failed model → Sonnet (1M context) → Opus (1M context)
// 'sonnet'/'opus' resolve to their 1M-context variants; Haiku has no 1M variant.
// agent() returns null on terminal API errors; retry with next tier.
const MODEL_FALLBACK = {
  'haiku': ['sonnet', 'opus'],
  'sonnet': ['opus'],
  'fable': ['sonnet', 'opus'],
  'opus': ['sonnet'],
}

async function retryAgent(prompt, opts) {
  const result = await agent(prompt, opts)
  if (result !== null) return result

  const fallbacks = MODEL_FALLBACK[opts.model] || ['sonnet', 'opus']
  for (const fallbackModel of fallbacks) {
    if (fallbackModel === opts.model) continue
    log(`${opts.label || 'agent'} failed with model=${opts.model}, retrying with ${fallbackModel}`)
    const retry = await agent(prompt, { ...opts, model: fallbackModel })
    if (retry !== null) return retry
  }
  log(`${opts.label || 'agent'} failed on all fallback models`)
  return null
}

const DISPATCH_CONTEXT_PATH = '.claude/dispatch-context.md'
const PROJECT_DIR = '/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex'
const AUDIT_DIR = 'audit'

// ═══════════════════════════════════════════════════════════════
// Phase 1: Setup — wipe derived state + restart services (once)
// ═══════════════════════════════════════════════════════════════
phase('Setup')
log('Clearing derived processing state...')

const wipeResult = await retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Clear all derived processing state so we can re-ingest from scratch.
The project directory is: ${PROJECT_DIR}

Steps:
1. List current MinIO processed object count for reference
2. Remove all objects under the MinIO processed/ prefix in the pageindex bucket
3. Remove the hash cache file (hashes/processed_hashes.json) from MinIO
4. Flush Redis cache
5. Truncate the PostgreSQL doc_registry table

Report what was cleared with counts. If any step fails, report the error but continue.
`, { label: 'wipe-stores', phase: 'Setup', model: 'haiku' })

log('Wipe complete. Restarting services...')

const restartResult = await retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Restart the pageindex services to pick up clean state.
The project directory is: ${PROJECT_DIR}

Steps:
1. Check if arq worker and MCP server processes are running
2. Stop any existing instances gracefully
3. Start the MCP server (uv run python mcp_server.py) in the project directory
4. Start the arq worker (uv run arq pageindex_mcp.worker.WorkerSettings) in the project directory
5. Verify both are running and the health endpoint responds

Report service status.
`, { label: 'restart-services', phase: 'Setup', model: 'haiku' })

log('Services restarted')

// ═══════════════════════════════════════════════════════════════
// Phase 2: Baseline — collect prior audit + doc inventory
// ═══════════════════════════════════════════════════════════════
phase('Baseline')
log('Collecting doc inventory and prior audit baseline...')

const BASELINE_SCHEMA = {
  type: 'object',
  properties: {
    files: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          filename: { type: 'string' },
          path: { type: 'string' },
        },
        required: ['filename', 'path'],
      },
    },
    total: { type: 'number' },
    prior_audit_file: { type: ['string', 'null'] },
    run_number: { type: 'number' },
    branch: { type: 'string' },
  },
  required: ['files', 'total', 'prior_audit_file', 'run_number', 'branch'],
}

const baseline = await retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Collect the baseline info needed before incremental ingestion.
The project directory is: ${PROJECT_DIR}

Steps:
1. List all supported files in doc_store/ (extensions: .pdf .docx .pptx .md .markdown .txt .html .xlsx .png .jpg .jpeg .tif .tiff)
   Return each as { filename, path } where path is the full path.

2. Find the most recent audit file in audit/:
   ls -t audit/CORPUS_REINGESTION_AUDIT*.md | head -1

3. Determine run number: parse the highest run number from existing audit files and increment.
   If file is named like "CORPUS_REINGESTION_AUDIT_2026-07-27.md" (no run number), treat as Run 4.
   Next run = prior + 1. If no prior files, run = 1.

4. Get current git branch: git branch --show-current

Return structured data.
`, { label: 'baseline', phase: 'Baseline', model: 'haiku', schema: BASELINE_SCHEMA })

if (!baseline || !baseline.files || baseline.files.length === 0) {
  log('ERROR: No supported files found in doc_store/. Aborting.')
  return { error: 'No files in doc_store/' }
}

log(`Found ${baseline.total} docs. Run ${baseline.run_number}. Prior: ${baseline.prior_audit_file || 'none'}. Branch: ${baseline.branch}`)

// ═══════════════════════════════════════════════════════════════
// Phase 3: Ingest + Score — per-doc pipeline (INCREMENTAL)
// ═══════════════════════════════════════════════════════════════
//
// Each document independently flows through:
//   Stage 1 (Haiku):  Process via preprocess_client.py <filename>
//   Stage 2 (Sonnet): Fetch meta.json + tree JSON, extract metrics
//   Stage 3 (Opus):   Subjective judgment → final verdict
//
// pipeline() means doc A can be in Stage 3 while doc B is still
// in Stage 1 — no barrier, no waiting for all docs to finish
// before review starts.
// ═══════════════════════════════════════════════════════════════
phase('Ingest + Score')
log(`Starting incremental ingest+score pipeline for ${baseline.total} documents...`)

const SCORE_SCHEMA = {
  type: 'object',
  properties: {
    doc_id: { type: 'string' },
    filename: { type: 'string' },
    doc_class: { type: 'string' },
    verdict: { type: 'string', enum: ['PASS', 'MARGINAL', 'FAIL', 'ERROR'] },
    node_count: { type: ['number', 'null'] },
    depth: { type: ['number', 'null'] },
    chars: { type: ['number', 'null'] },
    markers: { type: ['number', 'null'] },
    picture_results: { type: ['number', 'null'] },
    garbled_blocks: { type: ['number', 'null'] },
    key_finding: { type: 'string' },
    programmatic_issues: { type: 'array', items: { type: 'string' } },
    subjective_notes: { type: 'string' },
    processing_status: { type: 'string', enum: ['success', 'error', 'timeout', 'oom'] },
    processing_error: { type: ['string', 'null'] },
  },
  required: ['doc_id', 'filename', 'verdict', 'key_finding', 'processing_status'],
}

const INGEST_SCHEMA = {
  type: 'object',
  properties: {
    doc_id: { type: 'string' },
    filename: { type: 'string' },
    status: { type: 'string', enum: ['success', 'error', 'timeout', 'oom'] },
    error: { type: ['string', 'null'] },
    content_class: { type: ['string', 'null'] },
  },
  required: ['doc_id', 'filename', 'status', 'error', 'content_class'],
}

const scores = await pipeline(
  baseline.files,

  // ── Stage 1: PROCESS (Haiku — mechanical subprocess call) ──
  (file) => retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Process a SINGLE document through the ingestion pipeline.
The project directory is: ${PROJECT_DIR}

File to process: ${file.filename}

Run this command and wait for it to complete:
  cd ${PROJECT_DIR} && UV_CACHE_DIR=/tmp/uv-cache uv run python preprocess_client.py "${file.filename}"

This runs in foreground — wait for completion (may take 1-5 minutes per doc).

Report:
- Whether processing succeeded or failed
- The doc_id printed in output (e.g., "[filename] doc_id: abc123")
- Any errors
- The content_class if printed

Return a JSON object: { "doc_id": "...", "filename": "${file.filename}", "status": "success|error|timeout|oom", "error": null or "error message", "content_class": "..." }
`, { label: 'ingest:' + file.filename.substring(0, 30), phase: 'Ingest + Score', model: 'haiku', schema: INGEST_SCHEMA }),

  // ── Stage 2: FETCH METRICS (Sonnet — comparison + extraction) ──
  (ingestResult, file) => {
    if (!ingestResult || ingestResult.status === 'error') {
      return {
        doc_id: 'unknown',
        filename: file.filename,
        doc_class: 'unknown',
        verdict: 'ERROR',
        node_count: null,
        depth: null,
        chars: null,
        markers: null,
        picture_results: null,
        garbled_blocks: null,
        key_finding: 'Processing failed: ' + (ingestResult?.error || 'no output'),
        programmatic_issues: ['processing_failure'],
        subjective_notes: '',
        processing_status: 'error',
        processing_error: ingestResult?.error || 'no output',
      }
    }

    const docId = ingestResult.doc_id || 'unknown'

    return retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Fetch and analyze the processed output for document: ${file.filename}
Doc ID: ${docId}
Processing status: ${ingestResult.status || 'unknown'}
Content class: ${ingestResult.content_class || 'unknown'}

IMPORTANT: The mc CLI is NOT available. Use the helper scripts.

1. Download the meta.json (MANDATORY — this is the ground truth):
   UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/minio_helper.py meta ${docId}

2. Download the tree JSON (first 500 lines):
   UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/minio_helper.py tree ${docId}

3. From meta.json, extract: content_class, node_count, verdict, verdict_reason, chars (content length), any marker counts.

4. From the tree JSON, compute:
   - Actual node count (count objects with "heading" or "content" keys)
   - Max depth
   - Total character count across all content fields: per block, mirror _flat_block_text() (helpers.py:2082-2100) rather than reading block.get('text', '') alone — role="table" blocks carry no "text" key by design (FLAT-05-C1) and store content in row_records instead, so fall back to "\\n".join(row_records) for tables and ocr_text/description for images when "text" is empty. Reading "text" alone undercounts table-heavy documents to near zero.
   - Count of garbled blocks (content containing PUA codepoints U+E000-U+F8FF or sequences of consonant-only Latin in Arabic-script documents)
   - Count of image markers (<!-- image --> or similar)
   - Count of PictureResult enrichments (> [Chart text]: or similar blockquote enrichments)

5. List programmatic issues found (e.g., garbled_text, flat_tree, low_chars, missing_enrichments).

Return the complete scoring object. Set processing_status to "${ingestResult.status || 'success'}".
`, { label: 'fetch:' + file.filename.substring(0, 30), phase: 'Ingest + Score', model: 'sonnet', schema: SCORE_SCHEMA })
  },

  // ── Stage 3: JUDGE VERDICT (Opus — subjective assessment) ──
  (fetchResult, file) => {
    if (!fetchResult || fetchResult.verdict === 'ERROR') {
      return fetchResult
    }

    return retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

You are reviewing the scoring data for document: ${file.filename}
Doc class: ${fetchResult.doc_class || 'unknown'}
Node count: ${fetchResult.node_count}
Depth: ${fetchResult.depth}
Chars: ${fetchResult.chars}
Garbled blocks: ${fetchResult.garbled_blocks || 0}
Markers: ${fetchResult.markers || 0}
Picture results: ${fetchResult.picture_results || 0}
Programmatic issues: ${JSON.stringify(fetchResult.programmatic_issues || [])}
Current verdict from fetch stage: ${fetchResult.verdict}

Apply subjective judgment:
1. Is the tree structure reasonable for this document type? (A legal document should have deep hierarchy; a simple letter can be flat)
2. Is the character count reasonable for the likely page count? (Rule of thumb: ~2000-4000 chars per page)
3. Are there signs of content loss (very low chars for a multi-page doc)?
4. For Arabic docs: is the text in logical order or visually reversed?
5. Do the programmatic issues warrant a verdict downgrade or are they acceptable for this doc type?

Based on programmatic checks AND your subjective assessment, assign final verdict:
- PASS: all checks pass, structure and content look good
- MARGINAL: minor issues but usable (some garble, shallow tree, low coverage)
- FAIL: major problems (content loss, structural collapse, heavy garbling)
- ERROR: processing failed entirely

Update the key_finding with a concise one-line summary capturing the most important observation.
Add subjective_notes explaining your reasoning if you changed the verdict from the fetch stage.

Return the complete scoring object with your final verdict.
`, { label: 'judge:' + file.filename.substring(0, 30), phase: 'Ingest + Score', model: 'opus', schema: SCORE_SCHEMA })
  }
)

const validScores = scores.filter(Boolean)
const processErrors = validScores.filter(s => s.processing_status !== 'success')
log(`Pipeline complete: ${validScores.length}/${baseline.total} docs scored. ${processErrors.length} processing errors.`)

// ═══════════════════════════════════════════════════════════════
// Gate G0: Verify all docs reached terminal state
// ═══════════════════════════════════════════════════════════════
phase('Gate G0')
log('G0: Checking all docs reached terminal state...')

const missingDocs = baseline.files.filter(f =>
  !validScores.some(s => s.filename === f.filename)
)

if (missingDocs.length > 0) {
  log(`G0 WARNING: ${missingDocs.length} docs missing from scores: ${missingDocs.map(d => d.filename).join(', ')}`)
}

const g0Result = await retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Gate G0: Verify all documents reached terminal processing state.
The project directory is: ${PROJECT_DIR}

Expected docs: ${baseline.total}
Scored docs: ${validScores.length}
Processing errors: ${processErrors.length}
Missing from pipeline: ${missingDocs.length}

${missingDocs.length > 0 ? 'Missing docs: ' + JSON.stringify(missingDocs.map(d => d.filename)) : ''}

Steps:
1. Count processed .json files in MinIO (excluding .meta.json):
   UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/minio_helper.py inventory
2. Compare against expected count (${baseline.total})
3. Check for stuck/pending jobs in Redis
4. Identify any documents without a matching processed output

Report:
- File counts (doc_store vs processed)
- Any missing documents
- Any stuck jobs
- GATE VERDICT: PASS or FAIL
`, { label: 'gate-g0', phase: 'Gate G0', model: 'haiku' })

log('G0 check complete')

// ═══════════════════════════════════════════════════════════════
// Phase 5: Diff — compare against prior run
// ═══════════════════════════════════════════════════════════════
phase('Diff')

const DIFF_SCHEMA = {
  type: 'object',
  properties: {
    improvements: { type: 'array', items: { type: 'object', properties: { doc: { type: 'string' }, from: { type: 'string' }, to: { type: 'string' }, reason: { type: 'string' } }, required: ['doc', 'from', 'to', 'reason'] } },
    structural_improvements: { type: 'array', items: { type: 'object', properties: { doc: { type: 'string' }, detail: { type: 'string' } }, required: ['doc', 'detail'] } },
    regressions: { type: 'array', items: { type: 'object', properties: { doc: { type: 'string' }, from: { type: 'string' }, to: { type: 'string' }, reason: { type: 'string' }, hypothesis: { type: 'string' } }, required: ['doc', 'from', 'to', 'reason'] } },
    stalls: { type: 'array', items: { type: 'object', properties: { doc: { type: 'string' }, verdict: { type: 'string' }, reason: { type: 'string' } }, required: ['doc', 'verdict', 'reason'] } },
    stable: { type: 'array', items: { type: 'string' } },
  },
  required: ['improvements', 'regressions', 'stalls', 'stable'],
}

let diffResult = null

if (baseline.prior_audit_file) {
  log(`Diffing Run ${baseline.run_number} vs prior run (${baseline.prior_audit_file})...`)

  diffResult = await retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Compare the current scoring run against the prior run.

Prior audit file: ${baseline.prior_audit_file}
Read this file and parse the Summary Scorecard table to extract per-document verdicts.

Current run scores (Run ${baseline.run_number}):
${JSON.stringify(validScores.map(s => ({ doc: s.filename, verdict: s.verdict, key_finding: s.key_finding, node_count: s.node_count, chars: s.chars })), null, 2)}

For each document, compute the delta:
- Improvement: verdict upgraded (e.g., MARGINAL -> PASS)
- Structural improvement: metrics better but verdict unchanged
- Regression: verdict downgraded (e.g., PASS -> MARGINAL)
- Stall: same bad verdict (FAIL -> FAIL, MARGINAL -> MARGINAL with same issues)
- Stable: same good verdict (PASS -> PASS)

For regressions, include a hypothesis about what caused it.
`, { label: 'diff-runs', phase: 'Diff', model: 'sonnet', schema: DIFF_SCHEMA })
} else {
  log('First run — no prior baseline to diff against.')
  diffResult = { improvements: [], structural_improvements: [], regressions: [], stalls: [], stable: [] }
}

// ═══════════════════════════════════════════════════════════════
// Phase 6: Report — write audit scorecard
// ═══════════════════════════════════════════════════════════════
phase('Report')
log('Writing audit report...')

const tally = { PASS: 0, MARGINAL: 0, FAIL: 0, ERROR: 0 }
validScores.forEach(s => { tally[s.verdict] = (tally[s.verdict] || 0) + 1 })

const reportResult = await retryAgent(`
Read ${DISPATCH_CONTEXT_PATH} and apply its rules.

Write the corpus re-ingestion audit report to: ${AUDIT_DIR}/CORPUS_REINGESTION_AUDIT_RUN-${baseline.run_number}.md

Use this exact format (following the convention in existing audit files):

# Corpus Re-ingestion Audit — Run ${baseline.run_number}

## Environment

- Branch: ${baseline.branch}
- Date: (fill in today's date in YYYY-MM-DD format)
- Prior run: ${baseline.prior_audit_file || 'none (first run)'}
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)

---

## Summary Scorecard

Build a markdown table with columns: #, Document, Doc Class, Verdict, Key Finding

Data (one row per document):
${JSON.stringify(validScores.map((s, i) => ({
  num: i + 1,
  doc: s.filename,
  doc_class: s.doc_class || 'unknown',
  verdict: s.verdict,
  key_finding: s.key_finding,
})), null, 2)}

After the table, add:

**Run ${baseline.run_number} Tally (${validScores.length}/${baseline.total} audited):** ${tally.PASS} PASS, ${tally.MARGINAL} MARGINAL, ${tally.FAIL} FAIL, ${tally.ERROR} ERROR

${baseline.prior_audit_file ? `
Then add the delta section:

---

## Delta from Prior Run -> Run ${baseline.run_number}

Improvements: ${JSON.stringify(diffResult.improvements)}
Structural improvements: ${JSON.stringify(diffResult.structural_improvements || [])}
Regressions: ${JSON.stringify(diffResult.regressions)}
Stalls: ${JSON.stringify(diffResult.stalls)}
Stable: ${JSON.stringify(diffResult.stable)}

Format each category as a bullet list with document name, verdict change, and reason.

If there are regressions, add a "Regressions requiring investigation" table.
` : ''}

MANDATORY PRE-PUBLISH VERIFICATION (RFC-025 D4):
Before writing ANY per-document verdict/char/node figure, re-pull the live processed/{doc_id}.meta.json
from MinIO for that document and compare against the figure about to be written.
If any figure diverges from live MinIO state, re-derive from actual store before writing.

Write the file. Report the file path when done.
`, { label: 'write-report', phase: 'Report', model: 'fable' })

log(`Report written. Tally: ${tally.PASS}P/${tally.MARGINAL}M/${tally.FAIL}F/${tally.ERROR}E`)

return {
  phase: 'ingest-score',
  run_number: baseline.run_number,
  branch: baseline.branch,
  total_docs: baseline.total,
  scores: validScores,
  tally: tally,
  diff: diffResult,
  report_path: `${AUDIT_DIR}/CORPUS_REINGESTION_AUDIT_RUN-${baseline.run_number}.md`,
  has_regressions: diffResult ? diffResult.regressions.length > 0 : false,
  g0: g0Result,
}
