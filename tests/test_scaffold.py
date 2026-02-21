"""Tests for project scaffolding (Step 1.1)."""


def test_version_exists():
    import forgyn
    assert isinstance(forgyn.__version__, str)
    assert len(forgyn.__version__) > 0


def test_package_importable():
    import forgyn
    assert forgyn is not None


def test_submodules_importable():
    from forgyn import db, models, reasoner, cli
    assert db is not None
    assert models is not None
    assert reasoner is not None
    assert cli is not None
