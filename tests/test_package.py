"""Tests for package metadata (__init__.py) and the python -m entry point (__main__.py)."""

import importlib
import runpy
from importlib.metadata import PackageNotFoundError
from unittest.mock import MagicMock

import snow_ops
import snow_ops.cli


def test_version_is_exposed():
    assert snow_ops.__version__
    assert snow_ops.__version__ != "unknown"


def test_version_falls_back_to_unknown_when_not_installed(monkeypatch):
    monkeypatch.setattr(
        "importlib.metadata.version", MagicMock(side_effect=PackageNotFoundError)
    )
    try:
        importlib.reload(snow_ops)
        assert snow_ops.__version__ == "unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(snow_ops)


def test_python_dash_m_invokes_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(snow_ops.cli, "main", lambda: calls.append(True))
    runpy.run_module("snow_ops.__main__", run_name="__main__")
    assert calls == [True]
