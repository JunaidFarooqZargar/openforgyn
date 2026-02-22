"""Capability-based permission gate for skills."""

from __future__ import annotations

import json
from dataclasses import dataclass

VALID_PERMISSION_PREFIXES = ("network", "filesystem:", "env:", "schedule:", "exec")

PERMISSION_DESCRIPTIONS = {
    "network": "Make HTTP requests to the internet",
    "exec": "Execute shell commands",
}


def _describe_permission(perm: str) -> str:
    """Return a human-readable description of a permission."""
    if perm in PERMISSION_DESCRIPTIONS:
        return PERMISSION_DESCRIPTIONS[perm]
    if perm.startswith("filesystem:"):
        path = perm.split(":", 1)[1]
        return f"Read/write files at: {path}"
    if perm.startswith("env:"):
        var = perm.split(":", 1)[1]
        return f"Access environment variable: {var}"
    if perm.startswith("schedule:"):
        stype = perm.split(":", 1)[1]
        return f"Register a scheduled task ({stype})"
    return perm


@dataclass
class PermissionRequest:
    skill_name: str
    permissions: list[str]
    description: str


def validate_manifest(manifest: dict) -> list[str]:
    """Validate a skill manifest. Returns list of error strings (empty = valid)."""
    errors = []

    if not isinstance(manifest, dict):
        return ["Manifest must be a JSON object"]

    if not manifest.get("name"):
        errors.append("Missing required field: 'name'")

    if not manifest.get("description"):
        errors.append("Missing required field: 'description'")

    if "permissions" not in manifest:
        errors.append("Missing required field: 'permissions'")
    elif not isinstance(manifest["permissions"], list):
        errors.append("'permissions' must be a list")
    else:
        for perm in manifest["permissions"]:
            if not isinstance(perm, str):
                errors.append(f"Permission must be a string, got: {type(perm).__name__}")
            elif not any(perm == p or perm.startswith(p) for p in VALID_PERMISSION_PREFIXES):
                errors.append(f"Unknown permission: '{perm}'")

    if "entry_point" not in manifest:
        errors.append("Missing required field: 'entry_point'")

    if "test_file" not in manifest:
        errors.append("Missing required field: 'test_file'")

    if "dependencies" in manifest and not isinstance(manifest["dependencies"], list):
        errors.append("'dependencies' must be a list")

    return errors


async def request_permissions(
    request: PermissionRequest,
    prompt_fn=None,
) -> bool:
    """Ask the user to approve permissions for a skill.

    Args:
        request: The permission request details.
        prompt_fn: Optional callable for testing. Receives the prompt string,
                   returns the user's response. Defaults to input().
    """
    if not request.permissions:
        return True  # No permissions needed

    if prompt_fn is None:
        prompt_fn = input

    lines = [
        f"\nSkill '{request.skill_name}' requests the following permissions:",
        f"  Description: {request.description}",
        "",
    ]
    for perm in request.permissions:
        lines.append(f"  - {_describe_permission(perm)}")
    lines.append("")
    lines.append("Allow? [y/n] ")

    prompt_text = "\n".join(lines)
    response = prompt_fn(prompt_text)
    return response.strip().lower() in ("y", "yes")


def get_granted_permissions(skill_name: str, db) -> list[str]:
    """Get the permissions that were granted to a skill (from its stored manifest)."""
    from forgyn.db import get_skill

    skill = get_skill(db, skill_name)
    if not skill:
        return []
    manifest = skill["manifest"]
    if isinstance(manifest, str):
        manifest = json.loads(manifest)
    return manifest.get("permissions", [])
