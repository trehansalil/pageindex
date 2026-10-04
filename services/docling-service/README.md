# Docling Conversion Service

Vendor-neutral HTTP microservice that offloads heavy PDF/image conversion
(Docling RT-DETRv2 + TableFormer, ~1.9 GB RSS) from the main PageIndex worker
into a separately deployable container.

## How It Works

1. The PageIndex worker generates a **presigned MinIO URL** for the staged PDF.
2. Worker POSTs the URL to this service's `/convert/pdf` endpoint.
3. This service downloads the PDF, runs Docling conversion + picture OCR, and
   returns markdown + picture results as JSON.
4. Worker continues with tree building, validation, and MinIO save locally.

## Endpoints

| Method | Path             | Description                          |
|--------|------------------|--------------------------------------|
| GET    | `/health`        | Readiness probe — returns `{"status": "ok", "in_flight": 0}` (see below) |
| POST   | `/convert/pdf`   | Convert PDF → markdown + pictures    |
| POST   | `/convert/image` | Convert image → markdown (OCR)       |
| GET    | `/capacity`      | What this backend can take on now (bearer-authed, RFC-052 R5) |

### GET /health

```json
{"status": "ok", "in_flight": 0}
```

`in_flight` is the number of `/convert/*` requests currently inside the
service: downloading, queued for a conversion slot, or converting. Other
routes (`/health`, `/version`) are not counted. The Mac auto-updater
(`macos/update.sh`) restarts the service only when this is `0`; a response
without the field is treated as an older build and the restart is deferred.

### GET /capacity

Bearer-authed like `/convert/*`. Host resources, slots, timing and the
build only; nothing about any document (HR3).

```json
{"backend":"mac","build_sha":"…","effective_cpus":12.0,"total_mem_bytes":68719476736,
 "free_mem_bytes":41000000000,"reserve_bytes":4294967296,"chunk_pages":10,
 "per_proc_peak_bytes":1543503872,"safe_procs":12,"busy_slots":0,"max_slots":1,
 "slice_slots":12,"busy_slice_slots":0,"spp_ewma":19.2,"spp_samples":27}
```

- `slice_slots` / `busy_slice_slots` (RFC-052 A-P5-5): split chunks (page ranges of
  <= 20 pages) this service runs at once, each one process on
  `cpus // slice_slots` threads, and how many it is running. While any chunk
  runs, the chunks hold one `busy_slots` slot as a group, so a whole-document
  conversion waits for them and vice versa.

- `effective_cpus`: the cgroup CPU quota (Linux), P-cores + 0.5 × E-cores (macOS).
- `total_mem_bytes`: the same total the planner sizes from.
- `free_mem_bytes`: `min(cgroup memory.max − memory.current, MemAvailable)` on
  Linux; `vm_stat` free + inactive + speculative on macOS. `null` if unreadable,
  and `safe_procs` is then `0`.
- `safe_procs = min(floor(effective_cpus), floor((free − reserve) / per_proc_peak))`,
  `per_proc_peak = WORKER_BASE_BYTES + chunk_pages × PER_PAGE_BYTES`. It can be `0`.
- `spp_ewma`: seconds per page per process over recent `ok` chunks (from the
  `docling_chunk` records, so it stays at the prior with `PAGEINDEX_LOG_DECISIONS=off`);
  `DOCLING_SPP_PRIOR` while `spp_samples` is `0`.
- `build_sha`: `BUILD_SHA`, the same value as `/version`'s `commit_sha`.

Each conversion also clamps its own process count by `safe_procs` for its real
chunk size, planned once it holds its conversion slot (RFC-052 R5 AC2).

### POST /convert/pdf

```json
{
  "presigned_url": "https://minio.example.com/pageindex/staging/abc123.pdf?...",
  "force_full_page_ocr": false,
  "ocr_lang_override": ["deu", "eng"]
}
```

Optional `page_start` / `page_end` (RFC-052 R5 AC3): convert only those pages,
0-based and **inclusive** (the `docling_chunk` convention). Both or neither;
`0 <= page_start <= page_end < page count`, else 422. `page_classes` and
`pages_with_tables` stay document-level and are rebased to the slice here.
The response's picture `page` numbers are the slice's own, so a caller merging
shards adds `page_start`; `applied.page_range` echoes the range. Without a
range the request takes the whole-document path unchanged.

Response:

```json
{
  "markdown": "# Document Title\n...",
  "picture_results": [
    {
      "ocr_text": "...",
      "png_bytes": "<base64>",
      "page": 1,
      "bbox": {"l": 0, "t": 0, "r": 100, "b": 100},
      "description": "",
      "skipped_reason": "",
      "decorative": false
    }
  ]
}
```

### POST /convert/image

```json
{
  "presigned_url": "https://minio.example.com/pageindex/staging/abc123.png?...",
  "ocr_lang_override": ["deu", "eng"]
}
```

Response:

```json
{
  "markdown": "OCR text from the image..."
}
```

## Build

```bash
docker build -f services/docling-service/Dockerfile -t docling-service .
```

The build context is the **repository root** (not this directory) because the
service imports from the `pageindex_mcp` package.

## Run

```bash
docker run -p 8080:8080 \
  -e DOCLING_SERVICE_BEARER_TOKEN=changeme \
  docling-service
```

## Environment Variables

| Variable                       | Default | Description                                    |
|--------------------------------|---------|------------------------------------------------|
| `DOCLING_SERVICE_BEARER_TOKEN` | (empty) | Bearer token for auth; **required** — the service refuses to start without it |
| `DOCLING_SERVICE_ALLOW_ANONYMOUS` | (empty) | `1` allows an empty token (local dev container only, never an exposed pod) |
| `DOCLING_MAX_CONCURRENT`      | `1`     | Conversions run at once; others queue (each peaks ~2 GB RSS). `/capacity` `max_slots` |
| `DOCLING_RESERVE_BYTES`       | `805306368` (768 MiB) | Memory `safe_procs` leaves free, in the planner clamp and `/capacity` |
| `DOCLING_CHUNK_PAGES`         | `10`    | Chunk size `/capacity` quotes `safe_procs` for (each conversion clamps for its own) |
| `DOCLING_SLICE_SLOTS`         | (computed) | Split chunks run at once (`/capacity` `slice_slots`); default `max(1, min(cpus, floor((total memory − reserve) / per-process peak for 20 pages)))`; `/capacity` further clamps it to what free memory fits, never below 1 (when free memory is unreadable it reports the configured value unclamped, with `safe_procs` 0, so the coordinator sends this backend no split work until it can read it again) |
| `DOCLING_SPP_PRIOR`           | `40`    | `spp_ewma` before any chunk has finished (design: Mac 19, cpx62 40) |
| `DOCLING_BACKEND_NAME`        | hostname | `/capacity` `backend`, and the `docling_chunk` / Loki `host` label |
| `DOWNLOAD_TIMEOUT_S`          | `120`   | Timeout for downloading PDFs from presigned URL |
| `DOCLING_ARTIFACTS_PATH`      | (baked) | Path to pre-downloaded Docling model weights   |
| `TESSDATA_PREFIX`             | (baked) | Path to Tesseract trained data files           |
| `DOCLING_DO_OCR`              | `0`     | OCR policy: `0`/unset = page-class driven (forced on when a request has no usable page classes -- absent, kill switch, page-count mismatch, or parse failure); `1`/`true`/`yes` = force on; anything else (e.g. `off`) = force off |

## Worker Configuration

Set these on the **PageIndex worker** to enable remote conversion:

| Variable                       | Example                           | Description                         |
|--------------------------------|-----------------------------------|-------------------------------------|
| `DOCLING_SERVICE_URL`          | `http://docling-service:8080`     | Base URL of this service            |
| `DOCLING_SERVICE_TIMEOUT_S`    | `600`                             | HTTP timeout for conversion calls   |
| `DOCLING_SERVICE_BEARER_TOKEN` | `changeme`                        | Must match the service's token      |
| `MINIO_PRESIGN_ENDPOINT`      | `minio.example.com`               | Publicly-reachable MinIO endpoint   |

When `DOCLING_SERVICE_URL` is unset, the worker uses the local Docling path
(same behavior as before this service existed).

## Data Residency

PDF bytes transit from MinIO to this service over the network. For PII-bearing
corpora, deploy this service in an EU region with appropriate data residency
guarantees. Presigned URLs expire after 15 minutes.

## AGPL-3.0

This service uses PyMuPDF (fitz) for picture cropping, which is AGPL-3.0.
Serving it over a network is a legal decision to clear — see the project's
`CLAUDE.md` for details.

## Resource Requirements

- **Memory**: ~2 GB peak RSS (Docling models + inference)
- **CPU**: Benefits from multi-core for TableFormer; single worker recommended
- **Disk**: ~1.5 GB for model weights + tessdata (baked into image)
- **Cold start**: ~15-30s to load models on first request (warmed at startup)
