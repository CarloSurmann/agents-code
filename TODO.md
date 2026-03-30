---
title: "Agency Framework — Build Status"
type: ops
status: validated
author: giovanni + carlo
created: 2026-03-24
updated: 2026-03-30
---

# Agency Framework — What's Done, What's Next

## Done

### Core Framework (`agency/`)
- [x] **Agent loop** (`agent.py`) — Anthropic + Ollama backends, tool calling, hooks, cost tracking
- [x] **Config parser** (`config.py`) — YAML → typed Python with env var substitution
- [x] **Skill loader** (`skills.py`) — .md files → system prompt injection
- [x] **Tracing** (`tracing.py`) — JSONL trace logger, every tool call recorded
- [x] **Universal prompt principles** (`skills/universal-prompt-principles.md`) — UX rules auto-injected into every agent

### Channels (all 5 implemented + tested)
- [x] **Console** — stdin/stdout for dev
- [x] **Telegram** — polling mode, inline keyboards, typing indicator
- [x] **Slack** — Socket Mode, Bolt, button callbacks
- [x] **WhatsApp** — Meta Cloud API via pywa, webhook, tested end-to-end on real phone (2026-03-29)
- [x] **Teams** — Bot Framework SDK v4, Single Tenant auth, tested via Web Chat (2026-03-29)
- [x] **Web** — WebChannel shim for dashboard HITL (Carlo)

### Tools (all implemented)
- [x] **Gmail** — OAuth2, send/search/read/watch_sent/check_thread
- [x] **Outlook** — MSAL + Graph API, send/search/read
- [x] **Mock Email** — in-memory for testing
- [x] **Fatture in Cloud** — SDK, invoices + payment status + company info (LIVE, real data)
- [x] **Exact Online** — OAuth2 for Dutch SMBs
- [x] **Classifier** — two-layer: rules (fast, free) + LLM fallback
- [x] **Tracker** — SQLite with WAL, 8 CRUD functions
- [x] **Memory** — per-customer markdown file, save/read facts across conversations
- [x] **Support KB** — keyword-based knowledge base search (Carlo)

### Hooks
- [x] **HITL** (`hooks/hitl.py`) — channel-agnostic approval gate with Approve/Edit/Skip buttons
- [x] **Confidence Gate** (`hooks/confidence_gate.py`) — auto-scores agent confidence, routes low-confidence to HITL (Carlo)
- [x] **Feedback Capture** (`hooks/feedback_capture.py`) — records human corrections, tracks approval streaks (Carlo)

### Agents (3 workflows)
- [x] **AR Follow-Up (B1)** — invoice chasing, multi-language, tested on WhatsApp + Teams
- [x] **Email Follow-Up (A3)** — scan/track/check/follow-up phases, Slack + Console
- [x] **Customer Support Triage** — classify/respond/escalate, KB search, monitors (Carlo)

### Production Infrastructure
- [x] **serve.py** — universal config-driven entry point (replaces per-customer scripts)
- [x] **onboard.py** — interactive CLI to create customer folders
- [x] **Customer config system** — YAML + .env per customer (`customers/_template/`)
- [x] **Dockerfile + docker-compose.yaml** — containerized multi-customer deployment
- [x] **README.md** — full architecture docs, component status, scaling guide, roadmap

### Quality / Testing (Carlo)
- [x] **Proving Ground** — full test harness: mock providers, scenario engine, fault injection, evals
- [x] **Scenario Packs** — YAML-driven test scenarios (ar_basic, ar_edge_cases)
- [x] **Statistical Evals** — multi-run analysis, regression detection
- [x] **Confidence scoring** (`confidence.py`) — priority chain: feedback history → rules → LLM → default
- [x] **Feedback system** (`feedback.py`) — SQLite-based, tracks corrections + approval streaks
- [x] **Health monitoring** (`monitors.py`) — volume spikes, KB gaps, response rate, draft quality
- [x] **Tuning reports** (`tuning.py`) — drift detection, KB gap analysis, edit patterns

---

## Needs Attention — Scaling & Production Hardening

These are known issues that must be fixed before deploying to 5+ customers:

- [ ] **feedback.py — connection pooling**: opens new SQLite connection every function call. Will cause churn under concurrency. Add connection pooling or context manager pattern.
- [ ] **feedback.py — retry logic on DB writes**: if `conn.commit()` fails (locked DB, disk full), no retry or fallback. Could silently lose HITL feedback data. Add exponential backoff.
- [ ] **confidence_gate.py — async/sync bridge is fragile**: tries to detect event loops, falls back with `asyncio.run()`. Will break in some async contexts. Refactor to proper async hook or delegate to channel.
- [ ] **tuning.py — encapsulation violation**: `suggest_kb_additions()` and `analyze_edit_patterns()` bypass feedback.py and open SQLite directly. Will break if feedback.py schema changes. Route through feedback module's API.
- [ ] **monitors.py — alert deduplication**: same alert can fire twice, creating duplicates in history. Add dedup by monitor_id or last-alert timestamp.
- [ ] **Hook ordering not enforced**: ConfidenceGate → ChannelHITL → FeedbackCapture must run in order, but nothing enforces it. Wrong order = silent failures. Add assertions or explicit chaining.
- [ ] **Metadata keys undocumented**: hooks communicate via `tool_call.metadata` dict with keys like `skip_hitl`, `confidence_band`, etc. No schema — typos fail silently. Create a `MetadataKeys` dataclass or enum.
- [ ] **WhatsApp permanent token**: current token expires in 24h. Need to create a System User in Meta Business Settings and generate a permanent token before any real deployment.
- [ ] **Azure free trial**: $200 credit expires in 30 days. Convert to pay-as-you-go before it lapses.
- [ ] **support_kb.py — no caching**: parses all KB markdown files on every `init_kb()` call. Slow with 50+ entries. Cache parsed sections.
- [ ] **Proving ground — no agent timeout**: if agent loops forever during test, it hangs. Wrap `agent.run()` in timeout.
- [ ] **Proving ground — no YAML schema validation**: typos in scenario YAML aren't caught until runtime. Add jsonschema validation.

---

## Needs Setup (code ready, needs credentials/accounts)

- [ ] **Gmail OAuth** — needs `credentials.json` from Google Cloud Console + first-run auth flow
- [ ] **Company registration (NL BV)** — unlocks Meta Business verification, Azure enterprise, customer contracts
- [ ] **Domain + Google Workspace** — professional email, webhook routing, customer trust
- [ ] **Meta Business verification** — with registered company, enables WhatsApp production access

---

## Priority — Build Next

### New Tools
- [ ] **Calendar** — Google Calendar + Outlook Calendar integration (schedule follow-ups, check availability)
- [ ] **CRM connectors** — HubSpot, Pipedrive (read/update contacts, deals)
- [ ] **PEC** — Italian certified email (legal requirement for formal B2B notices)
- [ ] **Mark-as-paid** — write back to Fatture in Cloud / Exact Online when payment confirmed
- [ ] **PDF attachment** — attach invoice PDFs to chase emails

### Infrastructure
- [ ] **VPS deployment** — Hetzner, nginx + Let's Encrypt, first real customer
- [ ] **Health check endpoint** — per-agent /health for monitoring
- [ ] **Watchtower** — auto-pull Docker images on push
- [ ] **Uptime Kuma** — monitoring + Telegram alerts when agents crash

---

## Later

### New Tools
- [ ] **Terminal/CLI** — execute shell commands (HITL-gated)
- [ ] **Browser automation** — web scraping, form filling
- [ ] **Database queries** — read customer databases
- [ ] **File operations** — read/write/upload documents
- [ ] **Webhook triggers** — call external APIs
- [ ] **Voice/phone** — Twilio or WhatsApp Voice integration

### Platform
- [ ] **Multi-tenant mode** — one server routing by phone number / bot ID
- [ ] **Customer management dashboard** — web UI for managing agents
- [ ] **Auto-deploy on git push** — CI/CD pipeline
- [ ] **Cost tracking** — per customer per month
- [ ] **Vector memory** — embeddings when markdown memory exceeds context window
- [ ] **Agent-to-agent** — agents delegate tasks to each other
- [ ] **WhatsApp BSP** — Embedded Signup for 2-min customer onboarding

### Quality
- [ ] **Langfuse integration** — replace JSON traces with proper observability
- [ ] **LLM-as-judge evals** — email quality scoring via Claude
- [ ] **Shadow mode** — run confidence/feedback in record-only mode before enabling auto-approve
- [ ] **Parallel test execution** — speed up proving ground statistical runs

---

## Architecture Decisions Made

- ✅ WhatsApp: Meta Cloud API (official, via pywa) — production-ready, compliant
- ✅ Teams: Bot Framework SDK v4, Single Tenant with tenant ID
- ✅ One Docker image, config-driven per customer via YAML + .env
- ✅ serve.py as universal entry point, old scripts kept for dev
- ✅ Italy first (Fatture in Cloud), Netherlands second (Exact Online)
- ✅ Company jurisdiction: Netherlands BV (recommended, pending Carlo decision)
- ✅ Per-customer persistent memory via markdown files
- ✅ Universal prompt principles auto-injected from skills/ directory
- ✅ Confidence → HITL → Feedback → Monitor loop for self-improving agents

## Architecture Decisions Open

- Multi-customer: one VPS with Docker Compose (phase 2-3) → Kubernetes (phase 4)
- OAuth token refresh strategy: cron daemon vs on-demand refresh
- PEC integration: API vs IMAP/SMTP
- When to migrate from SQLite to managed Postgres (probably at 15+ customers)
- When to implement WhatsApp BSP / Embedded Signup (probably at 5+ customers)
