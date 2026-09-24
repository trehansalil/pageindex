<!--
TEMPLATE INSTRUCTIONS (delete this block before committing):

File naming:  agents/rfcs/{{NNN}}-{{kebab-case-slug}}.md
Companions:   agents/designs/design-rfc{{NNN}}-{{slug}}.md
              agents/tasks/tasks-rfc{{NNN}}-{{slug}}.md

Frontmatter contract:
  id           (required)  RFC-NNN, zero-padded 3-digit
  title        (required)  human-readable, no RFC-NNN: prefix
  type         (required)  always "rfc"
  status       (required)  draft | review | accepted | superseded | withdrawn
  date         (required)  ISO 8601 YYYY-MM-DD
  plan-impact  (required)  yes | no
  tags         (required)  must include "rfc" + at least one domain tag
  aliases      (recommended) short names for Obsidian graph linking
  governs      (optional)  [[wikilink]] to design/task files
  supersedes   (optional)  [[RFC-NNN]] of replaced RFCs
  space        (optional)  Confluence space key, for mark sync (--features=frontmatter)
  folders      (optional)  Confluence folder path, for mark sync
  parents      (optional)  Confluence parent page(s), for mark sync

Linking:
  - Cross-references use [[wikilinks]] for Obsidian graph connectivity
  - Acceptance criteria verbs: SHALL (mandatory), SHOULD (recommended), MAY (optional)
  - Prefix decisions D1, D2, etc. for cross-reference from design docs

Required sections (in order):
  Context → Goals → Non-Goals → Glossary → Requirements →
  Decision Summary → Consequences → Traceability

Replace ALL {{placeholders}}. No template tokens may survive into committed artifacts.
-->
---
id: "RFC-{{NNN}}"
title: "{{Title}}"
type: rfc
status: draft
date: "{{YYYY-MM-DD}}"
plan-impact: "{{yes|no}}"
tags:
  - rfc
  - "{{domain-tag}}"
aliases:
  - "RFC-{{NNN}}"
  - "{{Short Alias}}"
governs: []
supersedes: []
# mark confluence-sync fields (flat, not nested — mark reads these with --features=frontmatter)
# space: CITRA
# folders:
#   - RFCs
# parents: []
---

## Context

{{Why this RFC exists. What problem or opportunity prompted it. Link prior RFCs with [[RFC-NNN]] wikilinks.}}

## Goals

- {{Goal}}

## Non-Goals

- {{Non-goal}}

## Glossary

| Term | Definition |
|------|------------|
| {{Term}} | {{Definition. Use Title_Case for component names referenced elsewhere.}} |

## Requirements

### Requirement {{N}}: {{Short Name}}

**User Story:** As a {{role}}, I want to {{action}}, so that {{outcome}}.

#### Acceptance Criteria

1. WHEN {{trigger}}, THE {{Component}} SHALL {{behavior}}.
2. IF {{condition}}, THEN THE {{Component}} SHALL {{behavior}}.
3. WHILE {{state}}, THE {{Component}} SHALL {{constraint}}.

## Decision Summary

{{One paragraph recording the core product/technical decision this RFC captures.}}

## Consequences

- {{Downstream effects, operational implications, and follow-on work.}}

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc{{NNN}}-{{slug}}]] |
| Tasks    | [[tasks-rfc{{NNN}}-{{slug}}]] |
| Supersedes | {{[[RFC-NNN]] or N/A}} |
