# Confluence sync for .agents/{rfcs,designs,tasks}.
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
