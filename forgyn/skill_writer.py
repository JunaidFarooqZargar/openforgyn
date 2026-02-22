"""Self-writing engine: generates skills from scratch using LLMs."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

from forgyn.audit import commit_skill, init_skills_repo
from forgyn.db import save_skill
from forgyn.models import Message, ModelConfig, ToolDef, chat, web_search
from forgyn.permissions import PermissionRequest, request_permissions, validate_manifest
from forgyn.sandbox import run_in_sandbox

MAX_RETRIES = 3

# Injected into every skill directory so pytest-asyncio works in the sandbox.
CONFTEST_PY = """\
import pytest_asyncio  # noqa: F401

def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker("asyncio") is None:
            import asyncio, inspect
            if inspect.iscoroutinefunction(item.obj):
                item.add_marker(pytest.mark.asyncio)

pytest_plugins = ["pytest_asyncio"]
"""

PYTEST_INI = """\
[pytest]
asyncio_mode = auto
"""


@dataclass
class SkillWriteResult:
    success: bool
    skill_name: str
    manifest: dict = field(default_factory=dict)
    error: str = ""


MANIFEST_PROMPT = """\
Generate a JSON manifest for a Python skill with these details:
- Name: {name}
- Purpose: {description}

The manifest must be a JSON object with these fields:
- "name": string (the skill name)
- "description": string (what the skill does, generically)
- "parameters": object (JSON Schema for the arguments the skill's run() function accepts), e.g.:
  {{"type": "object", "properties": {{"city": {{"type": "string", "description": "City name"}}}}, "required": ["city"]}}
- "permissions": list of strings. Valid permissions:
  - "network" (if the skill needs HTTP access)
  - "env:VAR_NAME" (for each environment variable needed)
  - "schedule:cron" or "schedule:interval" (if it needs scheduling)
  - "exec" (if it needs to run shell commands)
- "dependencies": list of pip package names needed (e.g. ["requests"])
- "entry_point": "handler.py"
- "test_file": "test_handler.py"

IMPORTANT: The skill must be GENERIC and REUSABLE. Use parameters for any variable
input (cities, languages, amounts, etc). Never hardcode specific values.

Respond with ONLY the JSON object, no markdown fencing, no explanation."""

HANDLER_PROMPT = """\
Write a Python file `handler.py` for a skill that: {description}

Requirements:
- Must define exactly one function: `async def run(args: dict) -> dict`
- The function receives arguments as a dict and returns a dict
- Return format: {{"result": "...", "error": null}} on success
- Return format: {{"result": null, "error": "..."}} on failure
- Available dependencies: {dependencies}
- The skill MUST be generic and reusable. Accept parameters from the args dict
  for any variable input (e.g. city name, language, amount). NEVER hardcode
  specific values like city names, URLs with specific queries, etc.
- Read any needed API keys from environment variables using os.environ.get()
- Keep it simple and focused
- NEVER generate placeholder, stub, or simulated data. If you cannot implement
  the real functionality, return {{"result": null, "error": "Cannot implement: <reason>"}}
  instead of faking results with sample/dummy data.

Respond with ONLY the Python code, no markdown fencing, no explanation."""

TEST_PROMPT = """\
Write pytest tests for this Python handler:

```python
{handler_code}
```

Requirements:
- File name: test_handler.py
- Import the handler with: from handler import run
- Use `pytest.mark.asyncio` on async tests
- Test at least: one success case, one edge/error case
- Mock any external API calls or network requests
- Keep tests focused and simple
- Tests must validate REAL behavior, not placeholder/stub output. Never assert
  on strings like "Sample", "placeholder", or fabricated data.

Respond with ONLY the Python code, no markdown fencing, no explanation."""


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the LLM included them despite instructions."""
    text = text.strip()
    if text.startswith("```"):
        # Remove first line (```python or ```)
        lines = text.split("\n")
        lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    text = _strip_code_fences(text)
    return json.loads(text)


class SkillWriter:
    """Generates skills from scratch: manifest + handler + tests."""

    def __init__(
        self,
        model_config: ModelConfig,
        db: sqlite3.Connection,
        skills_dir: Path,
        permission_prompt_fn=None,
        code_model_config: ModelConfig | None = None,
    ):
        self.model_config = model_config
        self.code_model = code_model_config or model_config
        self.db = db
        self.skills_dir = skills_dir
        self.permission_prompt_fn = permission_prompt_fn

    async def write_skill(
        self,
        name: str,
        description: str,
        user_context: str = "",
    ) -> SkillWriteResult:
        """Generate, test, and deploy a new skill."""
        init_skills_repo(self.skills_dir)

        # Step 1: Generate manifest
        log.info("[1/5] Generating manifest for skill '%s'...", name)
        manifest = await self._generate_manifest(name, description)
        errors = validate_manifest(manifest)
        if errors:
            return SkillWriteResult(
                success=False, skill_name=name,
                error=f"Invalid manifest: {'; '.join(errors)}",
            )

        log.info("[2/5] Requesting permissions...")
        # Step 2: Request permissions
        approved = await request_permissions(
            PermissionRequest(
                skill_name=name,
                permissions=manifest.get("permissions", []),
                description=manifest.get("description", description),
            ),
            prompt_fn=self.permission_prompt_fn,
        )
        if not approved:
            return SkillWriteResult(
                success=False, skill_name=name,
                error="User denied permissions",
            )

        # Step 3: Research APIs if web search is available
        log.info("[3/6] Researching APIs...")
        research = await self._research(description)

        # Step 4: Generate handler + tests, test in sandbox, iterate
        log.info("[4/6] Writing handler.py...")
        deps = manifest.get("dependencies", [])
        handler_code = await self._generate_handler(name, description, deps, research=research)
        log.info("[5/6] Writing tests...")
        test_code = await self._generate_tests(name, description, handler_code)

        # Write to temp skill dir and test
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        log.info("[6/7] Running tests in sandbox...")
        passed = await self._test_and_iterate(
            skill_dir, manifest, handler_code, test_code, description, deps,
        )
        if not passed:
            return SkillWriteResult(
                success=False, skill_name=name,
                error="Tests failed after maximum retries",
            )

        # Step 7: Smoke test — run once and validate output isn't stub/fake
        log.info("[7/7] Smoke testing output quality...")
        smoke_ok = await self._smoke_test(name, description, handler_code, manifest)
        if not smoke_ok:
            return SkillWriteResult(
                success=False, skill_name=name,
                error="Smoke test failed: output appears to be placeholder/stub data.",
            )

        # Deploy — files are already written, save to DB and commit
        save_skill(self.db, name, manifest)
        commit_skill(
            self.skills_dir, name,
            f"Add skill: {name} — {description}",
        )

        return SkillWriteResult(success=True, skill_name=name, manifest=manifest)

    async def modify_skill(self, name: str, feedback: str) -> SkillWriteResult:
        """Modify an existing skill based on user feedback, then re-test."""
        init_skills_repo(self.skills_dir)
        skill_dir = self.skills_dir / name

        handler_path = skill_dir / "handler.py"
        manifest_path = skill_dir / "manifest.json"
        if not handler_path.is_file():
            return SkillWriteResult(success=False, skill_name=name, error=f"Skill '{name}' not found.")

        old_handler = handler_path.read_text()
        manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
        deps = manifest.get("dependencies", [])

        log.info("[1/3] Rewriting handler for skill '%s'...", name)
        prompt = (
            f"This is the handler.py for a skill called '{name}':\n\n"
            f"```python\n{old_handler}\n```\n\n"
            f"The user wants this change: {feedback}\n\n"
            f"Rewrite handler.py to address the feedback. Keep the same `async def run(args: dict) -> dict` "
            f"signature. Available dependencies: {', '.join(deps) if deps else 'standard library only'}.\n"
            f"Respond with ONLY the fixed Python code, no markdown fencing."
        )
        response = await chat(self.code_model, [Message(role="user", content=prompt)], temperature=0.3)
        new_handler = _strip_code_fences(response.content or old_handler)

        log.info("[2/3] Regenerating tests...")
        test_code = await self._generate_tests(name, feedback, new_handler)

        log.info("[3/3] Running tests in sandbox...")
        passed = await self._test_and_iterate(skill_dir, manifest, new_handler, test_code, feedback, deps)
        if not passed:
            # Restore original handler on failure
            handler_path.write_text(old_handler)
            return SkillWriteResult(success=False, skill_name=name, error="Tests failed after retries.")

        save_skill(self.db, name, manifest)
        commit_skill(self.skills_dir, name, f"Modify skill: {name} — {feedback[:80]}")
        return SkillWriteResult(success=True, skill_name=name, manifest=manifest)

    async def _smoke_test(self, name: str, description: str, handler_code: str, manifest: dict) -> bool:
        """Ask the LLM to evaluate if the handler code produces real or stub output."""
        prompt = (
            f"You are a code reviewer. This handler.py is for a skill called '{name}' "
            f"that should: {description}\n\n"
            f"```python\n{handler_code}\n```\n\n"
            f"Does this code implement REAL functionality, or does it return placeholder/"
            f"stub/simulated/fabricated data (e.g. 'Sample Title', hardcoded fake results, "
            f"lorem ipsum, dummy data)?\n\n"
            f"Respond with ONLY 'REAL' or 'STUB' followed by a one-sentence explanation."
        )
        response = await chat(
            self.code_model,
            [Message(role="user", content=prompt)],
            temperature=0.0,
        )
        verdict = (response.content or "").strip().upper()
        is_real = verdict.startswith("REAL")
        if not is_real:
            log.warning("  Smoke test failed: %s", response.content)
        return is_real

    async def _generate_manifest(self, name: str, description: str) -> dict:
        """Ask the LLM to generate a skill manifest."""
        prompt = MANIFEST_PROMPT.format(name=name, description=description)
        response = await chat(
            self.code_model,
            [Message(role="user", content=prompt)],
            temperature=0.3,
        )
        return _extract_json(response.content or "{}")

    async def _research(self, description: str) -> str:
        """Search the web for API documentation relevant to the skill."""
        query = f"Python API documentation for: {description}"
        result = await web_search(self.model_config, query)
        if result.get("error") or not result.get("summary"):
            return ""
        summary = result["summary"]
        sources = result.get("sources", [])
        if sources:
            refs = "\n".join(f"- {s['title']}: {s['url']}" for s in sources[:3])
            return f"{summary}\n\nReferences:\n{refs}"
        return summary

    async def _generate_handler(self, name: str, description: str, deps: list[str], research: str = "") -> str:
        """Ask the LLM to generate the skill handler code."""
        prompt = HANDLER_PROMPT.format(
            description=description,
            dependencies=", ".join(deps) if deps else "standard library only",
        )
        if research:
            prompt += f"\n\nHere is relevant API documentation from a web search:\n{research}"
        response = await chat(
            self.code_model,
            [Message(role="user", content=prompt)],
            temperature=0.3,
        )
        return _strip_code_fences(response.content or "")

    async def _generate_tests(self, name: str, description: str, handler_code: str) -> str:
        """Ask the LLM to generate tests for the handler."""
        prompt = TEST_PROMPT.format(handler_code=handler_code)
        response = await chat(
            self.code_model,
            [Message(role="user", content=prompt)],
            temperature=0.3,
        )
        return _strip_code_fences(response.content or "")

    async def _test_and_iterate(
        self,
        skill_dir: Path,
        manifest: dict,
        handler_code: str,
        test_code: str,
        description: str,
        deps: list[str],
    ) -> bool:
        """Write files, run tests in sandbox, retry on failure."""
        needs_network = "network" in manifest.get("permissions", [])

        for attempt in range(MAX_RETRIES):
            # Write current code to disk
            (skill_dir / "handler.py").write_text(handler_code)
            (skill_dir / "test_handler.py").write_text(test_code)
            (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
            # Ensure pytest-asyncio is configured in the sandbox
            (skill_dir / "conftest.py").write_text(CONFTEST_PY)
            (skill_dir / "pytest.ini").write_text(PYTEST_INI)

            # Run tests in sandbox
            result = await run_in_sandbox(
                skill_dir=skill_dir,
                timeout=60,
                network=needs_network,
                install_deps=deps if deps else None,
            )

            if result.exit_code == 0:
                log.info("  Tests passed on attempt %d.", attempt + 1)
                return True

            # Tests failed — ask LLM to fix the handler
            error_output = result.stdout + "\n" + result.stderr
            log.warning("  Attempt %d failed (exit code %d):\n%s", attempt + 1, result.exit_code, error_output[-1000:])
            if attempt < MAX_RETRIES - 1:
                log.info("  Asking LLM to fix handler (retry %d/%d)...", attempt + 2, MAX_RETRIES)
                handler_code = await self._fix_handler(
                    description, handler_code, test_code, error_output, deps,
                )

        return False

    async def _fix_handler(
        self,
        description: str,
        handler_code: str,
        test_code: str,
        error_output: str,
        deps: list[str],
    ) -> str:
        """Ask the LLM to fix a handler based on test failure output."""
        prompt = (
            f"This handler for a skill that '{description}' failed its tests.\n\n"
            f"Handler code:\n```python\n{handler_code}\n```\n\n"
            f"Test code:\n```python\n{test_code}\n```\n\n"
            f"Error output:\n```\n{error_output[-2000:]}\n```\n\n"
            f"Fix the handler.py code so the tests pass. "
            f"Available dependencies: {', '.join(deps) if deps else 'standard library only'}. "
            f"Respond with ONLY the fixed Python code, no markdown fencing."
        )
        response = await chat(
            self.code_model,
            [Message(role="user", content=prompt)],
            temperature=0.3,
        )
        return _strip_code_fences(response.content or handler_code)

    def make_tool_def(self, name: str, manifest: dict) -> ToolDef:
        """Create a ToolDef for a deployed skill so the reasoner can call it."""
        return ToolDef(
            name=f"skill_{name}",
            description=manifest.get("description", f"Run the {name} skill"),
            parameters=manifest.get("parameters", {
                "type": "object",
                "properties": {},
            }),
        )
