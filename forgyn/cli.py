"""CLI interface for Forgyn."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()


def _configure_logging(debug: bool = False) -> None:
    """Set up logging. Normal mode shows skill writer progress only. Debug shows everything."""
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")
    else:
        # Clean output: only show skill_writer INFO+ (the [1/5]... progress lines)
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        logging.getLogger("forgyn.skill_writer").setLevel(logging.INFO)
        # Suppress noisy third-party loggers
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

import forgyn
from forgyn.db import init_db
from forgyn.models import PROVIDER_DEFAULTS, parse_model_string
from forgyn.reasoner import Reasoner
from forgyn.skill_writer import SkillWriter

DEFAULT_DATA_DIR = Path.home() / ".forgyn"
DEFAULT_MODEL = "openai/gpt-4o"


def ensure_data_dir(data_dir: Path) -> Path:
    """Create data directory and subdirectories on first run."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "skills").mkdir(exist_ok=True)
    return data_dir


def resolve_model_config(model_str: str | None):
    """Build ModelConfig from CLI flag or environment."""
    model_str = model_str or os.environ.get("FORGYN_MODEL", DEFAULT_MODEL)
    return parse_model_string(model_str)


@click.group(invoke_without_command=True)
@click.option("--model", "-m", default=None, help="Model as provider/model (e.g. openai/gpt-4o)")
@click.option("--data-dir", default=None, type=click.Path(), help="Data directory path")
@click.option("--conversation-id", "-c", default=None, help="Resume a conversation by ID")
@click.option("--debug", "-d", is_flag=True, default=False, help="Enable debug logging")
@click.version_option(forgyn.__version__, prog_name="forgyn")
@click.pass_context
def main(ctx: click.Context, model: str | None, data_dir: str | None, conversation_id: str | None, debug: bool):
    """Forgyn — An agent that forges its own capabilities."""
    _configure_logging(debug)
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
    skill_writer = SkillWriter(
        model_config=model_config,
        db=db,
        skills_dir=skills_dir,
    )
    reasoner = Reasoner(model_config=model_config, db=db, skills_dir=skills_dir, skill_writer=skill_writer)
    reasoner.load_existing_skills()

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


@main.command()
@click.pass_context
def doctor(ctx: click.Context):
    """Check system health: Python, Docker, API keys, data directory."""
    ok_count = 0
    total = 0

    def check(label: str, passed: bool, detail: str = ""):
        nonlocal ok_count, total
        total += 1
        status = "OK" if passed else "FAIL"
        if passed:
            ok_count += 1
        msg = f"  [{status}] {label}"
        if detail:
            msg += f" — {detail}"
        click.echo(msg)

    click.echo(f"Forgyn v{forgyn.__version__} — System Check\n")

    # Python version
    v = sys.version_info
    check("Python", v >= (3, 12), f"{v.major}.{v.minor}.{v.micro}")

    # Docker
    docker_available = shutil.which("docker") is not None
    if docker_available:
        import subprocess
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        docker_running = result.returncode == 0
    else:
        docker_running = False
    check("Docker installed", docker_available)
    check("Docker running", docker_running)

    # API keys
    for provider, defaults in PROVIDER_DEFAULTS.items():
        key_env = defaults.get("key_env")
        if key_env:
            has_key = bool(os.environ.get(key_env))
            check(f"{provider} API key ({key_env})", has_key, "set" if has_key else "not set")

    # Data directory
    data_dir = ctx.obj["data_dir"]
    check("Data directory", data_dir.exists(), str(data_dir))

    # Skills directory
    skills_dir = data_dir / "skills"
    check("Skills directory", skills_dir.exists(), str(skills_dir))

    click.echo(f"\n{ok_count}/{total} checks passed.")


@main.command()
@click.pass_context
def history(ctx: click.Context):
    """Show recent conversations."""
    data_dir = ensure_data_dir(ctx.obj["data_dir"])
    db = init_db(data_dir / "forgyn.db")

    rows = db.execute(
        "SELECT id, title, created_at FROM conversations ORDER BY created_at DESC LIMIT 20"
    ).fetchall()

    if not rows:
        click.echo("No conversations yet.")
        return

    click.echo(f"{'ID':<38} {'Title':<25} {'Created'}")
    click.echo("-" * 80)
    for r in rows:
        title = r["title"] or "(untitled)"
        click.echo(f"{r['id']:<38} {title:<25} {r['created_at']}")


@main.command()
@click.argument("skill_name")
@click.argument("commit_hash")
@click.pass_context
def rollback(ctx: click.Context, skill_name: str, commit_hash: str):
    """Rollback a skill to a previous version. Usage: forgyn rollback <skill> <commit>"""
    data_dir = ensure_data_dir(ctx.obj["data_dir"])
    skills_dir = data_dir / "skills"

    from forgyn.audit import get_skill_history, rollback_skill

    hist = get_skill_history(skills_dir, skill_name)
    if not hist:
        click.echo(f"No history found for skill '{skill_name}'.")
        return

    valid_hashes = [h["hash"] for h in hist]
    if commit_hash not in valid_hashes:
        click.echo(f"Commit {commit_hash} not found in history for '{skill_name}'.")
        click.echo("Available commits:")
        for h in hist:
            click.echo(f"  {h['hash'][:12]}  {h['date']}  {h['message']}")
        return

    rollback_skill(skills_dir, skill_name, commit_hash)
    click.echo(f"Rolled back '{skill_name}' to commit {commit_hash[:12]}.")
