"""Tests for permission gate (Step 2.2)."""

import pytest

from forgyn.db import init_db, save_skill
from forgyn.permissions import (
    PermissionRequest,
    _describe_permission,
    get_granted_permissions,
    request_permissions,
    validate_manifest,
)


# --- validate_manifest ---


def test_validate_valid_manifest():
    manifest = {
        "name": "echo",
        "description": "Echoes input back",
        "permissions": ["network"],
        "dependencies": [],
        "entry_point": "handler.py",
        "test_file": "test_handler.py",
    }
    assert validate_manifest(manifest) == []


def test_validate_missing_name():
    manifest = {"description": "x", "permissions": [], "entry_point": "handler.py", "test_file": "test_handler.py"}
    errors = validate_manifest(manifest)
    assert any("name" in e for e in errors)


def test_validate_missing_permissions():
    manifest = {"name": "x", "description": "x", "entry_point": "handler.py", "test_file": "test_handler.py"}
    errors = validate_manifest(manifest)
    assert any("permissions" in e for e in errors)


def test_validate_missing_entry_point():
    manifest = {"name": "x", "description": "x", "permissions": [], "test_file": "test_handler.py"}
    errors = validate_manifest(manifest)
    assert any("entry_point" in e for e in errors)


def test_validate_missing_test_file():
    manifest = {"name": "x", "description": "x", "permissions": [], "entry_point": "handler.py"}
    errors = validate_manifest(manifest)
    assert any("test_file" in e for e in errors)


def test_validate_unknown_permission():
    manifest = {
        "name": "x", "description": "x",
        "permissions": ["fly_to_moon"],
        "entry_point": "handler.py", "test_file": "test_handler.py",
    }
    errors = validate_manifest(manifest)
    assert any("Unknown permission" in e for e in errors)


def test_validate_valid_permissions():
    manifest = {
        "name": "x", "description": "x",
        "permissions": ["network", "env:API_KEY", "schedule:cron", "filesystem:/tmp", "exec"],
        "entry_point": "handler.py", "test_file": "test_handler.py",
    }
    assert validate_manifest(manifest) == []


# --- request_permissions ---


@pytest.mark.asyncio
async def test_request_approved():
    req = PermissionRequest(skill_name="test", permissions=["network"], description="Test skill")
    result = await request_permissions(req, prompt_fn=lambda _: "y")
    assert result is True


@pytest.mark.asyncio
async def test_request_denied():
    req = PermissionRequest(skill_name="test", permissions=["network"], description="Test skill")
    result = await request_permissions(req, prompt_fn=lambda _: "n")
    assert result is False


@pytest.mark.asyncio
async def test_no_permissions_auto_approved():
    req = PermissionRequest(skill_name="test", permissions=[], description="Test skill")
    result = await request_permissions(req, prompt_fn=lambda _: "n")
    assert result is True  # No permissions needed = auto-approved


# --- describe_permission ---


def test_describe_network():
    assert "HTTP" in _describe_permission("network")


def test_describe_env():
    desc = _describe_permission("env:MY_API_KEY")
    assert "MY_API_KEY" in desc


def test_describe_filesystem():
    desc = _describe_permission("filesystem:/tmp/data")
    assert "/tmp/data" in desc


def test_describe_schedule():
    desc = _describe_permission("schedule:cron")
    assert "cron" in desc


# --- get_granted_permissions ---


def test_get_granted_permissions(tmp_db_path):
    db = init_db(tmp_db_path)
    manifest = {"name": "echo", "description": "x", "permissions": ["network", "env:KEY"]}
    save_skill(db, "echo", manifest)
    perms = get_granted_permissions("echo", db)
    assert perms == ["network", "env:KEY"]


def test_get_granted_permissions_missing_skill(tmp_db_path):
    db = init_db(tmp_db_path)
    assert get_granted_permissions("nonexistent", db) == []
