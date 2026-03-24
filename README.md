<p align="center">
  <h1 align="center">OpenForgyn</h1>
  <p align="center">
    <strong>The agent that writes its own soul.</strong><br>
    No plugins. No skill registry. No supply chain. It forges itself.
  </p>
  <p align="center">
    <code>~3,200 lines</code> &nbsp;&middot;&nbsp; <code>14 modules</code> &nbsp;&middot;&nbsp; <code>198 tests</code> &nbsp;&middot;&nbsp; <code>any LLM</code>
  </p>
</p>

---

> *"I'm definitely a bit sus'd to run OpenClaw specifically — giving my private data/keys to 400K lines of vibe coded monster that is being actively attacked at scale is not very appealing at all."*
>
> — **Andrej Karpathy**

He asked for something small enough to fit in his head. We built something small enough to fit in the AI's head too — **and it writes its own capabilities from scratch.**

---

```
You:    "What's the weather in Tokyo?"

Forgyn: I don't have a weather skill yet. Building one now.

        [1/7] Generating manifest...
        [2/7] Requesting permissions...

        Skill 'weather' wants:
          - network: Make HTTP requests
        Allow? [y/n] y

        [3/7] Researching APIs...
        [4/7] Writing handler.py...
        [5/7] Writing tests...
        [6/7] Running tests in sandbox... passed
        [7/7] Smoke testing output quality...

        Deployed. Committed to audit log.

        Tokyo: 14.9°C, scattered clouds, humidity 58%, wind 3.6 m/s.
```

That's not a demo. That's what actually happens. The agent wrote Python code, tested it in a Docker container, asked for your permission, deployed it, and called it — all in one turn.

## Why This Exists

Karpathy called them **Claws** — personal AI agents that act in the real world. The first wave proved the category. It also proved the model is broken:

- **OpenClaw**: 190K+ stars, 400K+ lines of vibe-coded TypeScript. Its skill registry had **11.9% malware**. 40,000+ exposed instances. RCE vulnerabilities. OpenAI acqui-hired the creator.
- **NanoClaw**: ~500 lines, elegant, but Claude-only and limited to pre-written skill instructions humans have to write.

The root cause: **skill registries are the new npm, except the packages run with your API keys, your files, and your home network.** A malicious skill doesn't just crash your app — it exfiltrates your life.

OpenForgyn eliminates the registry entirely. The agent writes its own code. You approve what it can do. Nothing is downloaded. Nothing is trusted blindly.

## Quick Start

```bash
pip install openforgyn
```

```bash
# Pick any LLM provider
export FORGYN_MODEL=openai/gpt-4o
export OPENAI_API_KEY=sk-...

# Start — Forgyn introduces itself and builds its first skill live
forgyn
```

Works with **any provider**:

| Provider | Config |
|---|---|
| OpenAI | `FORGYN_MODEL=openai/gpt-4o` |
| Anthropic | `FORGYN_MODEL=anthropic/claude-sonnet-4-20250514` |
| Google Gemini | `FORGYN_MODEL=gemini/gemini-2.5-pro` |
| Ollama (local) | `FORGYN_MODEL=ollama/llama3` |
| Any OpenAI-compatible | `FORGYN_MODEL=custom/model-name` + `OPENAI_API_KEY` + `OPENAI_BASE_URL` |

Requires Python 3.12+ and Docker.

## How It Works

```
 You: "I need a weather checker"
  |
  v
+------------------+
|    REASONER      |  "I don't have a weather skill. Building one."
+------------------+
  |
  v
+------------------+
|  SKILL WRITER    |  Researches the API, then generates:
|                  |  manifest.json + handler.py + test_handler.py
+------------------+
  |
  v
+------------------+
|    SANDBOX       |  Docker container: memory-limited, non-root, ephemeral.
|                  |  Runs pytest. Pass? Continue. Fail? LLM fixes + retry (3x).
+------------------+
  |
  v
+------------------+
|  SMOKE TEST      |  LLM reviews: is this real code or placeholder stubs?
+------------------+
  |
  v
+------------------+
|  PERMISSIONS     |  "Skill wants: network access. Allow? [y/n]"
+------------------+
  |
  v
+------------------+
|  DEPLOY + AUDIT  |  Files written to skills/. Dependencies installed.
|                  |  Git commit. Full history. Full rollback.
+------------------+
  |
  v
  Skill is now a tool the Reasoner can call.
  Next time you ask about weather, it just works.
```

## First Run

On a fresh install, Forgyn introduces itself and immediately proves it can write code:

```
$ forgyn

First run detected — Forgyn will introduce itself.

I'm Forgyn, an AI agent that forges its own capabilities by writing
new skills when needed. Let me prove it.

Built a system_info skill and called it:

  hostname:       macbook-pro.local
  os:             Darwin 25.2.0
  python_version: 3.12.9
  current_time:   2026-03-24T14:06:51+00:00

>
```

That's the entire onboarding. No config wizard. No tutorial. The agent builds a skill and runs it.

## Telegram Bot

Message your agent from your phone:

```bash
export TELEGRAM_BOT_TOKEN=<token-from-BotFather>
forgyn telegram
```

Forgyn connects via long-polling (no webhooks, no exposed ports) and responds to messages. Skills, memory, and scheduling all work through Telegram. Reminders set via Telegram are delivered back to Telegram.

## Architecture

```
+-------------------------------------------------------+
|  CORE RUNTIME  (~3,200 lines of Python, immutable)    |
|                                                       |
|  reasoner.py ---- Main LLM loop, tool dispatch        |
|  skill_writer.py  THE innovation: generates skills    |
|  sandbox.py ----- Docker isolation for skill tests    |
|  permissions.py - Capability-based approval gate      |
|  audit.py ------- Git-based change tracking           |
|  scheduler.py --- Cron + interval task scheduling     |
|  bridge.py ------ External messaging channels         |
|  telegram.py ---- Telegram Bot API (long-polling)     |
|  mcp_client.py -- MCP server discovery + tool use     |
|  models.py ------ Multi-provider LLM adapter          |
|  db.py ---------- SQLite persistence                  |
|  cli.py --------- CLI + first-run experience          |
+-------------------------------------------------------+
        |            |
        | writes     | reads
        v            v
+-------------------------------------------------------+
|  skills/  (agent-written, the only mutable part)      |
|                                                       |
|  skills/weather/handler.py                            |
|  skills/weather/test_handler.py                       |
|  skills/weather/manifest.json                         |
+-------------------------------------------------------+
        |
        | tested inside
        v
+-------------------------------------------------------+
|  DOCKER SANDBOX  (ephemeral, isolated containers)     |
+-------------------------------------------------------+
```

The entire core fits in one AI context window. A human can read it in 20 minutes.

## Skill Anatomy

Every agent-written skill has exactly three files:

**`manifest.json`** — what it needs
```json
{
  "name": "weather",
  "description": "Get current weather for a city",
  "parameters": {
    "type": "object",
    "properties": {
      "city": { "type": "string", "description": "City name" }
    },
    "required": ["city"]
  },
  "permissions": ["network"],
  "dependencies": ["requests"],
  "entry_point": "handler.py",
  "test_file": "test_handler.py"
}
```

**`handler.py`** — what it does (one function, one contract)
```python
import os
import requests

async def run(args: dict) -> dict:
    city = args.get("city", "London")
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
    resp = requests.get(
        f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}"
    )
    data = resp.json()
    return {"result": {"city": data["name"], "temp": data["main"]["temp"]}, "error": None}
```

**`test_handler.py`** — proof it works (must pass before deploy)
```python
import pytest
from unittest.mock import patch, MagicMock
from handler import run

@pytest.mark.asyncio
async def test_weather_success():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"name": "London", "main": {"temp": 288.15}}
    with patch("handler.requests.get", return_value=mock_resp):
        result = await run({"city": "London"})
    assert result["error"] is None
    assert result["result"]["city"] == "London"
```

## Security Model

Security isn't a feature. It's the architecture.

| Layer | What it does |
|---|---|
| **Sandbox** | Skills test in Docker: memory-limited, `nobody` user, killed on timeout |
| **Smoke test** | LLM reviews generated code for stubs, placeholders, and fake data before deploy |
| **Permissions** | Skills declare what they need. You approve or deny. No permission = no access |
| **Immutable core** | The agent can only write to `skills/`. Core runtime is read-only |
| **Audit trail** | Every skill deployment is a git commit. Full diff. Full rollback |
| **No registry** | No downloads from the internet. No supply chain. Code is generated, not fetched |
| **Dep install** | Dependencies are installed via `uv`/`pip` only after sandbox tests pass and you approve |

### Permission Types

| Permission | What it grants |
|---|---|
| `network` | HTTP requests to the internet |
| `env:VAR_NAME` | Read a specific environment variable |
| `filesystem:/path` | Read/write a specific filesystem path |
| `schedule:cron` | Register a recurring task |
| `exec` | Execute shell commands |

Skills with no permissions are auto-approved. Everything else requires explicit consent.

## Built-in Capabilities

These are infrastructure — not skills. They exist because the self-writing loop needs them:

- **Web search** — via OpenAI/Anthropic native APIs. The skill writer uses this to research APIs before generating code.
- **Long-term memory** — semantic retrieval with embeddings. Persists across conversations. The agent remembers who you are, your preferences, and your context.
- **Scheduling** — cron, interval, and one-shot. Cross-channel: a reminder set in Telegram fires in Telegram.
- **Skill modification** — `modify_skill` tool lets the agent rewrite existing skills based on feedback.

Everything else is a skill. Browser automation? Skill. Voice? Skill. Email? Skill. Home automation? Skill.

## CLI

```bash
forgyn                              # Interactive chat (first run = live demo)
forgyn telegram                     # Run as a Telegram bot
forgyn --model ollama/llama3        # Use a specific model
forgyn --code-model openai/o3       # Stronger model for code generation
forgyn --yes                        # Auto-approve permissions (CI/scripting)
forgyn -c <conversation-id>         # Resume a conversation

forgyn skills                       # List installed skills
forgyn memories                     # List stored memories
forgyn doctor                       # Check Python, Docker, API keys
forgyn history                      # Recent conversations
forgyn rollback <skill> <commit>    # Revert a skill to a previous version
```

## Development

```bash
git clone https://github.com/JunaidFarooqZargar/openforgyn.git
cd openforgyn
uv sync --all-extras

# Tests (198 total)
uv run pytest                       # Full suite (needs Docker)
uv run pytest -m "not docker"       # Without Docker
uv run ruff check forgyn/ tests/    # Lint
```

### Project Structure

```
openforgyn/
  forgyn/
    __init__.py ........     3 lines
    __main__.py ........     5 lines
    reasoner.py ........   560 lines   Main LLM loop + tool dispatch
    models.py ..........   565 lines   Multi-provider LLM adapter
    skill_writer.py ....   442 lines   Self-writing engine
    cli.py .............   430 lines   CLI + Telegram subcommand + first-run
    db.py ..............   359 lines   SQLite persistence + migrations
    sandbox.py .........   214 lines   Docker container runner
    telegram.py ........   154 lines   Telegram Bot API channel
    scheduler.py .......   126 lines   Cron/interval scheduling
    permissions.py .....   117 lines   Capability approval gate
    bridge.py ..........   107 lines   Messaging channel bridge
    audit.py ...........    95 lines   Git-based audit logging
    mcp_client.py ......    94 lines   MCP protocol client
                           ─────────
                           3,271 lines total
  skills/ ................ agent-written (starts empty)
  tests/ ................. 198 tests across 18 test files
  docker/
    skill.Dockerfile ..... base image for sandbox
```

## The Three Generations

| | Gen 1 | Gen 2 | Gen 3 |
|---|---|---|---|
| **Project** | OpenClaw | NanoClaw | **OpenForgyn** |
| **How skills work** | Download from registry | Pre-written instructions | Agent writes from scratch |
| **Core size** | 400K+ lines | ~500 lines | ~3,200 lines |
| **Language** | TypeScript | TypeScript | Python |
| **LLM support** | Multi-model | Claude only | Any provider |
| **Security** | Registry (11.9% malware) | Code modification | Sandbox + permissions + audit |
| **Messaging** | Multi-channel | WhatsApp | CLI + Telegram |
| **Skill growth** | Limited by registry | Limited by humans | Unlimited |

## Design Principles

1. **The core must be readable in one sitting.** ~3,200 lines. 14 files. A developer can understand the entire system in 20 minutes. An AI can read the whole thing in a single context window.

2. **Skills are the only mutable part.** The core runtime is frozen. The agent can only write to `skills/`. This is the security boundary.

3. **Every skill has tests.** No code deploys without passing pytest in a sandboxed container. The LLM writes the tests too. If tests fail, it fixes the code and retries.

4. **Permissions are explicit.** No ambient authority. A skill that needs network access must declare it, and you must approve it.

5. **Full audit trail.** The skills directory is its own git repo. Every change is a commit. You can diff any skill, see its full history, and rollback to any version.

6. **Any model works.** OpenAI, Anthropic, Google, Ollama, or any OpenAI-compatible endpoint. No vendor lock-in.

7. **Should this be in core, or should Forgyn write it?** If the agent can write it as a skill, it must not be in core. Every feature baked into core is a missed opportunity to demonstrate the thesis.

## Author

**[Dr. Junaid Farooq](https://www.junaidfarooq.net)** — AI researcher and builder.

## License

[MIT](LICENSE)
