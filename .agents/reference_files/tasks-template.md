# Implementation Plan: {{PROJECT_NAME}}

<!--
TEMPLATE INSTRUCTIONS (delete this block before use):
- Replace all {{PLACEHOLDER}} tokens with project-specific content.
- This file is the companion to design-{{PROJECT_SLUG}}.md — every task must trace
  back to a requirement or design property.
- Link to the governing RFC(s) — every task exists because an RFC decision requires it.
- Tasks marked with `*` are property-based tests (optional for faster MVP).
- Checkpoints are mandatory gates — never skip them.
- The Task Dependency Graph at the bottom drives parallel execution ordering.
- Keep task granularity at ~1-4 hours of implementation work.
-->

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC(s) | `rfcs/{{RFC_ID}}.md` |
| Design Document | `reference_files/design-{{PROJECT_SLUG}}.md` |
| PRD / Requirements | `{{PRD_PATH}}` |

## Overview

{{One-paragraph summary: what is being built, the technology stack, and the implementation strategy (e.g., "proceeds from shared infrastructure through core services, integrating incrementally with property-based tests validating N correctness properties").}}

## Tasks

<!--
STRUCTURE RULES:
1. Top-level items are numbered phases (1, 2, 3, ...).
2. Sub-items are numbered tasks (1.1, 1.2, ...).
3. Each task has:
   - A clear action title (verb-first: "Implement", "Create", "Write", "Wire")
   - Bulleted sub-steps describing WHAT to build (not HOW)
   - A _Requirements: X.Y_ line linking to the design/PRD requirements
   - [For test tasks] Property number + name + validates line
4. Property-based test tasks are marked with `*` (optional for MVP).
5. Checkpoint tasks validate a phase before proceeding.
6. NOTE: lines call out cross-service dependencies and deferred integration points.

TASK CATEGORIES:
- Infrastructure / scaffolding tasks (shared libs, config, CI)
- Data model + migration tasks
- API endpoint implementation tasks
- Business logic / algorithm tasks
- Event producer / consumer wiring tasks
- Property-based test tasks (marked *)
- Integration / E2E test tasks
- Checkpoint tasks (run tests, validate migrations)
-->

- [ ] 1. {{Phase name}}

  - [ ] 1.1 {{Task title}}

    - {{What to build — bullet per deliverable}}
    - _Requirements: {{requirement IDs from design doc}}_
  - [ ] 1.2 {{Task title}}

    - {{What to build}}
    - _Requirements: {{requirement IDs}}_

- [ ] 2. {{Phase name}}

  - [ ] 2.1 {{Data model / migration task}}

    - Create {{models}} in {{DB name}}
    - Create {{migration framework}} migration for {{DB}} schema
    - _Requirements: {{requirement IDs}}_
  - [ ] 2.2 {{API endpoint task}}

    - `{{METHOD}} {{PATH}}` — {{behavior description}}
    - _Requirements: {{requirement IDs}}_
  - [ ]* 2.3 {{Property test task}}

    - **Property {{N}}: {{Property title from design doc}}**
    - **Validates: Requirements {{requirement IDs}}**
  - [ ] 2.4 {{Event wiring task}}

    - Subscribe to `{{event_name}}` event from {{producer service}}
    - NOTE: Test with in-memory event fixtures until {{producer task ID}} is complete; integration-validate at Checkpoint {{N}}
    - _Requirements: {{requirement IDs}}_

- [ ] 3. Checkpoint — {{Phase name}}

  - Run `{{test command}}` and verify all property tests (Properties {{N, N, N}}) pass
  - Run `{{migration command}}` to validate migrations
  - Ask the user if questions arise before proceeding.

<!--
REPEAT the pattern above for each service / phase:
1. Data model + migrations
2. Core endpoint implementation
3. Property tests (marked *)
4. Event subscriptions / background jobs
5. Checkpoint

GROUP related services in the same phase when they have no cross-dependencies.
SPLIT a service across phases when later features depend on other services being built first.
-->

- [ ] {{N}}. {{Final integration phase}}

  - [ ] {{N}}.1 {{Cross-service wiring task}}

    - Wire event flows end-to-end:
      - `{{event}}` → {{consumer}} ({{purpose}})
    - _Requirements: {{cross-cutting requirement IDs}}_
  - [ ] {{N}}.2 {{Batch / periodic job registration}}

    - Register ALL periodic tasks in centralized schedule:
      - {{job_name}} (every {{interval}})
    - _Requirements: {{requirement IDs}}_

- [ ] {{N+1}}. Final checkpoint

  - Run `{{full test suite command}}`
  - Run all critical user journey integration tests from Task {{N}}.{{M}}
  - Verify zero flaky test failures across 3 consecutive runs
  - Ask the user if questions arise before proceeding.

## Notes

<!--
Capture non-obvious implementation decisions, constraints, and conventions.
These are facts that a developer starting mid-project needs to know.
-->

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each major service/phase
- Property tests validate the {{N}} universal correctness properties defined in the design
- {{Technology / framework conventions}}
- {{Cross-service communication patterns}}
- {{Compliance / regulatory notes}}

## Task Dependency Graph

<!--
Encodes parallelism: tasks in the same wave have no mutual dependencies
and can execute concurrently. Tasks in wave N+1 depend on wave N completing.

Rules:
- Wave 0 = shared infrastructure (no dependencies)
- Data model tasks can parallelize across services (same wave)
- API tasks depend on their service's data model task
- Property test tasks depend on the feature they test
- Checkpoint tasks depend on all tasks in their service
- Integration tasks depend on all services being built
- Final checkpoint depends on everything
-->

```json
{
  "waves": [
    { "id": 0, "tasks": [{{shared infra task IDs}}] },
    { "id": 1, "tasks": [{{data model task IDs — parallelized across services}}] },
    { "id": 2, "tasks": [{{core API task IDs}}] },
    { "id": 3, "tasks": [{{business logic + property test task IDs}}] },
    { "id": {{N}}, "tasks": [{{integration + final test task IDs}}] }
  ]
}
```
