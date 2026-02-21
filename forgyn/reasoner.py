"""Main LLM reasoning loop. Plans actions, calls tools, returns responses."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from forgyn.db import add_message, create_conversation, get_messages, list_skills
from forgyn.models import Message, ModelConfig, ToolDef, chat

MAX_TOOL_ITERATIONS = 10

SYSTEM_PROMPT = """\
You are Forgyn, a personal AI agent that forges its own capabilities.

You can write your own skills (Python code) to gain new abilities. When the user
asks you to do something you can't do yet, you can build a skill for it.

Guidelines:
- Be concise and helpful.
- When you have an installed skill that can handle a request, use it.
- When you need a capability you don't have, tell the user you can build it
  and use the write_skill tool.
- Always explain what you built after creating a new skill.
"""


class Reasoner:
    """Orchestrates LLM calls, tool execution, and conversation state."""

    def __init__(
        self,
        model_config: ModelConfig,
        db: sqlite3.Connection,
        skills_dir: Path,
        skill_writer=None,  # Set after Phase 2
    ):
        self.model_config = model_config
        self.db = db
        self.skills_dir = skills_dir
        self.skill_writer = skill_writer
        self._skill_tools: dict[str, dict] = {}  # name -> manifest

    def new_conversation(self, title: str | None = None) -> str:
        """Create a new conversation and return its ID."""
        return create_conversation(self.db, title)

    async def run(self, conversation_id: str, user_message: str) -> str:
        """Process a user message and return the assistant's response."""
        add_message(self.db, conversation_id, "user", user_message)
        messages = self._build_messages(conversation_id)
        tools = self._get_tools()

        for _ in range(MAX_TOOL_ITERATIONS):
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
                    result = await self._execute_tool(tool_name, args)

                    add_message(
                        self.db, conversation_id, "tool", result, tool_call_id=tc["id"],
                    )
                    messages.append(Message(role="tool", content=result, tool_call_id=tc["id"]))
            else:
                # Final text response
                text = response.content or ""
                add_message(self.db, conversation_id, "assistant", text)
                return text

        # Hit max iterations — return whatever we have
        return "(Reached maximum tool iterations. Please try again with a simpler request.)"

    def _build_messages(self, conversation_id: str) -> list[Message]:
        """Load conversation history from DB and prepend system prompt."""
        rows = get_messages(self.db, conversation_id)
        messages = [Message(role="system", content=SYSTEM_PROMPT)]
        for row in rows:
            tc = json.loads(row["tool_calls"]) if row.get("tool_calls") else None
            messages.append(Message(
                role=row["role"],
                content=row["content"],
                tool_calls=tc,
                tool_call_id=row.get("tool_call_id"),
            ))
        return messages

    def _get_tools(self) -> list[ToolDef]:
        """Return all available tools: built-ins + installed skills."""
        tools = list(BUILTIN_TOOLS)

        if self.skill_writer:
            tools.append(ToolDef(
                name="write_skill",
                description="Write a new skill from scratch. Use when the user needs a capability that doesn't exist yet.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Short snake_case name for the skill"},
                        "description": {"type": "string", "description": "What the skill should do"},
                    },
                    "required": ["name", "description"],
                },
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

    def _tool_read_file(self, args: dict) -> str:
        path = Path(args.get("path", ""))
        resolved = (self.skills_dir / path).resolve()
        if not str(resolved).startswith(str(self.skills_dir.resolve())):
            return "Error: Can only read files within the skills directory."
        if not resolved.is_file():
            return f"Error: File not found: {path}"
        return resolved.read_text()

    def _tool_write_file(self, args: dict) -> str:
        path = Path(args.get("path", ""))
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

    async def _tool_run_skill(self, skill_name: str, args: dict) -> str:
        """Run an installed skill by importing and calling its handler."""
        handler_path = self.skills_dir / skill_name / "handler.py"
        if not handler_path.is_file():
            return f"Error: Skill '{skill_name}' handler not found."
        # Dynamic import and execution of skill handler
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"skill_{skill_name}", handler_path)
        if not spec or not spec.loader:
            return f"Error: Cannot load skill '{skill_name}'."
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        run_fn = getattr(module, "run", None)
        if not run_fn:
            return f"Error: Skill '{skill_name}' has no run() function."
        try:
            result = await run_fn(args)
            return json.dumps(result)
        except Exception as e:
            return f"Error running skill '{skill_name}': {e}"

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
        description="Read a file from the skills directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within skills/"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="write_file",
        description="Write a file to the skills directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within skills/"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    ),
]
