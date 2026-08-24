# PageIndex make targets.
#
#   make help          # what you are probably looking for
#   make env           # resolve the local/remote toggles into .env.active
#
# Everything below the "Local / remote toggles" banner reads .env.active, so a
# single `make env` switches the server, the worker, docker compose, and the
# ingestion test script together. See docs/ENV_PROFILES.md.

# ─── Local / remote toggles ──────────────────────────────────────────────────
# Pass on the command line (make env PROFILE=local MINIO=remote), export as
# PI_* in your shell, or persist in env/profile.env.
PROFILE      ?=
APP          ?=
MINIO        ?=
REDIS        ?=
POSTGRES     ?=
DOCLING      ?=
MINIO_ACCESS ?=

# Only forward the ones actually set, so env_profile.sh's own defaults apply.
TOGGLES = $(if $(PROFILE),PI_PROFILE=$(PROFILE)) $(if $(APP),PI_APP=$(APP)) \
          $(if $(MINIO),PI_MINIO=$(MINIO)) $(if $(REDIS),PI_REDIS=$(REDIS)) \
          $(if $(POSTGRES),PI_POSTGRES=$(POSTGRES)) $(if $(DOCLING),PI_DOCLING=$(DOCLING)) \
          $(if $(MINIO_ACCESS),PI_MINIO_ACCESS=$(MINIO_ACCESS))

ACTIVE_ENV := .env.active
PY         := uv run
RUN        := set -a; . ./$(ACTIVE_ENV); set +a;
LOGDIR     := .run

.PHONY: help
help:
	@echo "Environment"
	@echo "  make env-remote      snapshot the k3s infra namespace -> env/remote.env (needs kubectl)"
	@echo "  make env             resolve toggles -> .env.active     [PROFILE=remote|local|hybrid]"
	@echo "  make env-show        show what would be resolved, write nothing"
	@echo ""
	@echo "Run the app (reads .env.active)"
	@echo "  make serve           upload API + MCP server on :8201 (foreground)"
	@echo "  make worker          arq document worker (foreground, separate shell)"
	@echo "  make up / make down  both in the background / stop them"
	@echo ""
	@echo "docker compose (local infra)"
	@echo "  make compose-infra   redis + minio + postgres"
	@echo "  make compose-docling local Docling service (only if PI_DOCLING=local)"
	@echo "  make compose-app     full stack in containers"
	@echo "  make compose-down    stop everything"
	@echo ""
	@echo "Ingestion testing"
	@echo "  make preflight       prove every hop before spending LLM budget"
	@echo "  make ingest          ingest doc_store/ through the running server"
	@echo "  make ingest-dry-run  list what would be submitted"
	@echo "  make ingest-minio    ingest from the MinIO bucket   [PREFIX=some/folder/]"
	@echo ""
	@echo "Toggles: PROFILE APP MINIO REDIS POSTGRES DOCLING MINIO_ACCESS"
	@echo "  e.g. make preflight PROFILE=local     make ingest MINIO=remote DOCLING=remote"

.DEFAULT_GOAL := help

# ─── Environment resolution ──────────────────────────────────────────────────
.PHONY: env env-show env-remote
env-remote:
	./scripts/make_remote_env.sh

env:
	@$(TOGGLES) ./scripts/env_profile.sh

env-show:
	@$(TOGGLES) ./scripts/env_profile.sh --show

# Every runtime target regenerates .env.active first. Deliberately not a file
# rule: with `$(ACTIVE_ENV):` as a prerequisite, `make env` followed by
# `make ingest PROFILE=local` would find the file up to date and silently ignore
# the toggle. Regenerating is cheap, and printing the resolved summary before
# every run means you always see which MinIO you are about to hit.

# ─── Run the app on the host (no Docker) ─────────────────────────────────────
.PHONY: serve worker up down
serve: env
	$(RUN) $(PY) gunicorn -c gunicorn.conf.py pageindex_mcp.server:app

worker: env
	$(RUN) $(PY) arq pageindex_mcp.worker.WorkerSettings

up: env
	@mkdir -p $(LOGDIR)
	@$(RUN) nohup $(PY) gunicorn -c gunicorn.conf.py pageindex_mcp.server:app \
		> $(LOGDIR)/server.log 2>&1 & echo $$! > $(LOGDIR)/server.pid
	@$(RUN) nohup $(PY) arq pageindex_mcp.worker.WorkerSettings \
		> $(LOGDIR)/worker.log 2>&1 & echo $$! > $(LOGDIR)/worker.pid
	@echo "server + worker started; logs in $(LOGDIR)/"

down:
	@for p in server worker; do \
		if [ -f $(LOGDIR)/$$p.pid ]; then \
			pkill -P "$$(cat $(LOGDIR)/$$p.pid)" 2>/dev/null || true; \
			kill "$$(cat $(LOGDIR)/$$p.pid)" 2>/dev/null || true; \
			rm -f $(LOGDIR)/$$p.pid; echo "stopped $$p"; \
		fi; \
	done

# ─── docker compose (the "local" side of the toggles) ────────────────────────
.PHONY: compose-infra compose-docling compose-app compose-down
compose-infra: env
	docker compose --env-file $(ACTIVE_ENV) up -d redis minio minio-setup postgres

# Guarded rather than unconditional: the resolved profile is the source of
# truth, so `make compose-docling` under a remote/hybrid toggle refuses instead
# of silently starting a local Docling the rest of the stack will not use.
# DOCLING=local on the command line is the documented way through.
compose-docling: env
	@. ./$(ACTIVE_ENV); \
	if [ "$$PI_DOCLING" != "local" ]; then \
		echo "refusing: PI_DOCLING=$${PI_DOCLING:-unset}, not 'local'."; \
		echo "  run 'make compose-docling DOCLING=local' to switch the toggle first."; \
		exit 1; \
	fi
	docker compose --env-file $(ACTIVE_ENV) --profile docling-local up -d --build docling-service

compose-app: env
	docker compose --env-file $(ACTIVE_ENV) --profile app up -d --build

compose-down:
	docker compose --profile app --profile docling-local down

# ─── Ingestion testing ───────────────────────────────────────────────────────
INGEST := $(PY) python scripts/remote_ingest_test.py --env-file $(ACTIVE_ENV)
DIR    ?= doc_store
PREFIX ?=

.PHONY: preflight ingest ingest-dry-run ingest-minio
preflight: env
	$(INGEST) --preflight-only

ingest: env
	$(INGEST) --source local --dir $(DIR)

ingest-dry-run: env
	$(INGEST) --source local --dir $(DIR) --dry-run

ingest-minio: env
	$(INGEST) --source minio $(if $(PREFIX),--prefix $(PREFIX))

# ─── Confluence sync for .agents/{rfcs,designs,tasks} ────────────────────────
#
# `make confluence-sync` only does work when a doc file is new or changed:
# the stamp file .agents/.confluence-sync.stamp depends on every rfc/design/
# tasks markdown file, so `make` skips the recipe (and the mark push) when
# nothing changed since the last successful sync.

AGENTS_DIR := .agents
DOC_FILES  := $(wildcard $(AGENTS_DIR)/rfcs/*.md) $(wildcard $(AGENTS_DIR)/designs/*.md) $(wildcard $(AGENTS_DIR)/tasks/*.md) $(wildcard audit/CORPUS_REINGESTION_AUDIT_RUN-*.md)
STAMP      := $(AGENTS_DIR)/.confluence-sync.stamp

.PHONY: confluence-sync confluence-sync-dry-run confluence-scaffold confluence-force-sync confluence-local-sync

# Scaffold + push only the docs that are new/changed since the last sync.
confluence-sync: $(STAMP)

$(STAMP): $(DOC_FILES) scripts/confluence_sync.sh scripts/confluence_scaffold.py
	scripts/confluence_sync.sh
	@touch $(STAMP)

# Show what would be pushed without writing to Confluence.
confluence-sync-dry-run: scripts/confluence_sync.sh scripts/confluence_scaffold.py
	scripts/confluence_sync.sh --dry-run

# Only run the header/companion-file scaffolding, no Confluence push.
confluence-scaffold: scripts/confluence_scaffold.py
	python3 scripts/confluence_scaffold.py

# Push only files with uncommitted local changes (git diff).
confluence-local-sync: scripts/confluence_sync.sh scripts/confluence_scaffold.py
	scripts/confluence_sync.sh --local-diff

# Force a full re-push regardless of the stamp (e.g. after editing mark.toml).
confluence-force-sync: scripts/confluence_sync.sh scripts/confluence_scaffold.py
	scripts/confluence_sync.sh
	@touch $(STAMP)

# ─── Sync .claude skills/workflows to server ────────────────────────────────
SERVER ?= hetzner_server
REMOTE_CLAUDE_DIR ?= pageindex_deployment/.claude/

.PHONY: sync-claude
sync-claude:
	rsync -avz .claude/ $(SERVER):$(REMOTE_CLAUDE_DIR)
