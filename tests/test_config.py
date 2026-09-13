"""配置测试（`MVP_PLAN.md` 阶段 1 任务 4）。

要点：API Key 只从环境变量读；配置在**使用时**读取而不是 import 时固化；
`DATA_DIR` 可覆盖，默认 `<repo>/data`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from booksoul.config import (
    DEFAULT_MODEL_NAME,
    ENV_API_KEY,
    ENV_DATA_DIR,
    ENV_MODEL_NAME,
    Settings,
    get_data_dir,
    get_project_root,
    load_settings,
    require_api_key,
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (ENV_API_KEY, ENV_MODEL_NAME, ENV_DATA_DIR):
        monkeypatch.delenv(name, raising=False)


def test_project_root_points_at_repo() -> None:
    root = get_project_root()

    assert (root / "MVP_PLAN.md").is_file()
    assert (root / "src" / "booksoul" / "config.py").is_file()


def test_data_dir_defaults_to_repo_data(clean_env: None) -> None:
    assert get_data_dir() == get_project_root() / "data"


def test_api_key_read_from_environment(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_KEY, "sk-test-123")

    settings = load_settings()

    assert settings.api_key == "sk-test-123"
    assert settings.has_api_key is True


def test_api_key_absent_is_empty_not_error(clean_env: None) -> None:
    settings = load_settings()

    assert settings.api_key == ""
    assert settings.has_api_key is False


def test_api_key_whitespace_is_stripped(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_KEY, "  sk-test-456  ")

    assert load_settings().api_key == "sk-test-456"


def test_model_name_defaults_and_override(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_settings().model_name == DEFAULT_MODEL_NAME

    monkeypatch.setenv(ENV_MODEL_NAME, "deepseek/deepseek-reasoner")
    assert load_settings().model_name == "deepseek/deepseek-reasoner"

    # 空字符串不应把默认值顶掉
    monkeypatch.setenv(ENV_MODEL_NAME, "   ")
    assert load_settings().model_name == DEFAULT_MODEL_NAME


def test_data_dir_env_override(clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "custom-data"))

    settings = load_settings()

    assert settings.data_dir == tmp_path / "custom-data"
    assert get_data_dir() == tmp_path / "custom-data"


def test_explicit_arguments_beat_environment(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_API_KEY, "from-env")
    monkeypatch.setenv(ENV_MODEL_NAME, "env-model")

    settings = load_settings(
        api_key="from-arg", model_name="arg-model", data_dir=tmp_path / "d"
    )

    assert settings.api_key == "from-arg"
    assert settings.model_name == "arg-model"
    assert settings.data_dir == tmp_path / "d"


def test_derived_subdirectories(clean_env: None, tmp_path: Path) -> None:
    settings = load_settings(data_dir=tmp_path)

    assert settings.novels_dir == tmp_path / "novels"
    assert settings.cards_dir == tmp_path / "cards"
    assert settings.sessions_dir == tmp_path / "sessions"
    assert settings.memory_dir == tmp_path / "memory"
    assert settings.character_dir("book-1") == tmp_path / "cards" / "book-1"


def test_ensure_dirs_is_idempotent(clean_env: None, tmp_path: Path) -> None:
    settings = load_settings(data_dir=tmp_path / "fresh")

    returned = settings.ensure_dirs()
    settings.ensure_dirs()

    assert returned is settings
    for path in (
        settings.data_dir,
        settings.novels_dir,
        settings.cards_dir,
        settings.sessions_dir,
        settings.memory_dir,
    ):
        assert path.is_dir()


def test_config_is_read_lazily_not_frozen_at_import(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个进程里改了环境变量，下一次 load_settings 就要看到新值。"""
    monkeypatch.setenv(ENV_API_KEY, "first")
    assert load_settings().api_key == "first"

    monkeypatch.setenv(ENV_API_KEY, "second")
    assert load_settings().api_key == "second"


def test_require_api_key_returns_key(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_KEY, "sk-ok")

    assert require_api_key() == "sk-ok"
    assert require_api_key(load_settings(api_key="sk-explicit")) == "sk-explicit"


def test_require_api_key_raises_actionable_error(clean_env: None) -> None:
    with pytest.raises(RuntimeError) as excinfo:
        require_api_key()

    message = str(excinfo.value)
    assert ENV_API_KEY in message
    assert "环境变量" in message


def test_settings_is_frozen(clean_env: None) -> None:
    settings = Settings()

    with pytest.raises(Exception):
        settings.api_key = "nope"  # type: ignore[misc]
