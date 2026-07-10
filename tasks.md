# Implementation Plan: Chalo MVP

## Overview

This plan implements the Chalo verified travel-buddy matching platform as 8 Python/FastAPI microservices with per-service PostgreSQL databases, Redis 7 for cache/pub-sub/event bus, Celery for batch jobs, and a React Native (Expo) mobile app. Implementation proceeds from shared infrastructure through core services, integrating incrementally with property-based tests validating 57 correctness properties.

## Tasks

- [ ] 1. Shared infrastructure and project scaffolding

  - [ ] 1.1 Set up monorepo structure with per-service packages

    - Create directory structure: `services/{auth,match,trip_room,booking,payment,safety,notification,credibility}/`
    - Add shared library at `libs/shared/` for common models, event bus client, outbox pattern, circuit breaker
    - Configure `pyproject.toml` with workspace dependencies (FastAPI, SQLAlchemy, Pydantic, Redis, Celery, Hypothesis)
    - Set up per-service `alembic/` migration directories
    - _Requirements: All services_
  - [ ] 1.2 Implement shared event bus client (Redis Streams + outbox pattern)

    - Create `libs/shared/event_bus.py` with publish/subscribe abstractions over Redis Streams
    - Implement transactional outbox table model (`OutboxEvent`: id, event_type, payload, published_at)
    - Implement background outbox poller that reads unpublished events and publishes to Redis Streams
    - Implement `ProcessedEvent` table for idempotent consumer deduplication (event_id + consumer_name unique constraint)
    - Implement dead-letter stream (DLQ) for events failing after 3 retries
    - Define settlement guard interface in `libs/shared/settlement_guard.py`: abstract `SettlementGuard` protocol with `assert_zero_balance_or_block(traveler_id, room_id)` signature + `SettlementRequiredError` exception. Concrete implementation wired after Task 10.2 provides the balance endpoint.
    - _Requirements: All cross-service communication_
  - [ ] 1.3 Implement shared circuit breaker utility

    - Create `libs/shared/circuit_breaker.py` with configurable failure threshold, recovery time, fallback
    - Support per-service configuration (KYC: 5/60s/30s, Payment: 3/30s/60s, SMS: 5/60s/30s, Firebase: 10/60s/120s)
    - _Requirements: Error Handling_
  - [ ] 1.4 Set up database migration framework and base models

    - Create SQLAlchemy base with common fields (id UUID, created_at, updated_at)
    - Configure Alembic for each service's database
    - Create shared Pydantic base schemas for API responses and error formats
    - _Requirements: All services_
  - [ ] 1.5 Set up testing infrastructure

    - Configure Hypothesis with min 100 iterations, 500ms deadline
    - Create test fixtures for per-service transactional rollback on PostgreSQL
    - Create shared test utilities (factories, fake data generators)
    - _Requirements: Testing Strategy_
  - [ ] 1.6 Implement observability and operational readiness

    - Create per-service `/health` (liveness) and `/ready` (readiness) endpoints checking DB connection pool and Redis connectivity
    - Configure SQLAlchemy async connection pool with per-service limits (default: pool_size=5, max_overflow=10, pool_pre_ping=True)
    - Implement structured JSON logging with correlation ID (X-Request-ID) propagation across services
    - Add Prometheus metrics export endpoint (`/metrics`) with: request latency histograms, active connections gauge, event bus consumer lag gauge
    - Create shared OpenTelemetry trace context propagation for cross-service API calls
    - _Requirements: All services (operational)_
- [ ] 2. Auth & Verification Service

  - [ ] 2.1 Implement Traveler data model and migrations

    - Create `Traveler`, `VerificationStatus`, `EmergencyContact`, `ConsentRecord`, `DataErasureRequest`, `DataAuditLog`, `DataPrincipalGrievance` models in AuthDB
    - Implement age derivation from date_of_birth
    - Implement application-level encryption for sensitive columns (phone_number, aadhaar_number, pan_number) using AES-256-GCM with per-tenant key envelope via AWS KMS; store only encrypted values in AuthDB; decrypt on read with key cache (5-min TTL)
    - Create Alembic migration for AuthDB schema
    - _Requirements: 1.1–1.12_
  - [ ] 2.2 Implement OTP send and verify endpoints

    - `POST /api/v1/auth/otp/send` — validate phone (E.164 +91), call SMS gateway, store OTP with 60s TTL in Redis
    - `POST /api/v1/auth/otp/verify` — validate OTP within 60s, transition status to `otp_verified`
    - Implement SMS gateway integration with circuit breaker and fallback provider
    - Implement IP-based rate limiting on `/auth/otp/send`: max 3 requests per phone per 10 minutes, max 10 requests per IP per minute (to prevent SMS pumping)
    - Add phone-number cooldown: after 2 failed OTP verifications for same number, require 15-minute wait before next send
    - _Requirements: 1.1_
  - [ ] 2.3 Implement JWT token issuance and refresh

    - Issue JWT access token (15-min expiry) on successful OTP verification or KYC completion
    - Issue opaque refresh token (30-day expiry) stored in Redis with user binding
    - `POST /api/v1/auth/refresh` — rotate refresh token, return new access token
    - `POST /api/v1/auth/logout` — blacklist refresh token in Redis
    - Implement token blacklist check on suspension/ban (Safety Service publishes `user.suspended` event; Auth Service subscribes and invalidates all active tokens)
    - _Requirements: All authenticated endpoints_
  - [ ] 2.4 Implement under-18 age gate and liveness check endpoints

    - `POST /api/v1/auth/liveness` — validate age >= 18 before accepting selfie; reject minors before biometric collection
    - Enforce 3-attempt max for liveness detection (including initial attempt)
    - Return descriptive error messages on failure with remaining attempts count
    - _Requirements: 1.2, 1.3, 1.4, 1.12_
  - [ ]* 2.5 Write property test for under-18 age gate (Property 55)

    - **Property 55: Under-18 age-gate enforcement**
    - **Validates: Requirements 1.12**
  - [ ]* 2.6 Write property test for liveness attempt limit (Property 2)

    - **Property 2: Liveness attempt limit enforcement**
    - **Validates: Requirements 1.4**
  - [ ] 2.7 Implement KYC upload and cross-verification endpoints

    - `POST /api/v1/auth/kyc/upload` — accept Aadhaar/PAN/DL document, store in S3 PII Store
    - `POST /api/v1/auth/kyc/verify` — submit selfie + document to KYC Vendor, enforce max 1 retry on mismatch (increment kyc_retry_count before checking limit)
    - Extract gender from government ID document (not self-reported)
    - On success: transition to "verified", publish `user.verified` event to Redis Streams
    - _Requirements: 1.5, 1.6, 1.7, 1.8, 1.9_
  - [ ]* 2.8 Write property test for New Traveler badge on verification (Property 3 — publish side)

    - **Property 3 (publish): `user.verified` event published on KYC success**
    - **Validates: Requirements 1.10**
  - [ ] 2.9 Implement DPDP consent and erasure endpoints

    - `POST /api/v1/auth/consent` — record consent with data_category, purpose, version, retention_policy_days
    - `GET /api/v1/auth/consent` — retrieve consent records for data principal
    - `POST /api/v1/auth/erasure-request` — submit erasure request; reject if active booking exists
    - Implement erasure logic: full deletion for biometric/identity data, anonymization for financial records (retain 8 years per Companies Act 2013 §128)
    - _Requirements: 1.2, 1.5, 1.6 (DPDP compliance)_
  - [ ]* 2.10 Write property test for DPDP consent before collection (Property 48)

    - **Property 48: DPDP consent required before data collection**
    - **Validates: Requirements 1.2, 1.5, 1.6**
  - [ ]* 2.11 Write property test for DPDP erasure correctness (Property 53)

    - **Property 53: DPDP erasure — anonymize financial records, delete biometric data**
    - **Validates: Requirements 1.2, 1.5, 1.6**
  - [ ] 2.12 Implement profile endpoints and grievance mechanism

    - `GET /api/v1/auth/profile` — return current user profile
    - `PUT /api/v1/auth/profile` — update display name, home city, interests
    - `GET /api/v1/auth/profiles?ids=...` — batch fetch public profiles (max 10), enforcing pseudonymity (no PII fields exposed)
    - `POST /api/v1/privacy/grievances` — submit DPDP §13 grievance
    - `GET /api/v1/privacy/grievances/{id}` — get grievance status
    - Implement Celery job to flag overdue grievances (>30 days) for ops alert
    - _Requirements: 1.9, 7.13_
  - [ ]* 2.13 Write property test for pseudonymity in API responses (Property 36)

    - **Property 36: Pseudonymity in API responses**
    - **Validates: Requirements 7.13**
  - [ ] 2.14 Implement Auth Service event subscriptions

    - Subscribe to `xp.updated` event from Credibility Service
    - Subscribe to `user.suspended` event from Safety Service — on receipt, invalidate all active refresh tokens for traveler_id and add access token JTI to Redis blacklist (15-min TTL matching access token expiry)
    - NOTE: Test with in-memory event fixtures until Credibility Service producer (Task 17.2) and Safety Service producer (Task 15.4) are complete; integration-validate at Checkpoint 20
    - Update denormalized fields on Traveler: credibility_xp, average_rating, trips_completed
    - Implement idempotent consumer using ProcessedEvent table
    - _Requirements: 9.1, 9.7_
- [ ] 3. Checkpoint — Auth Service

  - Run `pytest services/auth/tests/ -x --tb=short` and verify all property tests (Properties 2, 3-publish, 36, 48, 53, 55) pass
  - Run `alembic -c services/auth/alembic.ini upgrade head` to validate migrations
  - Ask the user if questions arise before proceeding.
- [ ] 4. Match Engine Service — Trip Requests

  - [ ] 4.1 Implement Trip Request data models and migrations

    - Create `TripRequest`, `TripRequestStatus`, `MatchCandidate`, `MatchCandidateMember`, `MemberResponse`, `MatchStatus`, `TripLeaderPosting`, `PostingStatus`, `Corridor`, `DestinationWaitlist` models in MatchDB
    - Create Alembic migration for MatchDB schema
    - _Requirements: 2.1–2.9, 3.1–3.10, 6.1–6.8_
  - [ ] 4.2 Implement Trip Request CRUD endpoints

    - `POST /api/v1/trips/requests` — validate all required fields, enforce 3-day lead time, enforce max 2 active requests per Traveler, verify traveler is verified (call Auth Service with circuit breaker)
    - `GET /api/v1/trips/requests` — list active requests for current Traveler
    - `PUT /api/v1/trips/requests/{id}` — allow edits to interest_tags and bio_text only while status is "seeking_match"; reject destination/dates/budget changes
    - `DELETE /api/v1/trips/requests/{id}` — cancel Trip Request
    - Store valid requests with status "seeking_match"
    - _Requirements: 2.1–2.8_
  - [ ]* 4.3 Write property tests for Trip Request validation (Properties 4, 5, 6, 7, 8)

    - **Property 4: Trip Request validation correctness**
    - **Property 5: Trip Request date validation enforces minimum lead time**
    - **Property 6: Valid Trip Request stored with seeking_match status**
    - **Property 7: Active Trip Request limit per Traveler**
    - **Property 8: Trip Request field editability while seeking_match**
    - **Validates: Requirements 2.1–2.8**
  - [ ] 4.4 Implement Trip Request auto-expiry

    - Create Celery periodic task to expire Trip Requests whose start_date has passed
    - Transition status from "seeking_match" to "expired"
    - Publish `trip_request.expired` event for notification delivery
    - _Requirements: 2.9_
  - [ ]* 4.5 Write property test for Trip Request auto-expiry (Property 9)

    - **Property 9: Trip Request auto-expiry on start date**
    - **Validates: Requirements 2.9**
  - [ ] 4.6 Implement unverified traveler exclusion from matching

    - Before match evaluation, verify traveler's verification_status == "verified" via Auth Service (with cached fallback)
    - Exclude non-verified travelers from all match candidate results
    - _Requirements: 1.11_
  - [ ]* 4.7 Write property test for unverified exclusion (Property 1)

    - **Property 1: Unverified travelers excluded from match results**
    - **Validates: Requirements 1.11**
- [ ] 5. Match Engine Service — Algorithmic Matching

  - [ ] 5.1 Implement matching algorithm and compatibility score

    - Implement match candidate generation: same destination, ≥2 days date overlap, overlapping budget ranges, ≥1 shared interest tag
    - Compute compatibility_score as normalized weighted sum: 0.4 × date_overlap_ratio + 0.3 × budget_proximity + 0.3 × shared_interest_ratio
    - Implement Celery periodic sweep for match computation: bucket by (destination, date_range_week) before pairwise comparison to avoid O(n²); add composite index on (destination, status, start_date) in MatchDB; batch size limit of 100 requests per sweep iteration
    - Register match_computation_sweep in `celerybeat_schedule.py` with interval: every 2 minutes
    - _Requirements: 3.1, 3.2, 3.3_
  - [ ]* 5.2 Write property tests for match criteria and score (Properties 10, 11)

    - **Property 10: Match criteria correctness**
    - **Property 11: Compatibility score is a normalized weighted sum in [0, 1]**
    - **Validates: Requirements 3.2, 3.3**
  - [ ] 5.3 Implement match candidate lifecycle

    - Create match candidate with 48-hour expiry, notify all involved Travelers
    - `GET /api/v1/matches/candidates` — list pending match candidates
    - `POST /api/v1/matches/candidates/{id}/accept` — accept match
    - `POST /api/v1/matches/candidates/{id}/decline` — decline match
    - Enforce at most one pending candidate per Trip Request
    - On all-accept: publish `match.confirmed` event, create Trip Room
    - On any-decline or expiry: mark as declined, continue matching
    - Implement 30-day cooldown for declined pairs
    - Implement 5-declines-in-7-days nudge notification trigger
    - _Requirements: 3.4–3.10_
  - [ ]* 5.4 Write property tests for match lifecycle (Properties 12, 13, 14, 15, 16, 57)

    - **Property 12: Match notification payload completeness**
    - **Property 13: All-accept creates N-party Trip Room**
    - **Property 14: Any decline or expiry marks candidate as declined**
    - **Property 15: At most one pending match candidate per Trip Request**
    - **Property 16: Declined pair cooldown period**
    - **Property 57: 5-declines-in-7-days triggers nudge notification**
    - **Validates: Requirements 3.4–3.10**
- [ ] 6. Match Engine Service — Trip Leader & Corridor

  - [ ] 6.1 Implement Trip Leader posting endpoints

    - `POST /api/v1/trips/leader-postings` — validate leader has ≥2 completed trips, validate posting fields (destination, dates, budget, tags, description, max_group_size 2–6, payment_window_hours 24–72)
    - `GET /api/v1/trips/leader-postings` — browse joinable postings (visible if current_members < max and start_date > 3 days)
    - `POST /api/v1/trips/leader-postings/{id}/join` — request to join
    - `POST /api/v1/trips/leader-postings/{id}/approve/{traveler_id}` — approve join (add to Trip Room BEFORE notifying members)
    - `POST /api/v1/trips/leader-postings/{id}/decline/{traveler_id}` — decline (generic message, no reason revealed)
    - _Requirements: 6.1–6.8_
  - [ ]* 6.2 Write property tests for Trip Leader (Properties 29, 30, 52)

    - **Property 29: Trip Leader eligibility requires 2+ completed trips**
    - **Property 30: Trip Leader posting visibility rules**
    - **Property 52: Trip Leader join approval ordering guarantee**
    - **Validates: Requirements 6.1, 6.6, 6.4**
  - [ ] 6.3 Implement corridor and density endpoints

    - `GET /api/v1/corridors/destinations` — return curated Manali-Kasol destination list
    - `POST /api/v1/corridors/waitlist` — join waitlist for unsupported destination
    - `GET /api/v1/corridors/density` — return encouragement message if <20 active requests, actual count if ≥20
    - Validate destination against corridor list on Trip Request creation; reject unsupported destinations with waitlist offer
    - _Requirements: 10.1–10.5_
  - [ ]* 6.4 Write property tests for corridor and density (Properties 42, 43)

    - **Property 42: Destination support categorization**
    - **Property 43: Density display threshold**
    - **Validates: Requirements 10.2, 10.4, 10.5**
- [ ] 7. Checkpoint — Match Engine Service

  - Run `pytest services/match/tests/ -x --tb=short` and verify all property tests (Properties 1, 4–16, 29, 30, 42, 43, 52, 57) pass
  - Run `alembic -c services/match/alembic.ini upgrade head` to validate migrations
  - Ask the user if questions arise before proceeding.
- [ ] 8. Trip Room Service — Core

  - [ ] 8.1 Implement Trip Room data models and migrations

    - Create `TripRoom`, `TripRoomPhase`, `TripRoomMember`, `MemberRole`, `RemovalReason`, `Message`, `MessagePIIAudit`, `ItineraryItem`, `Expense`, `Settlement` models in TripRoomDB
    - Create Alembic migration for TripRoomDB schema
    - _Requirements: 4.1–4.9_
  - [ ] 8.2 Implement Trip Room lifecycle and membership

    - Subscribe to `match.confirmed` event — create Trip Room with all matched Travelers as members within 5 seconds
    - Define maximum event processing latency: 5s target, 3 retries with exponential backoff (1s, 2s, 4s), dead-letter on failure, ops alert if p99 > 5s
    - NOTE: Test with in-memory event fixtures until Match Engine producer (Task 5.3) is complete; integration-validate at Checkpoint 20
    - `GET /api/v1/rooms/{room_id}` — return room details, phase, member list
    - Implement room phase transitions: pre_booking → booked → in_trip → post_trip → closed
    - Implement access window: room accessible from creation until end_date + 7 days, then transition to "closed"
    - `POST /api/v1/rooms/{room_id}/leave` — invoke shared `assert_zero_balance_or_block` guard across ALL member pairs before allowing departure; revoke access on leave
    - NOTE: Settlement guard uses a stub/mock until Task 10.2 implements the balance endpoint; integration-validate settlement behavior at Checkpoint 11
    - _Requirements: 4.1, 4.6, 4.7, 4.8_
  - [ ]* 8.3 Write property test for Trip Room access window (Property 18)

    - **Property 18: Trip Room access window**
    - **Validates: Requirements 4.6**
  - [ ]* 8.4 Write property test for settlement enforcement on all removal paths (Property 19)

    - **Property 19: Settlement enforcement on ALL removal paths**
    - **Validates: Requirements 4.7, 4.8**
  - [ ] 8.5 Implement Trip Leader succession logic

    - When Trip_Leader leaves: immediately auto-promote longest-tenured member (earliest `joined_at`)
    - Set `previous_leader_id` on TripRoom, `promoted_to_leader_at` on promoted member
    - Use database advisory lock on trip_room_id for deterministic selection
    - Notify all remaining members of leadership change
    - _Requirements: 6.7_
  - [ ]* 8.6 Write property test for leader succession (Property 31)

    - **Property 31: Trip Leader abandonment auto-promotes longest-tenured member**
    - **Validates: Requirements 6.7**
- [ ] 9. Trip Room Service — Chat & PII Redaction

  - [ ] 9.1 Implement WebSocket real-time chat with Redis Pub/Sub

    - `WS /api/v1/rooms/{room_id}/ws` — WebSocket endpoint for real-time messaging
    - `POST /api/v1/rooms/{room_id}/messages` — REST fallback for message send
    - `GET /api/v1/rooms/{room_id}/messages` — paginated chat history
    - Implement Redis Pub/Sub fan-out to all connected room members
    - Handle WebSocket disconnect with client auto-reconnect support and message queueing in Redis
    - _Requirements: 4.2_
  - [ ] 9.2 Implement synchronous PII redaction middleware

    - Create PII detection engine: Indian mobile numbers (+91, 10-digit variants), email (RFC 5322), social handles (@username, instagram.com/*, t.me/*, wa.me/*)
    - Implement common evasion pattern detection: spaced digits, phonetic spellings, leet speak
    - Integrate as synchronous middleware in WebSocket message pipeline — message CANNOT be delivered until redaction completes
    - If room phase is "pre_booking": detect and redact PII before publishing to Redis Pub/Sub
    - If room phase is "booked" or later: pass through without redaction
    - On redaction failure: deliver message unredacted + log failure
    - Store original content in `MessagePIIAudit` (access-restricted), redacted content in `Message`
    - Enforce 100ms processing budget per message: pre-compile all regex patterns at service startup, implement timeout that delivers unredacted + logs on budget breach
    - Add adversarial-string benchmark (10KB mixed-character payloads, leet speak variants) to validate p99 < 100ms
    - _Requirements: 8.4, 8.5_
  - [ ]* 9.3 Write property tests for PII redaction (Properties 37, 38)

    - **Property 37: PII redaction based on Trip Room phase**
    - **Property 38: PII audit separation**
    - **Validates: Requirements 8.4, 8.5**
  - [ ] 9.4 Implement women-message-first contact control

    - Check sender gender and recipient's `women_message_first` toggle
    - Cache sender gender + recipient toggle in Redis per room membership (key: `room:{room_id}:member:{traveler_id}:gender_toggle`); invalidate on `safety_settings.updated` event
    - Fall through to Auth Service API only on cache miss (avoid hot-path cross-service call on every message)
    - Block male-initiated contact until female Traveler sends first message; completely unrestricted when toggle is disabled
    - _Requirements: 7.10_
  - [ ]* 9.5 Write property test for women-message-first (Property 35)

    - **Property 35: Women-message-first contact control**
    - **Validates: Requirements 7.10**
- [ ] 10. Trip Room Service — Itinerary & Expenses

  - [ ] 10.1 Implement shared itinerary endpoints

    - `GET /api/v1/rooms/{room_id}/itinerary` — get shared itinerary items
    - `POST /api/v1/rooms/{room_id}/itinerary` — add item (date, location, activity, notes)
    - `PUT /api/v1/rooms/{room_id}/itinerary/{item_id}` — edit item
    - `DELETE /api/v1/rooms/{room_id}/itinerary/{item_id}` — remove item
    - Auto-add booking details to itinerary on `booking.confirmed` event
    - _Requirements: 4.3, 5.12_
  - [ ] 10.2 Implement multi-party expense split tracker

    - `POST /api/v1/rooms/{room_id}/expenses` — log expense (amount in paisa, description, paid_by, split_among)
    - `GET /api/v1/rooms/{room_id}/expenses` — list all expenses
    - `GET /api/v1/rooms/{room_id}/balances` — compute and return settlement balances among all members
    - `POST /api/v1/rooms/{room_id}/settle` — record settlement payment between two members
    - Implement ceil-rounding per share with excess assigned to payer via `rounding_adjustment_paisa`
    - Ensure zero-sum invariant: sum of all balances + total rounding adjustments == 0
    - Wire concrete `SettlementGuard` implementation in `libs/shared/settlement_guard.py`: call `GET /api/v1/rooms/{room_id}/balances` and raise `SettlementRequiredError` if any pairwise balance is non-zero (replaces stub from Task 1.2)
    - _Requirements: 4.4, 4.5_
  - [ ]* 10.3 Write property test for expense split balance invariant (Property 17)

    - **Property 17: Expense split balance invariant (zero-sum with rounding adjustment)**
    - **Validates: Requirements 4.4, 4.5**
- [ ] 11. Checkpoint — Trip Room Service

  - Run `pytest services/trip_room/tests/ -x --tb=short` and verify all property tests (Properties 17–19, 31, 35, 37, 38) pass
  - Run `alembic -c services/trip_room/alembic.ini upgrade head` to validate migrations
  - Ask the user if questions arise before proceeding.
- [ ] 12. Booking Service

  - [ ] 12.1 Implement Booking data models and migrations

    - Create `Booking`, `BookingStatus`, `Vendor`, `Invoice`, `InvoiceType` models in BookingDB
    - Create vendor tier configuration table with commission rates (tier_1: 8%, tier_2: 12%, tier_3: 15%)
    - Create Alembic migration for BookingDB schema
    - _Requirements: 5.1–5.14_
  - [ ] 12.2 Implement accommodation surfacing and booking endpoints

    - `GET /api/v1/rooms/{room_id}/accommodations` — list accommodations matching Trip Room destination and dates from partner Vendors
    - `GET /api/v1/accommodations/{id}` — get details (name, price/person/night, group discount %, photos, rating)
    - `POST /api/v1/rooms/{room_id}/bookings` — initiate booking (validate room membership via Trip Room Service API)
    - `GET /api/v1/rooms/{room_id}/bookings` — list room bookings
    - `GET /api/v1/bookings/{id}` — get booking details with per-person split
    - Reject cancellation attempts after payment confirmation
    - _Requirements: 5.1, 5.2, 5.3, 5.14_
  - [ ]* 12.3 Write property tests for booking (Properties 20, 23, 27)

    - **Property 20: Accommodation filtering by destination and dates**
    - **Property 23: Commission within vendor tier band**
    - **Property 27: No cancellation after payment confirmation**
    - **Validates: Requirements 5.1, 5.6, 5.14**
  - [ ] 12.4 Implement payment trigger and GST invoicing

    - `POST /api/v1/bookings/{id}/trigger-payment` — authorize Trip_Leader (or either party in pairwise match) to trigger payment window
    - `GET /api/v1/bookings/{id}/invoices` — return GST invoices
    - Generate two Invoice records per confirmed booking: Vendor→Traveler (service) and Chalo→Vendor (commission at 18% GST + 1% TCS)
    - Subscribe to `saga.amount_recalculated` event — update per_person_amount on Booking
    - Publish `booking.confirmed` event on successful confirmation
    - _Requirements: 5.4, 5.7, 5.10_
  - [ ]* 12.5 Write property tests for GST and payment authorization (Properties 24, 47, 56)

    - **Property 24: GST invoice calculation correctness**
    - **Property 47: GST invoice persistence (two records per booking)**
    - **Property 56: Pairwise match payment authorization**
    - **Validates: Requirements 5.4, 5.7**
- [ ] 13. Payment Service

  - [ ] 13.1 Implement Payment data models and migrations

    - Create `Payment`, `PaymentStatus`, `PaymentSaga`, `SagaStatus`, `PaymentSagaStep`, `SagaStepType`, `StepStatus`, `NodalAccount`, `Escrow`, `EscrowStatus`, `Refund`, `RefundReason`, `RefundStatus` models in PaymentDB
    - Create Alembic migration for PaymentDB schema
    - _Requirements: 5.4–5.14_
  - [ ] 13.2 Implement split-payment saga orchestrator

    - `POST /api/v1/payments/split-request` — create PaymentSaga with per_person_amount = ceil(total / N), assign rounding excess to earliest member
    - Implement saga state machine: SplitRequested → CollectingPayments → AllPaid → EscrowHeld → SettledToVendor
    - Implement payment window (24–72 hours configurable)
    - `POST /api/v1/payments/{id}/complete` — process individual UPI payment (with idempotency_key)
    - Integrate Payment Aggregator API with circuit breaker
    - Subscribe to `booking.confirmed` event to initiate payment saga
    - NOTE: Test with in-memory event fixtures until Booking Service producer (Task 12.4) is complete; integration-validate at Checkpoint 20
    - _Requirements: 5.4, 5.5, 5.13_
  - [ ]* 13.3 Write property tests for payment split and window (Properties 21, 22, 50)

    - **Property 21: Per-person split calculation correctness**
    - **Property 22: Payment window within configurable bounds**
    - **Property 50: Payment saga idempotency**
    - **Validates: Requirements 5.3, 5.4, 5.5**
  - [ ] 13.4 Implement auto-removal, cancellation fee, and saga compensation

    - Implement Celery task for payment window expiry check
    - On window expiry with unpaid members: invoke shared `assert_zero_balance_or_block` guard, mark as "auto_removed", remove from Trip Room, send notification
    - `POST /api/v1/payments/{id}/cancel` (with idempotency_key) — charge exactly 5% cancellation fee, mark as "cancelled_with_fee"; reject duplicate requests with same idempotency_key
    - Recalculate per_person_amount for remaining members: publish `saga.amount_recalculated` event
    - Implement saga compensation: on step failure, execute compensation in reverse order (collect → refund)
    - `POST /api/v1/payments/{id}/refund` (with idempotency_key) — initiate refund (sum of member's COMPLETED payments less owed fees); reject duplicate requests with same idempotency_key
    - _Requirements: 5.8, 5.9, 5.10, 5.11_
  - [ ]* 13.5 Write property tests for removal, cancellation, and compensation (Properties 25, 26, 28)

    - **Property 25: Auto-removal on payment window expiry**
    - **Property 26: Cancellation fee calculation**
    - **Property 28: Payment saga compensation correctness**
    - **Validates: Requirements 5.5, 5.8, 5.9, 5.11**
  - [ ] 13.6 Implement escrow management and RBI T+1 settlement

    - Create Escrow record when funds are collected (all paid)
    - Implement Celery settlement job: verify Booking status is still CONFIRMED via Booking Service API before settling
    - Settle to vendor within T+1 business day (configurable via ESCROW_SETTLEMENT_DAYS)
    - If booking CANCELLED/FAILED: abort settlement, trigger refund flow
    - Implement nightly escrow reconciliation job (compare SUM(Escrow.amount) vs Booking.total_amount, alert ops on mismatch)
    - `POST /api/v1/webhooks/payment-aggregator` — handle Payment Aggregator callbacks:
      - Validate HMAC-SHA256 signature (`X-Razorpay-Signature` or equivalent) against webhook secret using `hmac.compare_digest` (constant-time comparison) before processing; reject unsigned/invalid requests with 401
      - Implement replay protection: reject callbacks with timestamp older than 5 minutes or duplicate idempotency_key
      - Return HTTP 200 immediately on valid signature; process payment state transition asynchronously via Celery task
    - _Requirements: 5.5, 5.7_
  - [ ]* 13.7 Write property test for escrow T+1 settlement (Property 51)

    - **Property 51: Escrow settlement within RBI T+1 timeline**
    - **Validates: Requirements 5.5, 5.7**
- [ ] 14. Checkpoint — Booking & Payment Services

  - Run `pytest services/booking/tests/ services/payment/tests/ -x --tb=short` and verify all property tests (Properties 20–28, 47, 50, 51, 56) pass
  - Run `alembic -c services/booking/alembic.ini upgrade head && alembic -c services/payment/alembic.ini upgrade head` to validate migrations
  - Ask the user if questions arise before proceeding.
- [ ] 15. Safety & Moderation Service

  - [ ] 15.1 Implement Safety data models and migrations

    - Create `Report`, `ReportCategory`, `ReportSeverity`, `ReportStatus` models in SafetyDB
    - Create Alembic migration for SafetyDB schema
    - _Requirements: 7.1–7.13_
  - [ ] 15.2 Implement report submission and severity classification

    - `POST /api/v1/safety/reports` — submit report with category, description, mandatory evidence (S3 presigned URL)
    - Auto-classify severity: harassment/safety_concern → severe; fraud/inappropriate_behavior → minor
    - Acknowledge receipt within 2 seconds
    - On severe report: immediately restrict contact between reporter and reported Traveler
    - _Requirements: 7.1, 7.2, 7.3, 7.7_
  - [ ]* 15.3 Write property tests for report severity and contact restriction (Properties 32, 34)

    - **Property 32: Report severity classification**
    - **Property 34: Severe report triggers contact restriction**
    - **Validates: Requirements 7.3, 7.7**
  - [ ] 15.4 Implement moderation resolution and enforcement

    - `GET /api/v1/admin/reports` — list reports for moderation queue
    - `POST /api/v1/admin/reports/{id}/resolve` — resolve with action; enforcement executes atomically in same transaction
    - Severe + confirmed: immediately suspend or permanently remove account
    - Minor + confirmed: increment strike_count; at 3 strikes → BANNED_PENDING_SETTLEMENT
    - All removal paths (suspend, ban, kick) invoke shared `assert_zero_balance_or_block` guard before executing removal; block removal if unsettled balances exist (except force-settle admin override)
    - On suspend or ban action: publish `user.suspended` event to Redis Streams (traveler_id, action_type, timestamp) — consumed by Auth Service (Task 2.14) to invalidate all active refresh tokens + add access token JTI to short-lived blacklist
    - `POST /api/v1/admin/travelers/{id}/force-settle` — force-settle for banned traveler, then remove from rooms and transition to BANNED
    - _Requirements: 7.4, 7.5, 7.6_
  - [ ]* 15.5 Write property tests for enforcement (Properties 33, 54)

    - **Property 33: Three-strike ban enforcement with pending settlement**
    - **Property 54: Atomic enforcement on report resolution**
    - **Validates: Requirements 7.4, 7.5, 7.6**
  - [ ] 15.6 Implement appeal mechanism

    - `POST /api/v1/safety/reports/{id}/appeal` — max 1 appeal per report (reject if appeal_count >= 1); require counter-evidence attachment
    - _Requirements: 7.9_
  - [ ] 15.7 Implement SOS emergency flow

    - `POST /api/v1/safety/sos/trigger` — log SOS event (GPS coords, timestamp), delegate SMS delivery to Notification Service (Task 16.2) resilient SMS path with circuit breaker + retry
    - SOS flow: client triggers 112 direct dial (native OS intent); server-side SMS to emergency contacts via Notification Service
    - On SMS delivery exhaustion (circuit breaker open + all retries failed): send push notification to emergency contacts as fallback, fire ops alert to on-call
    - _Requirements: 7.11, 7.12_
  - [ ] 15.8 Implement safety settings and emergency contacts

    - `PUT /api/v1/safety/settings/women-first` — toggle women-message-first; on change publish `safety_settings.updated` event to Redis Streams (traveler_id, setting_name, new_value) for Trip Room cache invalidation
    - `PUT /api/v1/safety/emergency-contacts` — set emergency contacts (validate at least 1, max 3)
    - _Requirements: 7.10, 7.12_
- [ ] 16. Notification Service

  - [ ] 16.1 Implement Notification data models and migrations

    - Create `Notification` model in NotifDB with category, title, body, data (deep link), read status
    - Create notification preferences schema (per-category opt-out flags)
    - Create Alembic migration for NotifDB schema
    - _Requirements: 11.1–11.8_
  - [ ] 16.2 Implement notification delivery and preferences

    - `GET /api/v1/notifications` — list notifications for current Traveler
    - `PUT /api/v1/notifications/preferences` — set per-category opt-out preferences
    - `POST /api/v1/notifications/{id}/read` — mark as read
    - Implement Firebase Cloud Messaging integration for push notifications (with circuit breaker)
    - Implement SMS delivery for SOS alerts (with circuit breaker and retry)
    - _Requirements: 11.1, 11.2, 11.3, 11.5_
  - [ ] 16.3 Implement notification frequency capping and re-engagement

    - Enforce opt-out: do not deliver if category opted out (except 30-day override)
    - Enforce re-engagement cap: max 1 per Traveler per 7-day window
    - Implement Celery jobs for:
      - Match expiry reminder: send 24h before pending match acceptance expires
      - Re-engagement: 7-day inactivity → send if not opted out
      - 30-day override: send single notification regardless of opt-out, include opt-out instructions
    - Send match notifications within 30 seconds of match identification
    - Batch chat notifications every 30s when app not in foreground
    - _Requirements: 11.4, 11.5, 11.6, 11.7, 11.8_
  - [ ]* 16.4 Write property tests for notifications (Properties 44, 45, 46)

    - **Property 44: Notification opt-out enforcement**
    - **Property 45: Re-engagement frequency cap**
    - **Property 46: Inactivity-based re-engagement with 30-day override**
    - **Validates: Requirements 11.5, 11.6, 11.7, 11.8**
- [ ] 17. Credibility Score Service

  - [ ] 17.1 Implement Credibility data models and migrations

    - Create `DestinationBadge`, `PeerReview` models in CreditDB
    - Create XP ledger table for auditable XP history
    - Create Alembic migration for CreditDB schema
    - _Requirements: 9.1–9.7_
  - [ ] 17.2 Implement XP, badges, and event subscriptions

    - Subscribe to `user.verified` event → award "New Traveler" badge + 100 XP
    - Subscribe to `trip.completed` event → award Destination Badge + 200 XP
    - NOTE: `trip.completed` event is produced by Celery beat task in Task 19.2; test with in-memory event fixtures until then; integration-validate at Checkpoint 20
    - `POST /api/v1/credibility/photo-share` — log photo share, award 150 XP (3x standard 50 XP)
    - Publish `xp.updated` event to Redis Streams (traveler_id, new xp, average_rating, trips_completed) → consumed by Auth Service
    - Implement idempotent consumers using ProcessedEvent table
    - `GET /api/v1/credibility/{traveler_id}` — return verified badge, total XP, Destination Badges, average rating (if ≥3 reviews)
    - _Requirements: 8.6, 9.1, 9.2, 9.3_
  - [ ]* 17.3 Write property tests for XP and badges (Properties 3 — consume side, 39, 40)

    - **Property 3 (consume): New Traveler badge awarded on `user.verified` event consumption**
    - **Property 39: Destination Badge and XP awarded on trip completion**
    - **Property 40: Photo sharing awards 3x XP**
    - **Validates: Requirements 1.10, 8.6, 9.2, 9.3**
  - [ ] 17.4 Implement peer review system

    - `POST /api/v1/credibility/reviews` — submit peer review (1–5 stars, optional text)
    - `GET /api/v1/credibility/{traveler_id}/reviews` — get reviews for display
    - Implement Celery job: trigger peer review prompt to all Trip Room members when trip end_date passes
    - Compute average_rating: null if <3 reviews, arithmetic mean (1 decimal) if ≥3 reviews
    - _Requirements: 9.4, 9.5, 9.6_
  - [ ]* 17.5 Write property test for rating display threshold (Property 41)

    - **Property 41: Rating display threshold and computation**
    - **Validates: Requirements 9.5, 9.6**
- [ ] 18. Checkpoint — Safety, Notification, Credibility Services

  - Run `pytest services/safety/tests/ services/notification/tests/ services/credibility/tests/ -x --tb=short` and verify all property tests (Properties 3-consume, 32–34, 39–41, 44–46, 54) pass
  - Run migrations for all three services to validate schema
  - Ask the user if questions arise before proceeding.
- [ ] 19. API Gateway and cross-service wiring

  - [ ] 19.1 Implement FastAPI API Gateway

    - Create gateway service with route forwarding to all 8 services
    - Implement JWT authentication middleware (issue on login, validate on every request)
    - Implement rate limiting (per-user, per-endpoint)
    - Implement request-level logging with request_id for tracing
    - Configure CORS for React Native mobile client
    - _Requirements: All services_
  - [ ] 19.2 Implement admin authorization and audit logging

    - Create admin-role middleware: validate `role: admin` claim in JWT for all `/admin/*` endpoints; reject with 403 if missing
    - Implement mandatory audit log on every admin action: write `AdminAuditLog` record (admin_id, action, target_traveler_id, payload_hash, timestamp, IP) in SafetyDB
    - Add IP allowlist for admin endpoints (configurable via environment variable)
    - _Requirements: 7.4, 7.5, 7.6 (admin security)_
  - [ ] 19.3 Wire inter-service event consumers end-to-end

    - Verify all event producers publish to Redis Streams via outbox pattern
    - Verify all event consumers process events idempotently (ProcessedEvent dedup)
    - Wire event flows:
      - `user.verified` → Credibility Service (badge + XP)
      - `user.suspended` → Auth Service (token invalidation)
      - `safety_settings.updated` → Trip Room Service (cache invalidation for women-message-first)
      - `match.confirmed` → Trip Room Service (create room)
      - `booking.confirmed` → Trip Room (itinerary), Payment (saga), Credibility (trip.completed trigger)
      - `saga.amount_recalculated` → Booking Service (update per_person_amount)
      - `xp.updated` → Auth Service (denormalized fields)
      - `trip_request.expired` → Notification Service
    - Implement Celery beat task for trip completion check (fires `trip.completed` when end_date passes + booking still CONFIRMED)
    - _Requirements: All cross-service flows_
  - [ ] 19.4 Implement data retention and batch Celery jobs

    - Create centralized `celerybeat_schedule.py` in `libs/shared/` registering ALL periodic tasks: match_computation_sweep (every 2min), trip_request_expiry (every 5min), match_expiry_check (every 5min), payment_window_expiry (every 1min), peer_review_prompt (daily), trip_completion_check (daily), escrow_reconciliation (nightly), grievance_sla_check (daily), data_retention_enforcement (weekly)
    - Data retention policy enforcement (erase data past retention_policy_days)
    - Escrow reconciliation nightly job
    - Grievance SLA check (flag >30 days unresolved)
    - Trip Request expiry sweep
    - Match expiry check
    - Payment window expiry check
    - _Requirements: DPDP compliance, 2.9, 3.5, 5.8_
- [ ] 20. Checkpoint — Integration wiring

  - Run full integration test suite: `pytest tests/integration/ -x --tb=short`
  - Validate all Redis Streams event flows end-to-end (verify event-consumer tasks 8.2, 13.2, 17.2, 2.14 work with real producers)
  - Run `pytest --hypothesis-seed=0` across all services to verify deterministic property test behavior
  - Ask the user if questions arise before proceeding.
- [ ] 21. Anti-leakage mechanisms and final properties

  - [ ] 21.1 Implement anti-leakage enforcement

    - Ensure expense-split tracker and shared itinerary are NOT exportable (no export endpoints, no external access)
    - Ensure group discounts are exclusive to Trip Room booking flow (not accessible via direct vendor URLs)
    - Ensure Credibility_Score is not portable (no external verification endpoint)
    - Implement `booking.confirmed` event handler in Trip Room: transition phase to "booked", cease all PII redaction permanently
    - _Requirements: 8.1, 8.2, 8.3, 8.5_
  - [ ]* 21.2 Write property test for data retention (Property 49)

    - **Property 49: Data retention policy enforcement**
    - **Validates: Requirements 1.2, 1.5, 1.6**
  - [ ]* 21.3 Write integration tests for critical user journeys

    - Registration → verification → badge award
    - Trip Request → match → accept → Trip Room creation
    - Trip Room → booking → payment saga → escrow → settlement
    - PII redaction pre-booking → unredacted post-booking
    - Report → moderation → enforcement → appeal
    - Trip Leader → join → approval → room expansion → leader leaves → succession
    - _Requirements: All_
- [ ] 22. Final checkpoint

  - Run `pytest --tb=short` (full suite across all services)
  - Run all 6 critical user journey integration tests from Task 21.3
  - Verify zero Hypothesis flaky test failures across 3 consecutive runs
  - Ask the user if questions arise before proceeding.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each major service
- Property tests validate the 57 universal correctness properties defined in the design (Property 3 is tested on both publish and consume sides; Property 57 covers Req 3.10 nudge logic)
- Unit tests validate specific examples and edge cases
- All services use Python 3.12+ / FastAPI with per-service PostgreSQL databases
- Cross-service communication uses Redis Streams + outbox pattern for at-least-once delivery
- Payment Aggregator integration uses idempotency keys on every call
- DPDP Act 2023 compliance is built in from the start (consent, retention, erasure, grievance)
- RBI PA/PG compliance via marketplace escrow + T+1 settlement
- GST split-invoicing with 1% TCS collection per Section 52 CGST Act

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"] },
    { "id": 1, "tasks": ["2.1", "4.1", "8.1", "12.1", "13.1", "15.1", "16.1", "17.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "4.2", "8.2", "12.2", "13.2", "15.2", "16.2", "17.2"] },
    { "id": 3, "tasks": ["2.4", "2.5", "2.6", "2.7", "4.3", "4.4", "8.3", "8.4", "8.5", "12.3", "13.3", "15.3", "16.3", "17.3"] },
    { "id": 4, "tasks": ["2.8", "2.9", "4.5", "4.6", "5.1", "8.6", "9.1", "12.4", "13.4", "15.4", "16.4", "17.4"] },
    { "id": 5, "tasks": ["2.10", "2.11", "2.12", "4.7", "5.2", "5.3", "9.2", "10.1", "12.5", "13.5", "13.6", "15.5", "15.6", "15.7", "15.8", "17.5"] },
    { "id": 6, "tasks": ["2.13", "2.14", "5.4", "6.1", "9.3", "9.4", "10.2", "13.7"] },
    { "id": 7, "tasks": ["6.2", "6.3", "9.5", "10.3"] },
    { "id": 8, "tasks": ["6.4", "19.1", "19.2"] },
    { "id": 9, "tasks": ["19.3", "19.4"] },
    { "id": 10, "tasks": ["21.1"] },
    { "id": 11, "tasks": ["21.2", "21.3"] }
  ]
}
```
