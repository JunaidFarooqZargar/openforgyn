# OpenForgyn — Claude Code Instructions

## What Is This Project?

OpenForgyn is a **next-generation personal AI agent** — the successor
to OpenClaw, NanoClaw, and the entire first wave of Claws.

The agent it creates is called **Forgyn**.

The core innovation: **Forgyn writes its own capabilities from scratch.**
No skill registry. No pre-built plugins. No supply chain.
You describe what you need, and the agent writes the code, tests it in a
sandbox, and deploys it to itself.

## Why This Exists

Andrej Karpathy defined the evolution of AI layers:

> "First there was chat, then there was code, now there is claw."

Claws are a new layer on top of LLM agents — personal AI agents that act in
the real world through orchestration, scheduling, context, tool calls, and
persistence. OpenClaw proved the category (190K GitHub stars in 14 days).

But OpenClaw has critical problems:
- **400K+ lines of vibe-coded TypeScript** — unauditable by humans or AI
- **Security nightmare** — 1,184 malicious skills in ClawHub, RCE vulnerabilities,
  40,000+ exposed instances, supply chain poisoning
- **Skill registry model is fundamentally broken** — 11.9% of ClawHub skills
  are malware (vs <1% for npm)

Karpathy himself said:
> "I'm definitely a bit sus'd to run OpenClaw specifically — giving my private
> data/keys to 400K lines of vibe coded monster that is being actively attacked
> at scale is not very appealing at all."

He praised NanoClaw (~500 lines, fits in human head + AI context, skills-as-code-
modification) but NanoClaw is Claude-only, has pre-written skill instructions
(not self-generating), and hasn't achieved viral adoption (~10K stars).

OpenForgyn is **Generation 3** of Claws:
- **Gen 1 (OpenClaw)**: Download skills from a registry → supply chain attacks
- **Gen 2 (NanoClaw)**: Apply pre-written skill instructions to your fork → bounded by what humans write
- **Gen 3 (OpenForgyn)**: The agent writes its own skills from scratch → no registry, no supply chain, unbounded growth

## The Goal

Build an open-source personal AI agent that:
1. Has an ultra-small core (~1500 lines of TypeScript)
2. Writes its own capabilities (skills) from scratch using LLMs
3. Tests all self-written code in sandboxed containers before deployment
4. Is secure by architecture (no external skill registry, capability-based permissions)
5. Works with ANY LLM provider (OpenAI, Anthropic, Google, local via Ollama)
6. Runs locally on a Mac Mini / any Linux box
7. Connects to messaging apps (WhatsApp, Telegram, Slack)
8. Can integrate with home automation and physical devices
9. Maintains a full audit trail (git-based) of all self-modifications

The ultimate ambition: become the reference implementation for self-evolving
AI agents, achieve viral adoption, and attract acquisition interest from
major AI labs (OpenAI, Anthropic, Google).

## Reference Material

### Study These Before Writing Code:

1. **NanoClaw** (`../reference_claws/nanoclaw/`) — The closest architectural
   inspiration. Study:
   - Core engine (~500 lines across 4 files)
   - The "skills as code modification" pattern (`.claude/skills/`)
   - Container isolation approach
   - WhatsApp integration
   - How it keeps the codebase small and AI-readable

2. **OpenClaw** (`../reference_claws/openclaw/`) — The category creator.
   Study at HIGH LEVEL only (it's 400K+ lines):
   - Overall architecture (what to AVOID)
   - How skills/plugins work (the broken model)
   - What made it popular (features, integrations)
   - Security vulnerabilities (what to NOT repeat)

3. **Analysis documents** in parent directory:
   - `../deep_analysis_v2.md` — Full strategic analysis and architecture spec
   - `../deep_analysis.md` — Earlier analysis with acquisition landscape
   - `../discussion.md` — Full brainstorming discussion with context
   - `../karpathy.md` — Karpathy's exact tweet (decode this carefully)

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│  CORE RUNTIME (~1500 lines, IMMUTABLE)      │
│                                             │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │ Reasoner │ │ Scheduler│ │ Skill Writer│ │
│  │ (LLM     │ │ (cron +  │ │ (generates  │ │
│  │  loop)   │ │  events) │ │  new code)  │ │
│  └──────────┘ └──────────┘ └─────────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │ Sandbox  │ │ Audit    │ │ Permission  │ │
│  │ Runner   │ │ Logger   │ │ Gate        │ │
│  └──────────┘ └──────────┘ └─────────────┘ │
│  ┌──────────┐ ┌──────────┐                  │
│  │ MCP      │ │ Message  │                  │
│  │ Client   │ │ Bridge   │                  │
│  └──────────┘ └──────────┘                  │
└─────────────────────────────────────────────┘
         │ writes to │ reads from
         ▼           ▼
┌─────────────────────────────────────────────┐
│  SKILLS DIRECTORY (agent-written, mutable)  │
│                                             │
│  skills/                                    │
│  ├── whatsapp/     (agent-generated)        │
│  ├── smart-home/   (agent-generated)        │
│  ├── email/        (agent-generated)        │
│  └── ...           (grows over time)        │
└─────────────────────────────────────────────┘
         │ runs inside
         ▼
┌─────────────────────────────────────────────┐
│  CONTAINER SANDBOX                          │
│  (Docker / Apple Containers)                │
└─────────────────────────────────────────────┘
```

### Core Components (target ~1500 lines total):

1. **Reasoner** (`src/reasoner.ts`) — Main LLM loop. Takes user messages,
   plans actions, delegates to existing skills or triggers skill generation.

2. **Skill Writer** (`src/skill-writer.ts`) — THE key innovation. When the
   Reasoner needs a capability that doesn't exist:
   - Generates a spec for the skill
   - Writes the code using the LLM
   - Writes tests for the skill
   - Runs tests in the sandbox
   - If tests pass → deploys to skills directory
   - If tests fail → iterates (up to N attempts)
   - Logs everything to the audit trail

3. **Sandbox Runner** (`src/sandbox.ts`) — Container-based execution for
   all skills. Skills cannot access anything outside their declared permissions.

4. **Scheduler** (`src/scheduler.ts`) — Cron-like scheduler for recurring
   tasks. Also handles event-driven triggers.

5. **Permission Gate** (`src/permissions.ts`) — User approval for new
   capabilities. Each skill declares what it needs (network, filesystem,
   specific APIs). User grants or denies.

6. **Audit Logger** (`src/audit.ts`) — Git-based logging. Every skill
   write/modification is a commit. Full history. Full rollback.

7. **Message Bridge** (`src/bridge.ts`) — Connects to messaging apps
   (WhatsApp, Telegram, Slack). This is one of the first skills the agent
   can write for itself, but a minimal bridge should exist in core for
   the initial interaction channel.

8. **MCP Client** (`src/mcp.ts`) — Can discover and use external MCP
   servers. The integration code for specific MCP servers is written by
   the agent (not downloaded).

9. **Database** (`src/db.ts`) — SQLite for conversation history, skill
   metadata, and scheduling state.

10. **Entry Point** (`src/index.ts`) — Orchestrator that wires everything
    together. CLI interface for initial setup.

### Key Design Principles:

- **Total core must be ~1500 lines.** This is a hard constraint. The entire
  codebase must fit in an AI model's context window AND be readable by a
  human in under 20 minutes. If you're going over, refactor.

- **Skills directory is the ONLY mutable part.** The core runtime is
  read-only in production. The agent can only write to `skills/`.

- **Every skill has: code + tests + manifest.** The manifest declares
  permissions. Tests must pass in sandbox before deployment.

- **Multi-model from day one.** Support OpenAI, Anthropic, Google, and
  local models via Ollama. Use a simple adapter pattern. The Skill Writer
  should use the strongest available model; the Reasoner can use cheaper
  models for routine decisions.

- **Container-first.** All skills run in Docker containers by default.
  Apple Containers on macOS if available.

- **Git-based audit trail.** The skills directory is a git repo. Every
  change is a commit with a message explaining what was added/modified
  and why.

## The Self-Writing Loop (How It Works)

```
User: "Remind me about meetings 15 minutes before they start"

Forgyn (Reasoner) thinks:
  1. Do I have a calendar skill? → No
  2. Do I have a notification skill? → No
  3. I need to BUILD both.

Forgyn (Skill Writer) executes:
  → Writes skills/calendar/handler.ts (Google Calendar OAuth + fetch)
  → Writes skills/calendar/tests.ts (mock API, test threshold logic)
  → Writes skills/calendar/manifest.json (needs: network, google-calendar scope)
  → Writes skills/notify/handler.ts (system notifications)
  → Writes skills/notify/tests.ts (test notification fires correctly)
  → Writes skills/notify/manifest.json (needs: system-notifications)

Forgyn (Permission Gate):
  "Forgyn wants to access Google Calendar API. Allow? [y/n]"
  "Forgyn wants to send system notifications. Allow? [y/n]"

Forgyn (Sandbox Runner):
  → Runs all tests in container → 6/6 pass

Forgyn (Audit Logger):
  → git commit: "Added calendar + notification skills for meeting reminders"

Forgyn (Scheduler):
  → Registers: "check calendar every 5 minutes"

Forgyn responds:
  "Done. I built a Google Calendar integration and notification system.
   I'll remind you 15 min before meetings. Here's what I built: [diff link]"
```

## Tech Stack

- **Language**: TypeScript (most AI training data, most forkable, npm ecosystem)
- **Runtime**: Node.js (Bun is a good alternative if perf matters)
- **Database**: SQLite via better-sqlite3
- **Containers**: Docker (primary), Apple Containers (macOS option)
- **LLM SDK**: Vercel AI SDK or direct API calls (keep it simple)
- **MCP**: Use the official MCP TypeScript SDK
- **Testing**: Vitest (fast, TypeScript-native)

## File Structure (Target)

```
openforgyn/
├── CLAUDE.md              ← You are here
├── README.md              ← Project README (beautiful, clear)
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts           ← Entry point + CLI (~150 lines)
│   ├── reasoner.ts        ← Main LLM reasoning loop (~300 lines)
│   ├── skill-writer.ts    ← Self-writing engine (~250 lines)
│   ├── sandbox.ts         ← Container runner (~150 lines)
│   ├── scheduler.ts       ← Task scheduling (~100 lines)
│   ├── permissions.ts     ← Capability-based permission gate (~100 lines)
│   ├── audit.ts           ← Git-based audit logging (~100 lines)
│   ├── bridge.ts          ← Messaging bridge interface (~100 lines)
│   ├── mcp.ts             ← MCP client (~100 lines)
│   ├── db.ts              ← SQLite persistence (~80 lines)
│   └── models.ts          ← Multi-model adapter (~100 lines)
├── skills/                ← Agent-written skills (initially empty)
│   └── .gitkeep
├── tests/                 ← Core runtime tests
│   └── ...
└── docker/
    └── skill.Dockerfile   ← Base container for skill execution
```

## Development Order

### Phase 1: Core Runtime (Week 1-2)
1. Set up project scaffolding (package.json, tsconfig, etc.)
2. `src/db.ts` — SQLite schema for conversations, skills, schedules
3. `src/models.ts` — Multi-model adapter (OpenAI + Anthropic + Ollama)
4. `src/reasoner.ts` — Main LLM loop with tool-calling
5. `src/index.ts` — CLI entry point, basic chat interface
6. **Milestone**: Can have a conversation and use basic built-in tools

### Phase 2: Self-Writing Engine (Week 3-4)
1. `src/sandbox.ts` — Docker container runner for skill execution
2. `src/skill-writer.ts` — THE core innovation
3. `src/permissions.ts` — Capability approval flow
4. `src/audit.ts` — Git-based change tracking
5. `docker/skill.Dockerfile` — Base image for skill containers
6. **Milestone**: Agent can write, test, and deploy a new skill

### Phase 3: Integration & Demo (Week 5-6)
1. `src/bridge.ts` — WhatsApp/Telegram messaging interface
2. `src/scheduler.ts` — Cron + event scheduling
3. `src/mcp.ts` — MCP client for external tool discovery
4. Polish the viral demo: agent writes WhatsApp + smart home integrations live
5. **Milestone**: Full working agent, ready for demo video

### Phase 4: Launch (Week 7-8)
1. Beautiful README with GIF/video demos
2. One-command install script
3. Record the 60-second viral demo video
4. Launch on GitHub, Twitter/X, Hacker News

## What Makes OpenForgyn Different (The Pitch)

**vs OpenClaw**: 250x smaller. No skill registry (no supply chain attacks).
Self-writing capabilities. Fully auditable. Secure by architecture.

**vs NanoClaw**: Multi-model (not Claude-only). Self-GENERATING skills (not
pre-written instructions). Broader capability (home automation, MCP, A2A).

**vs ZeroClaw/PicoClaw**: Higher capability. AI-native architecture.
Self-evolving. Not just "small" but "intelligently growing."

**The one-liner**: "An ultra-small AI agent that forges its own capabilities.
No plugins. No registry. No supply chain. It forges itself."

## Important Constraints

1. **~1500 line budget for core.** This is sacred. Do NOT exceed it.
   Count your lines. If you're over, refactor and simplify.

2. **No config files for features.** Follow NanoClaw's philosophy:
   configuration happens through code modification, not config files.

3. **Security is architectural, not bolted on.** The skill directory
   is the only writable area. Container isolation is mandatory.
   Permissions are explicit. Audit trail is automatic.

4. **The codebase must be readable in one sitting.** A developer should
   be able to read and understand the ENTIRE core in under 20 minutes.
   Comment sparingly. Name things clearly. Keep functions short.

5. **Multi-model from day one.** Never hardcode a model provider.
   Use an adapter pattern so ANY model works.

6. **The first user experience must be magical.** `npx openforgyn` should
   get you to a working agent in under 2 minutes. The first thing it
   does is write a skill to prove it can. That's the "holy shit" moment.

## Naming Convention

- **OpenForgyn** — The open-source project name
- **Forgyn** — The agent itself (what users interact with)
- **openforgyn.com** — Project website (domain to be purchased)
- Usage: "I deployed OpenForgyn" / "Forgyn wrote a new skill" / "Ask Forgyn to..."

## Competitive Landscape (Know This)

- **OpenClaw**: 190K+ stars, security crisis, OpenAI acqui-hired the creator
- **NanoClaw**: 10K stars, ~500 lines, Claude-only, skills-as-instructions
- **ZeroClaw**: ~15K stars, Rust, security-focused, minimal
- **PicoClaw**: ~17K stars, Go, runs on $10 hardware
- **Nanobot**: ~11K stars, Python, lightweight
- **Goose**: 30K stars, Rust, enterprise-grade, MCP-native

Our positioning: **Gen 3 Claw**. The one that makes skill registries obsolete.

## Tone & Brand

- **Not corporate.** This is an open-source project by a builder, not a product.
- **Confident but not arrogant.** "We think skill registries are the wrong model" not "OpenClaw sucks."
- **Technical depth.** The README should make senior engineers nod.
- **Slightly provocative.** "Your agent should trust only itself."
