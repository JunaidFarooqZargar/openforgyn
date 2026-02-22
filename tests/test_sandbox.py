"""Tests for sandbox runner (Step 2.1).

Tests marked @pytest.mark.docker require a running Docker daemon.
"""

import pytest

from forgyn.sandbox import is_docker_available, run_in_sandbox


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_available():
    result = await is_docker_available()
    assert isinstance(result, bool)


@pytest.mark.docker
@pytest.mark.asyncio
async def test_run_passing_test(tmp_path):
    (tmp_path / "test_example.py").write_text("def test_pass(): assert 1 + 1 == 2\n")
    result = await run_in_sandbox(tmp_path)
    assert result.exit_code == 0
    assert "passed" in result.stdout.lower()


@pytest.mark.docker
@pytest.mark.asyncio
async def test_run_failing_test(tmp_path):
    (tmp_path / "test_example.py").write_text("def test_fail(): assert 1 == 2\n")
    result = await run_in_sandbox(tmp_path)
    assert result.exit_code != 0
    assert "failed" in result.stdout.lower()


@pytest.mark.docker
@pytest.mark.asyncio
async def test_stdout_captured(tmp_path):
    (tmp_path / "test_example.py").write_text(
        "def test_print():\n    print('HELLO_SANDBOX')\n    assert True\n"
    )
    result = await run_in_sandbox(tmp_path)
    assert result.exit_code == 0
    assert "HELLO_SANDBOX" in result.stdout


@pytest.mark.docker
@pytest.mark.asyncio
async def test_no_network_by_default(tmp_path):
    (tmp_path / "test_example.py").write_text(
        "import urllib.request\n"
        "def test_no_net():\n"
        "    try:\n"
        "        urllib.request.urlopen('https://httpbin.org/get', timeout=3)\n"
        "        assert False, 'Should not have network access'\n"
        "    except Exception:\n"
        "        pass  # Expected: no network\n"
    )
    result = await run_in_sandbox(tmp_path, network=False)
    assert result.exit_code == 0


@pytest.mark.docker
@pytest.mark.asyncio
async def test_install_deps(tmp_path):
    (tmp_path / "test_example.py").write_text(
        "def test_import():\n    import cowsay\n    assert cowsay is not None\n"
    )
    result = await run_in_sandbox(tmp_path, install_deps=["cowsay"], network=True)
    assert result.exit_code == 0
