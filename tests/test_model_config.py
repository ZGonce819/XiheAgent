from __future__ import annotations

from pathlib import Path

from config import model_config


def _model_payload(name: str, *, set_default: bool = False) -> dict[str, object]:
    return {
        "name": name,
        "api_base": "https://example.com/v1",
        "api_key": "test-key",
        "model_name": name.lower(),
        "set_default": set_default,
    }


def test_custom_model_is_default_only_when_requested(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        model_config, "_models_path", lambda: tmp_path / "custom_models.json"
    )
    monkeypatch.setattr(
        model_config, "_builtin_path", lambda: tmp_path / "builtin_model.json"
    )

    first = model_config.create_custom_model(_model_payload("First"))
    second = model_config.create_custom_model(
        _model_payload("Second", set_default=True)
    )

    assert first["is_default"] is False
    assert second["is_default"] is True
    assert [model["is_default"] for model in model_config.list_custom_models()] == [
        False,
        True,
    ]
