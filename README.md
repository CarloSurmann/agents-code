# AI Agency — Agent Framework

Build, deploy, and manage AI agents for European SMBs.
One codebase. Any channel. Any accounting system. Any workflow.

---

## Quick Start

```bash
# Dev testing (old way — still works, always will)
CHANNEL=whatsapp python server.py
CHANNEL=teams python server.py
python run.py --config deployments/dev.yaml --interactive

# Production (new way — config-driven)
python onboard.py                              # create a new customer
python serve.py --customer pizzeria-mario      # run their agent
python serve.py --customer pizzeria-mario -i   # interactive test mode
```

---

## Architecture

```
Internet
  │
  ├── pizzeria.aiagency.eu ──→ nginx ──→ Docker container
  │                                       ├── WhatsApp webhook (:8080)
  │                                       ├── AR Follow-Up agent
  │                                       ├── Fatture in Cloud API
  │                                       └── Gmail API
  │
  ├── techbuild.aiagency.eu ──→ nginx ──→ Docker container
  │                                       ├── Teams webhook (:3978)
  │                                       ├── Email Follow-Up agent
  │                                       ├── Exact Online API
  │                                       └── Outlook API
  │
  └── ... more customers ...
```

Each customer gets their own **isolated container** running the same image
with different config. `serve.py` reads the customer's YAML and wires
everything together automatically.

---

## Directory Structure

```
agents-code/
│
├── agency/                        # SHARED FRAMEWORK
│   ├── agent.py                   # LLM loop (Anthropic + Ollama backends)
│   ├── config.py                  # YAML config loader
│   ├── skills.py                  # .md skill files → system prompt
│   ├── tracing.py                 # JSONL logging for debugging/evals
│   │
│   ├── channels/                  # MESSAGING PLATFORMS
│   │   ├── base.py                # Abstract Channel interface
│   │   ├── console.py             # Terminal (dev/testing)
│   │   ├── telegram.py            # Telegram (polling, inline keyboards)
│   │   ├── slack.py               # Slack (Socket Mode, Bolt)
│   │   ├── whatsapp.py            # WhatsApp (Meta Cloud API, pywa)
│   │   └── teams.py               # Microsoft Teams (Bot Framework SDK v4)
│   │
│   ├── hooks/                     # MIDDLEWARE
│   │   └── hitl.py                # Human-in-the-loop approval gate
│   │
│   ├── tools/                     # API INTEGRATIONS
│   │   ├── email/                 # Email providers
│   │   │   ├── interface.py       # Abstract EmailProvider
│   │   │   ├── gmail.py           # Google Gmail API (OAuth2)
│   │   │   ├── outlook.py         # Microsoft Graph API (MSAL)
│   │   │   └── mock.py            # In-memory for testing
│   │   ├── fattureincloud.py      # Italian accounting (SDK)
│   │   ├── exact_online.py        # Dutch accounting (OAuth2)
│   │   ├── classifier.py          # Email classifier (rules + LLM)
│   │   ├── tracker.py             # SQLite follow-up tracker
│   │   └── memory.py              # Per-customer persistent memory
│   │
│   ├── skills/                    # AGENT KNOWLEDGE (.md → prompt)
│   │   ├── universal-prompt-principles.md  # ← EDIT THIS: rules for ALL agents
│   │   ├── classify_sent_email.md
│   │   └── draft_follow_up.md
│   │
│   └── evals/                     # EVALUATION FRAMEWORK
│       ├── runner.py
│       └── dashboard.py
│
├── agents/                        # AGENT RECIPES (thin wiring files)
│   ├── ar_follow_up.py            # B1: Invoice chasing
│   └── email_follow_up.py         # A3: Email follow-up
│
├── customers/                     # ONE FOLDER PER CUSTOMER
│   ├── _template/                 # Copy this to onboard a new customer
│   │   ├── config.yaml            # What to run, how to run it
│   │   └── .env.example           # Secrets template
│   └── <customer-name>/           # Created by onboard.py
│       ├── config.yaml
│       ├── .env                   # Secrets (gitignored)
│       ├── memory.md              # Agent memory (auto-created)
│       └── tracker.db             # Follow-up database (auto-created)
│
├── serve.py                       # PRODUCTION entry point (config-driven)
├── onboard.py                     # Customer onboarding CLI
├── Dockerfile                     # Container image
├── docker-compose.yaml            # Multi-customer orchestration
│
├── server.py                      # DEV entry point (quick testing)
├── run.py                         # DEV CLI runner (email follow-up)
├── run_slack.py                   # DEV Slack runner
│
└── deployments/                   # Dev deployment configs
    ├── dev.yaml
    └── dev-slack.yaml
```

---

## How It Works

### The Framework (`agency/`)

Think of it as a kitchen. The agent doesn't know or care which channel
it's talking on, which accounting system it's querying, or which LLM
is generating responses. Everything is swappable.

**Agent Loop** (`agent.py`):
Sends messages to LLM → LLM requests tool calls → framework executes
tools → results go back to LLM → repeat until done. Supports both
Anthropic API (Claude) and Ollama (local models).

**Channels** (`channels/`):
Unified interface: `send_message()`, `send_buttons()`, `start()`, `stop()`,
`typing()`. Add a new platform by implementing these 5 methods.

**Tools** (`tools/`):
Plain Python functions with docstrings. The framework auto-generates
tool schemas from the function signature. To add a new integration,
write a function and add it to the agent's tool list.

**Hooks** (`hooks/`):
Intercept tool calls before/after execution. HITL hook shows an
Approve/Edit/Skip button on the channel before sending emails.

**Skills** (`skills/`):
Markdown files loaded into the system prompt. Reusable knowledge
that any agent can reference.

**Universal Prompt Principles** (`skills/universal-prompt-principles.md`):
A living document of UX rules that get injected into EVERY agent's system
prompt automatically. Things like "don't repeat what the user said", "be
time-aware", "don't over-explain". When you notice an LLM doing something
annoying, add a principle here and it fixes it for ALL agents at once.
No code changes needed — just edit the markdown file.

### Agent Recipes (`agents/`)

Thin wiring files (~30-100 lines) that combine tools + prompt into
a workflow. The recipe defines WHAT the agent does. The customer
config defines WHO it does it for and HOW.

### Customer Configs (`customers/`)

Each customer gets a folder with:
- **config.yaml** — workflow, channel, accounting, email, custom instructions
- **.env** — API keys and secrets (gitignored)
- **memory.md** — facts the agent learns across conversations (auto-created)

### The Flow

```
onboard.py creates:     customers/pizzeria-mario/config.yaml + .env
                                     │
serve.py reads config ──────────────→│
    │                                │
    ├── Creates Channel (WhatsApp)   │
    ├── Creates Tools (FIC + Gmail)  │
    ├── Loads Memory (memory.md)     │
    ├── Builds System Prompt         │
    │   ├── Base prompt (from workflow)
    │   ├── Customer identity
    │   ├── Custom instructions
    │   ├── Memory facts
    │   ├── Universal principles (agency/skills/universal-prompt-principles.md)
    │   └── Communication rules
    ├── Creates HITL Hook            │
    └── Creates Agent                │
         │                           │
         └── Starts listening on channel
              │
              ├── Customer sends WhatsApp message
              ├── Agent processes with LLM + tools
              ├── HITL gate on sensitive actions
              └── Agent replies on WhatsApp
```

---

## Onboarding a New Customer

```bash
python onboard.py

# Prompts for:
#   Company name      → "Pizzeria Mario SRL"
#   Contact name      → "Mario Rossi"
#   Language          → italian
#   Workflow          → ar-follow-up
#   Channel           → whatsapp
#   Accounting        → fattureincloud
#   Email             → gmail

# Creates: customers/pizzeria-mario/config.yaml + .env
# Then:    fill in .env with API credentials
# Test:    python serve.py --customer pizzeria-mario --interactive
# Deploy:  python serve.py --customer pizzeria-mario
```

---

## Production Deployment

```bash
# Build the Docker image
docker-compose build

# Start all customers
docker-compose up -d

# Start one customer
docker-compose up pizzeria-mario

# View logs
docker-compose logs -f pizzeria-mario

# Stop everything
docker-compose down
```

Each customer is a service in `docker-compose.yaml`:

```yaml
services:
  pizzeria-mario:
    build: .
    command: python serve.py --customer pizzeria-mario
    env_file: customers/pizzeria-mario/.env
    ports:
      - "8080:8080"
    restart: unless-stopped
```

Nginx reverse proxy routes subdomains to containers. Let's Encrypt
provides free SSL certificates.

---

## Component Status

### Channels

| Channel | Status | Notes |
|---------|--------|-------|
| Console | Done | stdin/stdout for dev |
| Telegram | Done | Polling mode, inline keyboards, typing indicator |
| Slack | Done | Socket Mode, Bolt, button callbacks |
| WhatsApp | Done | Meta Cloud API, pywa, webhook, tested end-to-end |
| Teams | Done | Bot Framework SDK v4, Single Tenant, tested via Web Chat |

### Tools

| Tool | Status | Notes |
|------|--------|-------|
| Gmail | Done | OAuth2, send/search/read/watch |
| Outlook | Done | MSAL + Graph API |
| Mock Email | Done | In-memory for testing |
| Fatture in Cloud | Done + Live | SDK, connected to real data |
| Exact Online | Done | OAuth2 for Dutch SMBs |
| Classifier | Done | Two-layer: rules (fast) + LLM fallback |
| Tracker | Done | SQLite with WAL, 8 CRUD functions |
| Memory | Done | Markdown file, save/read per customer |

### Agents

| Agent | Status | Notes |
|-------|--------|-------|
| AR Follow-Up (B1) | Done | Invoice chasing, multi-language |
| Email Follow-Up (A3) | Done | Scan/track/check/follow-up phases |

### Infrastructure

| Component | Status | Notes |
|-----------|--------|-------|
| serve.py | Done | Universal config-driven entry point |
| onboard.py | Done | Interactive customer onboarding CLI |
| Customer configs | Done | YAML + .env template |
| Dockerfile | Done | Single image, config-driven |
| docker-compose | Done | Multi-customer orchestration |
| Memory system | Done | Per-customer markdown persistence |

---

## Infrastructure & Scaling

### Phase 1: Dev (now)
Your laptop + ngrok. One process, one tunnel. Fine for testing.

### Phase 2: First Customers (1-5)
Single Hetzner VPS (4GB, ~€10/month). Docker Compose runs all containers.
Nginx routes webhooks by subdomain. Let's Encrypt for free SSL.

```
VPS (4GB RAM)
├── docker-compose.yaml
├── Container: customer-a  (~100MB RAM each)
├── Container: customer-b
├── Container: nginx (routes + SSL)
└── Watchtower (auto-pulls image updates)
```

### Phase 3: Growing (5-30)
Bigger VPS (16-32GB, ~€30-60/month). Same Docker Compose setup.
Add monitoring (Uptime Kuma or Grafana). Automated backups.

### Phase 4: Scale (30-100+)
Managed Kubernetes (Hetzner/DigitalOcean). Multiple nodes.
Managed Postgres database (replaces per-customer SQLite).
Secrets management (HashiCorp Vault or cloud-native).
The agent code doesn't change — only the deployment config.

### Cost Estimate Per Customer
- VPS share: ~€1-3/month (split across customers)
- Anthropic API: ~€5-30/month (depends on usage)
- WhatsApp: free for first 1000 conversations/month, then ~€0.05/msg
- Total infra cost per customer: ~€10-40/month

---

## Next Steps — Roadmap

### Near Term (next 2-4 weeks)

**New Tools:**
- [ ] Calendar integration (Google Calendar / Outlook Calendar)
- [ ] CRM connectors (HubSpot, Pipedrive)
- [ ] PEC — Italian certified email (required for formal notices)
- [ ] Mark-as-paid — write back to accounting system when payment received
- [ ] PDF attachment handling — read/attach invoices to chase emails

**New Agents:**
- [ ] Inbound Lead Response (D1) — auto-reply to inquiries
- [ ] Email Triage (A1) — classify and route incoming email
- [ ] Automated Reporting (C1) — weekly summaries

**Infrastructure:**
- [ ] Nginx config templates + Let's Encrypt automation
- [ ] Health check endpoint per agent
- [ ] Monitoring dashboard (agent status, errors, costs)
- [ ] Secrets management (HashiCorp Vault or encrypted env)

### Medium Term (1-3 months)

**New Tools:**
- [ ] Terminal/CLI tool — execute shell commands (HITL-gated)
- [ ] Browser automation — web scraping, form filling
- [ ] Database query tool — read customer databases
- [ ] File operations — read/write/upload documents
- [ ] Webhook trigger tool — call external APIs

**Platform:**
- [ ] Multi-tenant mode — one server, route by phone number/bot ID
- [ ] Customer management dashboard (web UI)
- [ ] Auto-deploy on git push
- [ ] Cost tracking per customer per month

**Channels:**
- [ ] WhatsApp Business Solution Provider (BSP) — Embedded Signup for easy onboarding
- [ ] Web chat widget — embed on customer websites

### Long Term (3-6 months)

- [ ] Vector memory (embeddings) — when markdown memory gets too large
- [ ] Agent-to-agent communication — agents delegate tasks to each other
- [ ] Custom workflow builder — customers define their own workflows
- [ ] Marketplace — pre-built agent templates per industry
- [ ] Multi-language voice — phone call handling via Twilio/WhatsApp Voice

---

## Dev Quick Reference

```bash
# Quick test (old entry points — still work)
CHANNEL=whatsapp python server.py      # WhatsApp + AR agent
CHANNEL=teams python server.py         # Teams + AR agent
CHANNEL=console python server.py       # Terminal + AR agent
python run.py --config deployments/dev.yaml --interactive

# Production (new entry point)
python serve.py --customer <id>        # Run customer's agent
python serve.py --customer <id> -i     # Interactive test mode

# Onboarding
python onboard.py                      # Create new customer

# Docker
docker-compose build                   # Build image
docker-compose up -d                   # Start all
docker-compose up <customer>           # Start one
docker-compose logs -f <customer>      # View logs
```

---

## Contributing

- **agency/** is the shared framework — changes here affect ALL agents and customers
- **agents/** are thin recipes — add new workflows here
- **tools** are self-contained — adding one never breaks others
- **channels** implement the Channel interface — add new platforms by implementing 5 methods
- Always test with `--interactive` mode before deploying
- Secrets go in `.env` files (gitignored), never in YAML or code
