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
| GET    | `/health`        | Readiness probe — returns `{"status": "ok"}` |
| POST   | `/convert/pdf`   | Convert PDF → markdown + pictures    |
| POST   | `/convert/image` | Convert image → markdown (OCR)       |

### POST /convert/pdf

```json
{
  "presigned_url": "https://minio.example.com/pageindex/staging/abc123.pdf?...",
  "force_full_page_ocr": false,
  "ocr_lang_override": ["deu", "eng"]
}
```

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
| `DOCLING_SERVICE_BEARER_TOKEN` | (empty) | Bearer token for auth; empty = no auth         |
| `DOWNLOAD_TIMEOUT_S`          | `120`   | Timeout for downloading PDFs from presigned URL |
| `DOCLING_ARTIFACTS_PATH`      | (baked) | Path to pre-downloaded Docling model weights   |
| `TESSDATA_PREFIX`             | (baked) | Path to Tesseract trained data files           |
| `DOCLING_DO_OCR`              | `1`     | Enable OCR in Docling pipeline                 |

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
