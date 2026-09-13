"""CLI 冒烟测试（阶段 1 只有自检子命令，阶段 8 才补齐业务命令）。

用 typer 自带的 CliRunner，不启子进程。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from booksoul import __version__
from booksoul.cli import app
from booksoul.config import ENV_API_KEY, ENV_DATA_DIR

runner = CliRunner()


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (ENV_API_KEY, ENV_DATA_DIR):
        monkeypatch.delenv(name, raising=False)


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_bare_invocation_shows_help() -> None:
    """直接敲 `booksoul` 打印帮助（typer 的 no_args_is_help 走 exit code 2）。"""
    result = runner.invoke(app, [])

    assert result.exit_code == 2
    assert "Usage" in result.stdout
    assert "booksoul" in result.stdout.lower()


def test_config_command_masks_api_key(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_API_KEY, "sk-super-secret-value")
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path))

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert "sk-super-secret-value" not in result.stdout
    assert "已设置" in result.stdout


def test_config_command_reports_missing_key(clean_env: None, tmp_path: Path) -> None:
    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert ENV_API_KEY in result.stdout


def test_unknown_command_fails() -> None:
    result = runner.invoke(app, ["definitely-not-a-command"])

    assert result.exit_code != 0
