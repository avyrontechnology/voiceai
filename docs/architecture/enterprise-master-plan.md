# Enterprise Architecture Master Plan

**Goal:** Transform the current codebase into an enterprise-grade system with
SOLID principles, clean layered architecture, GDPR compliance, ISO 27001 certification,
and Meta API integration.

**Current state:** 94k LOC new arch, 8 modules, 56 test files — but concerns scattered,
no interface discipline, missing compliance framework, no real external API integration.

---

## 1. Current State Audit

### What exists
- **Modules:** voice (50k), agents (16.5k), tools (8.5k), auth (7.3k), chat, catalog, wallet, voices, health
- **Kernel:** common/ (envelopes, errors, security), core/ (container, env, app_factory), database/ (BaseFields, Collections)
- **Strangler:** adapters/ bridges legacy, identity.py shim
- **Gates:** make check (ruff + mypy + pytest 2165 tests), make sec (bandit), coverage 93%

### What's broken (violates SOLID)
- **Single Responsibility:** `voice/session/turn/transcript_listener.py` 1271 lines — owns ASR, debounce, interruption, barge-in, VAD
- **Open/Closed:** Adding a provider means editing executor.py switch — no plugin interface
- **Interface Segregation:** `AuthService` is 370 lines — owns sessions, JWT, API keys, users, invites, tickets
- **Dependency Inversion:** Services construct concrete repositories — no ports/adapters
- **Scattered concerns:** Security checks in controller, service, AND middleware — no single enforcement point
- **Duplicate naming:** `vault_models.py` + `models.py`, `vault_repository.py` + `repository.py`, `credential_store.py` vs `vault.py`

### What's missing
- **GDPR:** No data subject rights, no consent management, no retention policies, no DSAR process
- **ISO 27001:** No ISMS, no risk register, no audit trail, no incident management
- **Meta API:** WhatsApp node is simulated — no real Graph API integration

---

## 2. Target Architecture

### 2.1 SOLID Principles

| Principle | Current | Target |
|---|---|---|
| **S**ingle Responsibility | 1271-line files | ≤ 200 lines per file, one responsibility |
| **O**pen/Closed | Edit switch for new providers | Plugin registry — register, don't modify |
| **L**iskov Substitution | Concrete class dependencies | Interface-based — any implementation substitutable |
| **I**nterface Segregation | Fat AuthService | Small ports: SessionStore, TokenService, UserRepo |
| **D**ependency Inversion | Services construct repos | All dependencies via container interfaces |

### 2.2 Clean Layered Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Presentation (controllers, DTOs, middleware)            │
│  - Parse input, validate, enforce scope, wrap response   │
├─────────────────────────────────────────────────────────┤
│  Application (services, use cases, orchestration)       │
│  - Business logic, authorization, transaction bounds    │
├─────────────────────────────────────────────────────────┤
│  Domain (models, domain services, domain events)        │
│  - Business rules, invariants, no I/O                    │
├─────────────────────────────────────────────────────────┤
│  Infrastructure (repositories, external APIs, cache)    │
│  - Data access, outbound calls, persistence             │
└─────────────────────────────────────────────────────────┘
```

**Rules:**
- Domain has zero imports from Application/Infrastructure
- Application depends on Domain + Ports (interfaces)
- Infrastructure implements Ports
- Presentation depends on Application via container

### 2.3 Bounded Contexts

| Context | Module | External Ports |
|---|---|---|
| Identity & Access | auth | SessionStore, TokenService, UserRepo |
| Voice & Telephony | voice | ASRProvider, TTSProvider, TelephonyProvider |
| Tools & Integrations | tools | ToolExecutor, CredentialVault, IntegrationGateway |
| Billing & Wallet | wallet | PaymentGateway, LedgerRepo |
| Chat | chat | ChatProvider, MessageRepo |

---

## 3. GDPR Compliance Framework

### 3.1 Data Subject Rights (Chapter 3 GDPR)

| Right | Implementation | Endpoint |
|---|---||
| **Access** (Art. 15) | Export all data for a data subject | `GET /privacy/export/{subject_id}` |
| **Rectification** (Art. 16) | Update inaccurate data | `PATCH /privacy/rectify/{subject_id}` |
| **Erasure** (Art. 17) | Hard delete with audit trail | `DELETE /privacy/erase/{subject_id}` |
| **Portability** (Art. 20) | Machine-readable export (JSON) | `GET /privacy/export/{subject_id}?format=json` |
| **Restriction** (Art. 18) | Mark data as restricted | `POST /privacy/restrict/{subject_id}` |
| **Objection** (Art. 21) | Opt-out of processing | `POST /privacy/object/{subject_id}` |

### 3.2 Consent Management
- **Consent record:** `{subject_id, purpose, granted, timestamp, version, ip_hash}`
- **Purposes:** analytics, marketing, third_party_sharing, voice_recording
- **Consent API:** `POST /privacy/consent`, `GET /privacy/consent/{subject_id}`, `DELETE /privacy/consent/{subject_id}/{purpose}`

### 3.3 Data Retention Policies

| Data Type | Retention | Action |
|---|---|---|
| Session tokens | 7 days | Auto-expire (Redis TTL) |
| Auth events | 90 days | Soft delete → hard delete |
| Voice recordings | 30 days | Purge from storage |
| Transcripts | 90 days | Anonymize → delete |
| Chat messages | 1 year | Archive → delete |
| Audit logs | 7 years | Archive (legal requirement) |
| Execution logs | 1 year | Soft delete |

### 3.4 DSAR (Data Subject Access Request) Process
1. **Request intake:** `POST /privacy/dsar` — identity verification required
2. **Identity proof:** Document upload + verification (30-day window)
3. **Data collection:** Aggregate from all collections by `subject_id`
4. **Review:** DPO reviews before export
5. **Delivery:** Secure download link (7-day expiry)
6. **Audit:** Log the entire request lifecycle

### 3.5 Privacy by Design
- **Data minimization:** Collect only what's needed for the stated purpose
- **Purpose limitation:** Each collection has a declared purpose
- **Storage limitation:** Retention policies enforced by scheduled jobs
- **Accuracy:** Rectification process + periodic data quality checks
- **Integrity/confidentiality:** Encryption at rest (AES-256), access controls
- **Accountability:** Audit trails, DPO designation, privacy impact assessments

### 3.6 Data Breach Notification
- **Detection:** Automated monitoring + alerting
- **Assessment:** 72-hour risk assessment window
- **Notification:** Supervisory authority within 72 hours (Art. 33)
- **Communication:** Affected data subjects without undue delay (Art. 34)
- **Documentation:** All breaches documented regardless of notification

---

## 4. ISO 27001 Certification Roadmap

### 4.1 ISMS (Information Security Management System)

**Scope:** All voice AI backend systems, data processing, and infrastructure.

**Policy framework:**
- Information Security Policy
- Access Control Policy
- Asset Management Policy
- Cryptographic Policy
- Physical Security Policy
- Operations Security Policy
- Communications Security Policy
- Supplier Relationships Policy
- Incident Management Policy
- Business Continuity Policy
- Compliance Policy

### 4.2 Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Unauthorized data access | Medium | Critical | RBAC + encryption + audit |
| Data breach | Medium | Critical | Encryption + monitoring + DPO |
| Insider threat | Low | High | Least privilege + audit logs |
| Provider compromise | Low | High | Vendor assessment + SLA |
| DDoS attack | High | Medium | Rate limiting + WAF + CDN |
| Ransomware | Low | Critical | Backups + recovery + training |
| Misconfiguration | Medium | Medium | IaC + scanning + review |

### 4.3 Controls (Annex A)

| Control | Implementation |
|---|---|
| **A.5** Policies | Documented policy framework |
| **A.6** Organization | Roles, responsibilities, segregation of duties |
| **A.7** HR Security | Background checks, training, termination |
| **A.8** Asset Management | Asset inventory, classification, handling |
| **A.9** Access Control | RBAC, MFA, least privilege, access review |
| **A.10** Cryptography | AES-256 at rest, TLS 1.3 in transit, key management |
| **A.11** Physical Security | Data center access, environmental controls |
| **A.12** Operations Security | Logging, monitoring, vulnerability management |
| **A.13** Communications Security | Network security, encryption |
| **A.14** System Acquisition | Secure SDLC, testing, review |
| **A.15** Supplier Relationships | Vendor assessment, contracts, SLAs |
| **A.16** Incident Management | Detection, response, recovery, lessons learned |
| **A.17** Business Continuity | Backup, DR, testing |
| **A.18** Compliance | GDPR, PCI, legal requirements |

### 4.4 Audit Trail
- **Who:** Actor ID, role, IP
- **What:** Action, resource, result
- **When:** Timestamp (UTC, tamper-evident)
- **Where:** Source IP, user agent
- **Immutable:** Append-only, 7-year retention

### 4.5 Certification Timeline

| Phase | Duration | Deliverables |
|---|---|---|
| Gap analysis | 2 weeks | Current state assessment |
| Policy writing | 3 weeks | ISMS policy framework |
| Control implementation | 8 weeks | Technical controls + documentation |
| Internal audit | 2 weeks | Audit report + corrective actions |
| Certification audit | 3 weeks | External audit + certification |
| **Total** | **18 weeks** | **ISO 27001 certified** |

---

## 5. Meta API Integration

### 5.1 WhatsApp Business API

**Architecture:**
```
Client (WhatsApp) → Meta Webhook → voiceai webhook endpoint
    → Signature verification (X-Hub-Signature-256)
    → Message parsing (text, image, audio, video, document)
    → Business logic (agent routing, response generation)
    → Meta Graph API (send message, media)
    → Response to client
```

**Components:**
- **Webhook receiver:** `POST /webhooks/whatsapp` — verify signature, parse event
- **Message sender:** `POST /v21.0/{phone-number-id}/messages` — text, media, templates
- **Media upload:** `POST /v21.0/{phone-number-id}/media` — upload audio/video
- **Template manager:** Create/query message templates via Graph API

**Endpoints:**
- `POST /webhooks/whatsapp` — receive webhook (verify + process)
- `GET /webhooks/whatsapp` — webhook verification (Meta challenge)
- `POST /integrations/whatsapp` — configure WhatsApp integration
- `GET /integrations/whatsapp/{id}/status` — health check

**Models:**
- `WhatsappWebhookEvent`: `{entry: [{id, changes: [{value: {messages, metadata}}]}]}`
- `WhatsappMessage`: `{from, id, timestamp, type, text/media}`
- `WhatsappConfig`: `{phone_number_id, access_token, verify_token, business_account_id}`

### 5.2 Instagram Messaging API
- Same webhook pattern as WhatsApp
- Reuse message parsing + response generation

### 5.3 Rate Limiting & Quotas
- Meta rate limits: 200 messages/hour per number (default)
- Token bucket algorithm per phone number
- Queue + retry with exponential backoff

### 5.4 Media Handling
- **Upload:** Audio (ogg, mp3), Video (mp4), Image (jpg, png)
- **Download:** Media ID → download URL → store locally
- **Transcoding:** FFmpeg pipeline for format normalization
- **Storage:** S3-compatible object storage with lifecycle policies

---

## 6. Implementation Roadmap

### Phase 1: Architecture Overhaul (specs 0063–0070)
- **0063:** SOLID refactor — split transcript_listener, executor plugin interface
- **0064:** Interface segregation — AuthService → SessionStore + TokenService + UserRepo
- **0065:** Bounded contexts — module boundaries + container interfaces
- **0066:** File size budget — enforce ≤ 800 lines per file
- **0067:** Provider plugin registry — ASR/TTS/telephony
- **0068:** Dependency inversion — all services via container ports

### Phase 2: GDPR Compliance (specs 0071–0078)
- **0071:** Consent management — models + API
- **0072:** Data subject rights — access, rectification, erasure, portability
- **0073:** Retention policies — scheduled jobs + enforcement
- **0074:** DSAR process — request intake → delivery
- **0075:** Data breach notification — detection + assessment + notification
- **0076:** Privacy by design — data minimization + purpose limitation
- **0077:** Audit trail — immutable access logs
- **0078:** DPO designation + documentation

### Phase 3: ISO 27001 Certification (specs 0079–0086)
- **0079:** ISMS policy framework — 11 policies
- **0080:** Risk register — assessment + treatment plan
- **0081:** Access control — RBAC + MFA + access review
- **0082:** Cryptographic controls — key management + rotation
- **0083:** Operations security — logging + monitoring + vuln management
- **0084:** Incident management — detection + response + recovery
- **0085:** Business continuity — backup + DR + testing
- **0086:** Internal audit + certification preparation

### Phase 4: Meta API Integration (specs 0087–0092)
- **0087:** WhatsApp webhook receiver — verify + parse
- **0088:** WhatsApp message sender — text + media
- **0089:** Media pipeline — upload, download, transcode
- **0090:** Rate limiting — token bucket + queue
- **0091:** Instagram messaging — reuse WhatsApp components
- **0092:** Meta integration tests — offline mocks + golden fixtures

---

## 7. Architecture Delta

**Impact on existing diagrams:**
- `repo-topology.html` — add WhatsApp/Meta as external system
- `module-map.html` — add privacy module + security module
- `trust-boundaries.html` — add GDPR boundary + consent boundary
- `lifecycles.html` — add WhatsApp webhook lifecycle
- `data-model.html` — add consent + DSAR + audit log models

**New modules:**
- `privacy` — GDPR compliance (consent, DSAR, data rights)
- `security` — ISO 27001 controls (audit, risk, access)
- `integrations` — external API adapters (WhatsApp, Instagram)

**New collections:**
- `CONSENT_RECORDS`, `DSAR_REQUESTS`, `AUDIT_LOGS`, `RISK_REGISTER`, `INCIDENTS`

---

## 8. Verification

**Phase 1:** make check + make sec + make cov green; layer contract tests pass
**Phase 2:** GDPR compliance tests; data retention verification
**Phase 3:** ISO internal audit; risk register review
**Phase 4:** Meta API integration tests; webhook verification tests

**Overall:** Enterprise-grade architecture with SOLID principles, GDPR compliance,
ISO 27001 certification, and Meta API integration.
