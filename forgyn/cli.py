"""CLI interface for Forgyn."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

import forgyn
from forgyn.db import init_db
from forgyn.models import ModelConfig, parse_model_string
from forgyn.reasoner import Reasoner

DEFAULT_DATA_DIR = Path.home() / ".forgyn"
DEFAULT_MODEL = "openai/gpt-4o"


def ensure_data_dir(data_dir: Path) -> Path:
    """Create data directory and subdirectories on first run."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "skills").mkdir(exist_ok=True)
    return data_dir


def resolve_model_config(model_str: str | None) -> ModelConfig:
    """Build ModelConfig from CLI flag or environment."""
    model_str = model_str or os.environ.get("FORGYN_MODEL", DEFAULT_MODEL)
    return parse_model_string(model_str)


@click.group(invoke_without_command=True)
@click.option("--model", "-m", default=None, help="Model as provider/model (e.g. openai/gpt-4o)")
@click.option("--data-dir", default=None, type=click.Path(), help="Data directory path")
@click.option("--conversation-id", "-c", default=None, help="Resume a conversation by ID")
@click.version_option(forgyn.__version__, prog_name="forgyn")
@click.pass_context
def main(ctx: click.Context, model: str | None, data_dir: str | None, conversation_id: str | None):
    """Forgyn — An agent that forges its own capabilities."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    ctx.obj["model"] = model
    ctx.obj["conversation_id"] = conversation_id

    if ctx.invoked_subcommand is None:
        ctx.invoke(chat)


@main.command()
@click.pass_context
def chat(ctx: click.Context):
    """Start an interactive chat session with Forgyn."""
    data_dir = ensure_data_dir(ctx.obj["data_dir"])
    db_path = data_dir / "forgyn.db"
    skills_dir = data_dir / "skills"

    try:
        model_config = resolve_model_config(ctx.obj.get("model"))
    except (ValueError, KeyError) as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Set FORGYN_MODEL (e.g. openai/gpt-4o) and the matching API key.", err=True)
        sys.exit(1)

    db = init_db(db_path)
    reasoner = Reasoner(model_config=model_config, db=db, skills_dir=skills_dir)

    conversation_id = ctx.obj.get("conversation_id") or reasoner.new_conversation()

    click.echo(f"Forgyn v{forgyn.__version__} — An agent that forges its own capabilities.")
    click.echo(f"Model: {model_config.provider}/{model_config.model}")
    click.echo("Type 'exit' or Ctrl+C to quit.\n")

    asyncio.run(_chat_loop(reasoner, conversation_id))


async def _chat_loop(reasoner: Reasoner, conversation_id: str):
    """Interactive read-eval-print loop."""
    while True:
        try:
            user_input = click.prompt(">", prompt_suffix=" ")
        except (EOFError, KeyboardInterrupt):
            click.echo("\nGoodbye.")
            break

        if user_input.strip().lower() in ("exit", "quit"):
            click.echo("Goodbye.")
            break

        try:
            response = await reasoner.run(conversation_id, user_input)
            click.echo(f"\n{response}\n")
        except Exception as e:
            click.echo(f"\nError: {e}\n", err=True)


@main.command()
@click.pass_context
def skills(ctx: click.Context):
    """List installed skills."""
    data_dir = ensure_data_dir(ctx.obj["data_dir"])
    db = init_db(data_dir / "forgyn.db")

    from forgyn.db import list_skills

    skill_list = list_skills(db)
    if not skill_list:
        click.echo("No skills installed.")
        return

    click.echo(f"{'Name':<20} {'Status':<10} {'Created'}")
    click.echo("-" * 50)
    for s in skill_list:
        click.echo(f"{s['name']:<20} {s['status']:<10} {s['created_at']}")
