# Framework Alignment — 2026-03-24

## What Happened

Giovanni's Claude aligned both codebases into a shared framework. This branch (`aligned-framework`) replaces the `agency/` directory with a unified version that supports BOTH the AR Follow-Up agent (Giovanni) and the Email Follow-Up agent (Carlo).

**Both agents now share the same framework.** Swappable channels, tools, and hooks.

---

## What Changed

### 1. `agency/agent.py` — Merged Agent Loop
- **Kept:** Your Anthropic API agent loop (native tool_use protocol)
- **Added:** Ollama backend so Giovanni can test locally for free
- **Changed:** Hooks are now a `Hook` base class (list of instances) instead of your dict format
  - Old: `hooks={"pre_tool_use": {"send_reply": my_hook}}`
  - New: `hooks=[ChannelHITL(channel=my_channel, gated_tools=["send_reply"])]`
  - The `Hook.pre_tool_use(ToolCall) -> bool` pattern is cleaner and extensible

### 2. `agency/hooks/hitl.py` — Unified HITL
- **Replaced** your `hooks/hitl/interface.py` + `hooks/hitl/console.py` + `hooks/hitl/slack.py`
- **New pattern:** HITL goes through the `Channel` interface (same chat where the conversation happens)
- `ChannelHITL(channel, gated_tools=["send_follow_up_reply"])` works with ANY channel
- For console/dev, use `ConsoleChannel()` — prints to terminal, reads stdin
- For Telegram, use `TelegramChannel(token, chat_id)`
- For Slack, implement `SlackChannel` using your existing Block Kit code (see TODO below)

### 3. `agency/channels/` — NEW: Channel Interface
```python
class Channel(ABC):
    async def send_message(self, text: str) -> None: ...
    async def send_buttons(self, text: str, buttons: list[dict]) -> str: ...
    async def start(self, on_message) -> None: ...
    async def stop(self) -> None: ...
    async def typing(self): ...  # context manager
```
Implementations:
- `telegram.py` — Full Telegram bot (polling, buttons, typing indicator) ✅ Working
- `console.py` — Terminal input/output for dev testing ✅ Working
- `slack.py` — Placeholder, needs your Bolt code ⚠️ TODO (see below)

### 4. `agency/tools/email/` — Your EmailProvider Interface (Adopted!)
- **Kept as-is.** Your `interface.py`, `gmail.py`, `mock.py` are the standard.
- **Added:** `outlook.py` — Microsoft Graph API (same EmailProvider interface)
- Giovanni's old separate gmail/outlook functions are gone. Everything goes through the provider.

### 5. `agency/tools/` — Merged Tools
- `classifier.py` — Your two-layer classifier, unchanged ✅
- `tracker.py` — Your SQLite tracker, unchanged ✅
- `fattureincloud.py` — Giovanni's Italian accounting connector (NEW for you)
- `exact_online.py` — Dutch accounting connector (NEW for you)

### 6. `agency/tracing.py` — Your Tracer, Kept
- Your `Tracer` with cost tracking, API call logging, classification traces — all preserved
- Giovanni also has `JSONTracer` for simpler JSONL file logging (used in Telegram/Ollama testing)

### 7. `agency/config.py` — Your Config, Kept
- Your full `AgentConfig` with `${ENV_VAR}` substitution — preserved as-is
- Giovanni's simpler config is still there but yours is the canonical one for production

### 8. `agency/evals/` — Giovanni's Eval Framework (NEW for you)
- `runner.py` — Base eval runner
- `dashboard.py` — CLI dashboard that reads JSONL traces

### 9. `agency/skills/` — Merged Skills
- `classify_sent_email.md` — Your classification skill
- `draft_follow_up.md` — Your follow-up drafting skill
- AR-specific skills coming soon

---

## What You Need To Do

### IMMEDIATE: Update `run_slack.py`
Your Slack runner still uses the old HITL interface. You have two options:

**Option A (Quick): Skip HITL for Slack temporarily**
```python
# In run_slack.py, create agent without channel:
agent, config = create_agent(args.config, channel=None)
```
This works but Slack won't have approval buttons.

**Option B (Proper): Implement SlackChannel**
Create `agency/channels/slack_channel.py` that implements the `Channel` interface using your existing Bolt code. The key method is `send_buttons()` — it should post Block Kit buttons and block until a callback resolves. Your `SlackHITL.send_approval_request()` already does exactly this.

Here's the mapping:
- `Channel.send_message()` → `slack_client.chat_postMessage()`
- `Channel.send_buttons()` → Your `SlackHITL.send_approval_request()` pattern (post blocks, wait for event)
- `Channel.start()` → Start the Bolt app (Socket Mode or HTTP)
- `Channel.typing()` → Post "🤔 Thinking..." message

Your `agency/slack/` directory (app.py, blocks.py, modals.py, conversations.py) is preserved. Use it inside SlackChannel.

### LATER: Run the AR Follow-Up agent
`agents/ar_follow_up.py` + `server.py` are included. These are Giovanni's Telegram-based AR agent with Fatture in Cloud. You don't need to touch them, but they demonstrate how the same framework serves both workflows.

---

## Architecture After Alignment

```
agency/                          # SHARED — both agents use this
├── agent.py                     # Agent loop (Anthropic + Ollama backends)
├── channels/                    # Messaging platform adapters
│   ├── base.py                  # Channel interface
│   ├── telegram.py              # ✅ Working
│   ├── console.py               # ✅ Working
│   └── slack.py                 # ⚠️ Placeholder — implement with your Bolt code
├── hooks/
│   └── hitl.py                  # Channel-based HITL (works with any channel)
├── tools/
│   ├── email/                   # EmailProvider interface (YOUR pattern)
│   │   ├── interface.py
│   │   ├── gmail.py
│   │   ├── outlook.py
│   │   └── mock.py
│   ├── classifier.py            # YOUR two-layer classifier
│   ├── tracker.py               # YOUR SQLite tracker
│   ├── fattureincloud.py        # Giovanni's Italian accounting
│   └── exact_online.py          # Dutch accounting
├── skills/                      # Skill .md files
├── evals/                       # Eval framework
├── slack/                       # YOUR Slack-specific code (Bolt, blocks, modals)
├── config.py                    # YOUR config parser
├── skills.py                    # Skill loader
└── tracing.py                   # YOUR tracer + Giovanni's JSONTracer

agents/
├── ar_follow_up.py              # Giovanni's B1 agent
└── email_follow_up.py           # YOUR A3 agent (updated for new hooks)

server.py                        # Telegram bot entry point (Giovanni)
run.py                           # CLI runner (updated for ConsoleChannel)
run_slack.py                     # Slack runner (needs SlackChannel update)
```

---

## Key Principle

**The agent doesn't know what channel it's talking to.** It just calls tools. The HITL hook gates tool calls through whatever Channel is active. Swap Telegram for Slack for WhatsApp — the agent code doesn't change. Only the Channel instance in the wiring file changes.

Same for email: the agent calls `send_follow_up_reply`. Whether that goes through Gmail or Outlook depends on which EmailProvider is initialized. The agent doesn't care.

**Everything is swappable. That's the architecture.**
