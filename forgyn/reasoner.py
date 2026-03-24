"""Main LLM reasoning loop. Plans actions, calls tools, returns responses."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import math

from forgyn.db import (
    add_message, create_conversation, get_messages, get_memories_with_embeddings,
    list_memories, list_skills, save_memory, delete_memory,
)
from forgyn.models import Message, ModelConfig, ToolDef, chat, embed, web_search

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10

SYSTEM_PROMPT_TEMPLATE = """\
You are Forgyn, a personal AI agent that forges its own capabilities.
Current time: {current_time}

You can write your own skills (Python code) to gain new abilities. When the user
asks you to do something you can't do yet, you can build a skill for it.

Guidelines:
- Be concise and helpful.
- When you have an installed skill that can handle a request, use it.
- When you need a capability you don't have, tell the user you can build it
  and use the write_skill tool.
- Always explain what you built after creating a new skill.
- IMPORTANT: Always create GENERIC, REUSABLE skills. For example, if the user asks
  for the weather in Paris, create a general "weather" skill that accepts any city
  as a parameter — not a "weather_paris" skill. Skills should be like functions:
  parameterized and reusable for different inputs.
- When naming skills, use broad category names (e.g. "weather", "currency_convert",
  "translate") not specific instance names (e.g. "weather_london", "convert_usd_eur").
- You have long-term memory that persists across conversations. You MUST actively
  use it. Saying "I'll remember that" without calling the remember tool means
  you WILL forget it next session.
- Call the remember tool when the user shares anything you would need in a future
  conversation: who they are, what they're working on, how they prefer things,
  facts about their setup. When in doubt, remember it — a forgotten fact costs
  the user more than a redundant memory.
- Do NOT remember: throwaway chit-chat, one-off questions, or things only relevant
  to the current exchange. Do NOT store passwords or raw API keys.
- Categories:
  - "identity" — name, role, location, background
  - "preference" — timezone, language, tools, communication style
  - "context" — current project, upcoming deadline, temporary situation
  - "knowledge" — server IPs, workflows, team structure, technical facts
- When the user asks you to forget something, use the forget tool.
"""


class Reasoner:
    """Orchestrates LLM calls, tool execution, and conversation state."""

    def __init__(
        self,
        model_config: ModelConfig,
        db: sqlite3.Connection,
        skills_dir: Path,
        skill_writer=None,  # Set after Phase 2
        scheduler=None,
    ):
        self.model_config = model_config
        self.db = db
        self.skills_dir = skills_dir
        self.skill_writer = skill_writer
        self.scheduler = scheduler
        self._skill_tools: dict[str, dict] = {}  # name -> manifest
        self._current_channel: str | None = None
        self._current_recipient: str | None = None

    def new_conversation(self, title: str | None = None) -> str:
        """Create a new conversation and return its ID."""
        return create_conversation(self.db, title)

    async def run(
        self, conversation_id: str, user_message: str,
        channel: str | None = None, recipient: str | None = None,
    ) -> str:
        """Process a user message and return the assistant's response."""
        self._current_channel = channel
        self._current_recipient = recipient
        add_message(self.db, conversation_id, "user", user_message)

        # Retrieve semantically relevant memories for this message
        relevant = await self._retrieve_relevant_memories(user_message)
        log.debug("Retrieved %d relevant memories", len(relevant))

        messages = self._build_messages(conversation_id, relevant_memories=relevant)
        tools = self._get_tools()

        for iteration in range(MAX_TOOL_ITERATIONS):
            log.debug("Iteration %d — calling LLM with %d messages, %d tools", iteration + 1, len(messages), len(tools))
            response = await chat(self.model_config, messages, tools)

            if response.tool_calls:
                # Save assistant message with tool calls
                add_message(
                    self.db, conversation_id, "assistant",
                    response.content or "",
                    tool_calls=response.tool_calls,
                )
                messages.append(Message(
                    role="assistant", content=response.content, tool_calls=response.tool_calls,
                ))

                # Execute each tool call
                for tc in response.tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    try:
                        args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
                    except json.JSONDecodeError:
                        args = {}
                    log.debug("Tool call: %s(%s)", tool_name, json.dumps(args, default=str)[:200])
                    try:
                        result = await self._execute_tool(tool_name, args)
                    except Exception as e:
                        log.error("Tool %s raised: %s", tool_name, e)
                        result = f"Error: {e}"
                    log.debug("Tool result: %s", result[:500] if result else "(empty)")

                    add_message(
                        self.db, conversation_id, "tool", result, tool_call_id=tc["id"],
                    )
                    messages.append(Message(role="tool", content=result, tool_call_id=tc["id"]))
            else:
                # Final text response
                text = response.content or ""
                log.debug("Final response: %s", text[:200])
                add_message(self.db, conversation_id, "assistant", text)
                return text

        # Hit max iterations — return whatever we have
        return "(Reached maximum tool iterations. Please try again with a simpler request.)"

    def _build_messages(self, conversation_id: str, relevant_memories: list[dict] | None = None) -> list[Message]:
        """Load conversation history from DB and prepend system prompt with memories."""
        rows = get_messages(self.db, conversation_id)

        # Build memory section for system prompt
        memory_section = self._build_memory_section(relevant_memories)
        system_content = SYSTEM_PROMPT_TEMPLATE.format(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
        if memory_section:
            system_content += "\n" + memory_section

        messages = [Message(role="system", content=system_content)]
        for row in rows:
            tc = json.loads(row["tool_calls"]) if row.get("tool_calls") else None
            messages.append(Message(
                role=row["role"],
                content=row["content"],
                tool_calls=tc,
                tool_call_id=row.get("tool_call_id"),
            ))
        return messages

    def _build_memory_section(self, relevant_memories: list[dict] | None = None) -> str:
        """Build a '## What you know about the user' section from stored memories."""
        lines = []

        # Core memories: identity + preference — always included
        for cat in ("identity", "preference"):
            for m in list_memories(self.db, category=cat):
                lines.append(f"- [{cat}] {m['key']}: {m['value']}")

        # Contextual memories from semantic search
        if relevant_memories:
            for m in relevant_memories:
                cat = m["category"]
                lines.append(f"- [{cat}] {m['key']}: {m['value']}")

        if not lines:
            return ""
        return "## What you know about the user\n" + "\n".join(lines)

    def _get_tools(self) -> list[ToolDef]:
        """Return all available tools: built-ins + installed skills."""
        tools = list(BUILTIN_TOOLS)

        if self.skill_writer:
            tools.append(ToolDef(
                name="write_skill",
                description=(
                    "Write a new generic, reusable skill from scratch. Use when the user needs "
                    "a capability that doesn't exist yet. Skills must be parameterized — e.g. a "
                    "'weather' skill that accepts any city, not a 'weather_london' skill."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Short generic snake_case name (e.g. 'weather', 'translate', 'currency_convert')"},
                        "description": {"type": "string", "description": "What the skill does generically, mentioning the parameters it should accept"},
                    },
                    "required": ["name", "description"],
                },
            ))
            tools.append(ToolDef(
                name="modify_skill",
                description=(
                    "Modify an existing skill's handler based on feedback. Use when a skill "
                    "is broken, returns wrong results, or needs changes. Rewrites the handler, "
                    "regenerates tests, and re-tests in sandbox before redeploying."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name of the existing skill to modify"},
                        "feedback": {"type": "string", "description": "What's wrong or what to change"},
                    },
                    "required": ["name", "feedback"],
                },
            ))

        if self.scheduler:
            tools.append(ToolDef(
                name="schedule_task",
                description=(
                    "Schedule a skill to run at a specific time or interval. "
                    "Use for reminders, recurring checks, or delayed actions."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "schedule_type": {
                            "type": "string", "enum": ["once", "cron", "interval"],
                            "description": "once=run at specific time, cron=recurring cron, interval=every N ms",
                        },
                        "schedule_value": {
                            "type": "string",
                            "description": "For once: ISO datetime. For cron: cron expression. For interval: milliseconds as string.",
                        },
                        "message": {"type": "string", "description": "Message to deliver when the schedule fires"},
                    },
                    "required": ["schedule_type", "schedule_value", "message"],
                },
            ))
            tools.append(ToolDef(
                name="list_schedules",
                description="List all scheduled tasks and reminders (active, completed, paused) across all channels.",
                parameters={"type": "object", "properties": {}},
            ))

        for name, manifest in self._skill_tools.items():
            tools.append(ToolDef(
                name=f"skill_{name}",
                description=manifest.get("description", f"Run the {name} skill"),
                parameters=manifest.get("parameters", {"type": "object", "properties": {}}),
            ))

        return tools

    async def _execute_tool(self, name: str, arguments: dict) -> str:
        """Execute a tool call and return its result as a string."""
        if name == "list_skills":
            return self._tool_list_skills()
        elif name == "read_file":
            return self._tool_read_file(arguments)
        elif name == "write_file":
            return self._tool_write_file(arguments)
        elif name == "write_skill" and self.skill_writer:
            return await self._tool_write_skill(arguments)
        elif name == "modify_skill" and self.skill_writer:
            return await self._tool_modify_skill(arguments)
        elif name == "web_search":
            return await self._tool_web_search(arguments)
        elif name == "remember":
            return await self._tool_remember(arguments)
        elif name == "forget":
            return self._tool_forget(arguments)
        elif name == "schedule_task" and self.scheduler:
            return self._tool_schedule_task(arguments)
        elif name == "list_schedules" and self.scheduler:
            return self._tool_list_schedules()
        elif name.startswith("skill_"):
            return await self._tool_run_skill(name[6:], arguments)
        return f"Unknown tool: {name}"

    # --- Built-in tools ---

    def _tool_list_skills(self) -> str:
        skill_list = list_skills(self.db)
        if not skill_list:
            return "No skills installed."
        lines = [f"- {s['name']} ({s['status']})" for s in skill_list]
        return "Installed skills:\n" + "\n".join(lines)

    def _normalize_skill_path(self, raw_path: str) -> Path:
        """Normalize a skill path, stripping redundant 'skills/' prefix."""
        p = Path(raw_path)
        # LLMs often prepend "skills/" — strip it since skills_dir already points there
        if p.parts and p.parts[0] == "skills":
            p = Path(*p.parts[1:])
        return p

    def _tool_read_file(self, args: dict) -> str:
        path = self._normalize_skill_path(args.get("path", ""))
        resolved = (self.skills_dir / path).resolve()
        if not str(resolved).startswith(str(self.skills_dir.resolve())):
            return "Error: Can only read files within the skills directory."
        if not resolved.is_file():
            # List existing files in the target skill directory to help self-correct
            parent = resolved.parent
            if parent.is_dir():
                existing = [f.name for f in parent.iterdir() if f.is_file()]
                if existing:
                    return f"Error: File not found: {path}. Files in {path.parent}: {', '.join(existing)}"
            return f"Error: File not found: {path}. Use paths like: <skill_name>/handler.py"
        return resolved.read_text()

    def _tool_write_file(self, args: dict) -> str:
        path = self._normalize_skill_path(args.get("path", ""))
        content = args.get("content", "")
        resolved = (self.skills_dir / path).resolve()
        if not str(resolved).startswith(str(self.skills_dir.resolve())):
            return "Error: Can only write files within the skills directory."
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    async def _tool_write_skill(self, args: dict) -> str:
        name = args.get("name", "")
        description = args.get("description", "")
        if not name or not description:
            return "Error: write_skill requires 'name' and 'description'."
        result = await self.skill_writer.write_skill(name, description, "")
        if result.success:
            self.register_skill(name, result.manifest)
            return f"Skill '{name}' created and deployed successfully."
        return f"Failed to create skill '{name}': {result.error}"

    async def _tool_modify_skill(self, args: dict) -> str:
        name = args.get("name", "")
        feedback = args.get("feedback", "")
        if not name or not feedback:
            return "Error: modify_skill requires 'name' and 'feedback'."
        result = await self.skill_writer.modify_skill(name, feedback)
        if result.success:
            self.register_skill(name, result.manifest)
            return f"Skill '{name}' modified and redeployed successfully."
        return f"Failed to modify skill '{name}': {result.error}"

    async def _tool_run_skill(self, skill_name: str, args: dict) -> str:
        """Run an installed skill by importing and calling its handler."""
        handler_path = self.skills_dir / skill_name / "handler.py"
        if not handler_path.is_file():
            return f"Error: Skill '{skill_name}' handler not found."
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location(f"skill_{skill_name}", handler_path)
            if not spec or not spec.loader:
                return f"Error: Cannot load skill '{skill_name}'."
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            return f"Error loading skill '{skill_name}': {e}"
        run_fn = getattr(module, "run", None)
        if not run_fn:
            return f"Error: Skill '{skill_name}' has no run() function."
        try:
            result = await run_fn(args)
            return json.dumps(result)
        except Exception as e:
            return f"Error running skill '{skill_name}': {e}"

    async def _tool_web_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "Error: web_search requires 'query'."
        result = await web_search(self.model_config, query)
        if result.get("error"):
            return f"Web search failed: {result['error']}"
        summary = result.get("summary", "No results found.")
        sources = result.get("sources", [])
        if sources:
            source_lines = "\n".join(f"- [{s['title']}]({s['url']})" for s in sources)
            return f"{summary}\n\nSources:\n{source_lines}"
        return summary

    async def _tool_remember(self, args: dict) -> str:
        key = args.get("key", "")
        value = args.get("value", "")
        category = args.get("category", "knowledge")
        if not key or not value:
            return "Error: remember requires 'key' and 'value'."
        if category not in ("identity", "preference", "context", "knowledge"):
            return f"Error: invalid category '{category}'."
        embedding = await embed(self.model_config, value)
        save_memory(self.db, key, value, category, embedding or None)
        log.debug("Remembered [%s] %s = %s (embedding dims: %d)", category, key, value, len(embedding))
        return f"Remembered: {key} = {value} [{category}]"

    def _tool_forget(self, args: dict) -> str:
        key = args.get("key", "")
        if not key:
            return "Error: forget requires 'key'."
        deleted = delete_memory(self.db, key)
        if deleted:
            return f"Forgot: {key}"
        return f"No memory found with key '{key}'."

    def _tool_schedule_task(self, args: dict) -> str:
        schedule_type = args.get("schedule_type", "")
        schedule_value = args.get("schedule_value", "")
        message = args.get("message", "")
        if not schedule_type or not schedule_value or not message:
            return "Error: schedule_task requires schedule_type, schedule_value, and message."
        if schedule_type not in ("once", "cron", "interval"):
            return f"Error: invalid schedule_type '{schedule_type}'."
        sid = self.scheduler.schedule(
            "_system", schedule_type, schedule_value, message=message,
            channel=self._current_channel, recipient=self._current_recipient,
        )
        return f"Scheduled ({schedule_type}: {schedule_value}). Schedule ID: {sid}. Message: {message}"

    def _tool_list_schedules(self) -> str:
        from forgyn.db import list_schedules
        schedules = list_schedules(self.db)
        if not schedules:
            return "No schedules found."
        lines = []
        for s in schedules:
            ch = f" [{s.get('channel') or 'any'}]" if s.get("channel") else ""
            lines.append(
                f"- #{s['id']} ({s['status']}) {s['schedule_type']}: "
                f"{s.get('message', '(no message)')}{ch} — next: {s.get('next_run', 'none')}"
            )
        return "Schedules:\n" + "\n".join(lines)

    async def _retrieve_relevant_memories(self, user_message: str, top_k: int = 10) -> list[dict]:
        """Embed user message and find semantically similar context/knowledge memories."""
        msg_embedding = await embed(self.model_config, user_message)
        if not msg_embedding:
            return []  # No embedding support — skip semantic retrieval

        candidates = get_memories_with_embeddings(self.db)
        scored = []
        for mem in candidates:
            mem_embedding = json.loads(mem["embedding"])
            score = _cosine_similarity(msg_embedding, mem_embedding)
            if score > 0.3:  # Minimum relevance threshold
                scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        log.debug("Memory similarity scores: %s", [(s, m["key"]) for s, m in scored[:top_k]])
        return [m for _, m in scored[:top_k]]

    def register_skill(self, name: str, manifest: dict) -> None:
        """Register a deployed skill so it appears in the tool list."""
        self._skill_tools[name] = manifest

    def load_existing_skills(self) -> None:
        """Load all active skills from DB into the tool registry."""
        for skill in list_skills(self.db, status="active"):
            manifest = json.loads(skill["manifest"]) if isinstance(skill["manifest"], str) else skill["manifest"]
            self._skill_tools[skill["name"]] = manifest


# --- Built-in tool definitions ---

BUILTIN_TOOLS = [
    ToolDef(
        name="list_skills",
        description="List all installed skills and their status.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolDef(
        name="read_file",
        description="Read a file from the skills directory. Path is relative to the skills root.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path, e.g. 'reminder/handler.py' (NOT 'skills/reminder/handler.py')"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="write_file",
        description="Write a file to the skills directory. Path is relative to the skills root.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path, e.g. 'reminder/handler.py' (NOT 'skills/reminder/handler.py')"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDef(
        name="web_search",
        description=(
            "Search the web for current information. Use when you need up-to-date "
            "facts, news, documentation, or anything beyond your training data."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
            },
            "required": ["query"],
        },
    ),
    ToolDef(
        name="remember",
        description=(
            "Save a fact about the user to long-term memory. Use category: "
            "'identity' (name, role), 'preference' (timezone, tools), "
            "'context' (current project), 'knowledge' (workflows, server info)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short identifier (e.g. user_name, preferred_city)"},
                "value": {"type": "string", "description": "The fact to remember"},
                "category": {
                    "type": "string",
                    "enum": ["identity", "preference", "context", "knowledge"],
                    "description": "Memory category (default: knowledge)",
                },
            },
            "required": ["key", "value"],
        },
    ),
    ToolDef(
        name="forget",
        description="Delete a specific fact from long-term memory by its key.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The memory key to forget"},
            },
            "required": ["key"],
        },
    ),
]


# --- Helpers ---

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
