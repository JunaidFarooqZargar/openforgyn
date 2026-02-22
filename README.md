# OpenForgyn

**An ultra-small AI agent that forges its own capabilities.**
No plugins. No registry. No supply chain. It writes its own code.

You describe what you need. Forgyn writes the skill, tests it in a
sandboxed container, asks your permission, and deploys it to itself.

```
You:    "Remind me about meetings 15 minutes before they start"
Forgyn: Built calendar + notification skills. Tested. Deployed.
        I'll check every 5 minutes and notify you.
        Here's what I wrote: [shows code]
```

## Why

AI agents need capabilities. The current approach — download skills
from a registry — has the same supply chain problem as npm, except
worse: 11.9% of OpenClaw's skill registry was malware.

Forgyn takes a different approach: **the agent writes its own skills
from scratch using LLMs**. No registry. No downloads. No supply chain.
Every skill is generated, tested in a sandbox, and audited.

## Quick Start

```bash
# Install
pip install openforgyn

# Set your model (any provider works)
export FORGYN_MODEL=openai/gpt-4o
export OPENAI_API_KEY=sk-...

# Run
forgyn
```

Forgyn also works with Anthropic, Google Gemini, Ollama (local), or
any OpenAI-compatible API.

```bash
# Anthropic
export FORGYN_MODEL=anthropic/claude-sonnet-4-20250514
export ANTHROPIC_API_KEY=sk-ant-...

# Local via Ollama (no API key needed)
export FORGYN_MODEL=ollama/llama3

# Google Gemini
export FORGYN_MODEL=gemini/gemini-2.5-pro
export GEMINI_API_KEY=...
```

## How It Works

```
User describes a need
    |
Reasoner detects missing capability
    |
Skill Writer generates: manifest + handler.py + tests
    |
Sandbox runs tests in Docker container (no network, memory-limited)
    |--- fail --> LLM fixes code, retry (up to 3x)
    |--- pass --> continue
    |
Permission Gate asks user to approve capabilities
    |
Deploy to skills/ directory
    |
Audit Logger commits to git (full history, rollback)
    |
Skill is now a tool the Reasoner can call
```

## Architecture

```
+---------------------------------------------+
|  CORE RUNTIME (1,813 lines, read-only)      |
|                                             |
|  Reasoner    Scheduler    Skill Writer      |
|  Sandbox     Audit        Permissions       |
|  MCP Client  Bridge       DB    Models      |
+---------------------------------------------+
         | writes to / reads from
         v
+---------------------------------------------+
|  SKILLS DIRECTORY (agent-written, mutable)  |
|  skills/echo/handler.py                     |
|  skills/echo/test_handler.py                |
|  skills/echo/manifest.json                  |
+---------------------------------------------+
         | runs inside
         v
+---------------------------------------------+
|  DOCKER SANDBOX (isolated, ephemeral)       |
+---------------------------------------------+
```

**13 modules. ~1,800 lines. Fits in one AI context window.**

The core is immutable. Skills are the only mutable part. Every change
is a git commit with full rollback.

## CLI Commands

```bash
forgyn                     # Interactive chat
forgyn skills              # List installed skills
forgyn doctor              # Check system health
forgyn history             # Show recent conversations
forgyn rollback <skill> <commit>  # Revert a skill
forgyn --model ollama/llama3      # Override model
forgyn --version           # Print version
```

## Skill Anatomy

Every skill has three files:

**manifest.json** — declares permissions and dependencies
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

**handler.py** — the skill code (one function)
```python
async def run(args: dict) -> dict:
    city = args.get("city", "London")
    # ... fetch weather ...
    return {"result": "Sunny, 72F", "error": None}
```

**test_handler.py** — tests (must pass in sandbox before deploy)
```python
@pytest.mark.asyncio
async def test_weather():
    result = await run({"city": "London"})
    assert result["error"] is None
```

## Permissions

Skills declare what they need. You approve or deny.

| Permission | Meaning |
|---|---|
| `network` | Make HTTP requests |
| `env:VAR_NAME` | Access an environment variable |
| `filesystem:/path` | Read/write a specific path |
| `schedule:cron` | Register recurring execution |
| `exec` | Run shell commands |

No permissions = auto-approved. The sandbox enforces isolation: no
network by default, 256MB memory limit, 1 CPU, non-root user.

## Development

```bash
# Clone and install
git clone https://github.com/junaidfarooq/openforgyn.git
cd openforgyn
uv sync --all-extras

# Run tests
uv run pytest                    # All tests (needs Docker)
uv run pytest -m "not docker"   # Skip Docker tests

# Lint
uv run ruff check forgyn/ tests/
```

Requires Python 3.12+ and Docker for sandbox tests.

## The Three Generations

| Gen | Project | How skills work | Problem |
|---|---|---|---|
| 1 | OpenClaw | Download from registry | Supply chain attacks (11.9% malware) |
| 2 | NanoClaw | Pre-written instructions | Bounded by what humans write |
| **3** | **OpenForgyn** | **Agent writes from scratch** | **This repo** |

## License

MIT
