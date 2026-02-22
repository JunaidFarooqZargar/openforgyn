<p align="center">
  <h1 align="center">OpenForgyn</h1>
  <p align="center">
    <strong>A personal AI agent that writes its own capabilities from scratch.</strong><br>
    No plugins. No skill registry. No supply chain. It forges itself.
  </p>
  <p align="center">
    <code>~1,900 lines</code> &nbsp;&middot;&nbsp; <code>13 modules</code> &nbsp;&middot;&nbsp; <code>119 tests</code> &nbsp;&middot;&nbsp; <code>any LLM</code>
  </p>
</p>

---

```
You:    "Remind me about meetings 15 minutes before they start"

Forgyn: I don't have calendar or notification skills yet. Building them now.

        [1/5] Generating manifest...
        [2/5] Writing handler.py...
        [3/5] Writing tests...
        [4/5] Running tests in sandbox... 3/3 passed
        [5/5] Requesting permissions...

        Skill 'calendar' wants:
          - network: Make HTTP requests
          - env:GOOGLE_CALENDAR_API_KEY: Access environment variable
          - schedule:cron: Register recurring execution
        Allow? [y/n] y

        Deployed. Committed to audit log.
        I'll check every 5 minutes and notify you before meetings.
```

## The Problem

Andrej Karpathy called them **Claws** — personal AI agents that act in the real
world through tool calls, scheduling, and persistence.

The first wave proved the category. It also proved the security model is broken:

- **OpenClaw**: 190K+ stars, 400K+ lines of vibe-coded TypeScript, 11.9% of its
  skill registry was malware, 40,000+ exposed instances, RCE vulnerabilities
- **NanoClaw**: ~500 lines, secure by design, but Claude-only and limited to
  pre-written skill instructions

Karpathy on OpenClaw:

> *"I'm definitely a bit sus'd to run OpenClaw specifically — giving my private
> data/keys to 400K lines of vibe coded monster that is being actively attacked
> at scale is not very appealing at all."*

The root cause: **skill registries are the new npm, except 10x worse.** Agents
run with your API keys, your files, your home network. A malicious skill doesn't
just crash your app — it exfiltrates your life.

## The Fix

**Don't download skills. Write them.**

OpenForgyn is a Generation 3 Claw. The agent writes its own capabilities from
scratch using LLMs. Every skill is:

1. **Generated** — LLM writes handler + tests + manifest
2. **Tested** — runs in a Docker sandbox (no network, memory-limited, non-root)
3. **Approved** — you see exactly what permissions it needs and say yes or no
4. **Audited** — every deployment is a git commit with full rollback

No registry. No downloads. No supply chain. The only code that runs is code
the agent wrote and you approved.

## Quick Start

```bash
pip install openforgyn
```

```bash
# Pick any LLM provider
export FORGYN_MODEL=openai/gpt-4o
export OPENAI_API_KEY=sk-...

# Start
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
|  SKILL WRITER    |  LLM generates: manifest.json + handler.py + test_handler.py
+------------------+
  |
  v
+------------------+
|    SANDBOX       |  Docker container: no network, 256MB, 1 CPU, non-root
|                  |  Runs pytest. Pass? Continue. Fail? LLM fixes + retry (3x).
+------------------+
  |
  v
+------------------+
|  PERMISSIONS     |  "Skill wants: network access. Allow? [y/n]"
+------------------+
  |
  v
+------------------+
|  DEPLOY + AUDIT  |  Files written to skills/. Git commit. Full history.
+------------------+
  |
  v
  Skill is now a tool the Reasoner can call.
  Next time you ask about weather, it just works.
```

## Architecture

```
+-------------------------------------------------------+
|  CORE RUNTIME  (~1,900 lines of Python, immutable)    |
|                                                       |
|  reasoner.py ---- Main LLM loop, tool dispatch        |
|  skill_writer.py  THE innovation: generates skills    |
|  sandbox.py ----- Docker isolation for skill tests    |
|  permissions.py - Capability-based approval gate      |
|  audit.py ------- Git-based change tracking           |
|  scheduler.py --- Cron + interval task scheduling     |
|  bridge.py ------ External messaging channels         |
|  mcp_client.py -- MCP server discovery + tool use     |
|  models.py ------ Multi-provider LLM adapter          |
|  db.py ---------- SQLite persistence                  |
|  cli.py --------- CLI: chat, doctor, skills, history  |
+-------------------------------------------------------+
        |            |
        | writes     | reads
        v            v
+-------------------------------------------------------+
|  skills/  (agent-written, the only mutable part)      |
|                                                       |
|  skills/weather/manifest.json                         |
|  skills/weather/handler.py                            |
|  skills/weather/test_handler.py                       |
|                                                       |
|  skills/calendar/manifest.json                        |
|  skills/calendar/handler.py                           |
|  skills/calendar/test_handler.py                      |
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
  "permissions": ["network"],
  "dependencies": ["httpx"],
  "entry_point": "handler.py",
  "test_file": "test_handler.py"
}
```

**`handler.py`** — what it does (one function, one contract)
```python
async def run(args: dict) -> dict:
    city = args.get("city", "London")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://wttr.in/{city}?format=3")
    return {"result": resp.text.strip(), "error": None}
```

**`test_handler.py`** — proof it works (must pass before deploy)
```python
import pytest
from unittest.mock import AsyncMock, patch
from handler import run

@pytest.mark.asyncio
async def test_weather():
    with patch("handler.httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=type("R", (), {"text": "London: +15°C"})()
        )
        result = await run({"city": "London"})
    assert result["error"] is None
    assert "London" in result["result"]
```

## Security Model

Security isn't bolted on. It's architectural.

| Layer | What it does |
|---|---|
| **Sandbox** | Skills test in Docker: no network by default, 256MB RAM, 1 CPU, `nobody` user, killed on timeout |
| **Permissions** | Skills declare what they need. You approve or deny. No permission = no access. |
| **Immutable core** | The agent can only write to `skills/`. Core runtime is read-only. |
| **Audit trail** | Every skill deployment is a git commit. Full diff. Full rollback. |
| **No registry** | No downloads from the internet. No supply chain. Code is generated, not fetched. |

### Permission Types

| Permission | What it grants |
|---|---|
| `network` | HTTP requests to the internet |
| `env:VAR_NAME` | Read a specific environment variable |
| `filesystem:/path` | Read/write a specific filesystem path |
| `schedule:cron` | Register a recurring task |
| `exec` | Execute shell commands |

Skills with no permissions are auto-approved. Everything else requires explicit consent.

## CLI

```bash
forgyn                              # Interactive chat
forgyn --model ollama/llama3        # Use a specific model
forgyn -c <conversation-id>        # Resume a conversation

forgyn skills                       # List installed skills
forgyn doctor                       # Check Python, Docker, API keys
forgyn history                      # Recent conversations
forgyn rollback <skill> <commit>    # Revert a skill to a previous version

forgyn --version
```

## Development

```bash
git clone https://github.com/junaidfarooq/openforgyn.git
cd openforgyn
uv sync --all-extras

# Tests (119 total)
uv run pytest                       # Full suite (needs Docker)
uv run pytest -m "not docker"       # Without Docker
uv run ruff check forgyn/ tests/    # Lint
```

### Project Structure

```
openforgyn/
  forgyn/
    __init__.py ........   3 lines
    __main__.py ........   5 lines
    db.py ..............  184 lines   SQLite persistence
    models.py ..........  281 lines   Multi-provider LLM adapter
    reasoner.py ........  249 lines   Main LLM loop + tool dispatch
    skill_writer.py ....  277 lines   Self-writing engine
    sandbox.py .........  214 lines   Docker container runner
    cli.py .............  219 lines   CLI commands
    permissions.py .....  117 lines   Capability approval gate
    scheduler.py .......  111 lines   Cron/interval scheduling
    audit.py ...........   95 lines   Git-based audit logging
    mcp_client.py ......   94 lines   MCP protocol client
    bridge.py ..........   68 lines   Messaging channel bridge
                         ─────────
                         1,917 lines total
  skills/ .............. agent-written (starts empty)
  tests/ ............... 119 tests
  docker/
    skill.Dockerfile ... base image for sandbox
```

## The Three Generations of Claws

| | Gen 1 | Gen 2 | Gen 3 |
|---|---|---|---|
| **Project** | OpenClaw | NanoClaw | **OpenForgyn** |
| **How skills work** | Download from registry | Pre-written instructions | Agent writes from scratch |
| **Core size** | 400K+ lines | ~500 lines | ~1,900 lines |
| **Language** | TypeScript | TypeScript | Python |
| **LLM support** | Multi-model | Claude only | Any provider |
| **Security** | Registry (11.9% malware) | Code modification | Sandbox + permissions + audit |
| **Skill growth** | Limited by registry | Limited by humans | Unlimited |

## Design Principles

1. **The core must be readable in one sitting.** ~1,900 lines. 13 files. A developer can understand the entire system in 20 minutes.

2. **Skills are the only mutable part.** The core runtime is frozen. The agent can only write to `skills/`. This is the security boundary.

3. **Every skill has tests.** No code deploys without passing pytest in a sandboxed container. The LLM writes the tests too. If tests fail, it fixes the code and retries.

4. **Permissions are explicit.** No ambient authority. A skill that needs network access must declare it, and you must approve it. The sandbox enforces this.

5. **Full audit trail.** The skills directory is its own git repo. Every change is a commit. You can diff any skill, see its full history, and rollback to any version.

6. **Any model works.** OpenAI, Anthropic, Google, Ollama, or any OpenAI-compatible endpoint. The skill writer uses whatever model you configure. No vendor lock-in.

## License

MIT
