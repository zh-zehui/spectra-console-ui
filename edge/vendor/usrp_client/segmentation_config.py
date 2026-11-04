# -*- coding: utf-8 -*-
"""Configuration loading for segmentation plugins.

Transport settings belong to the receiver CLI.  Algorithm settings belong in
this file (or a user supplied JSON file) under ``algorithms.<name>.params``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_FILE = Path(__file__).with_name("segmentation_config.json")


def load_segmentation_config(path: str | None = None) -> dict[str, Any]:
    """Load a JSON configuration, returning an empty config when omitted."""
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_FILE
    if not config_path.is_file():
        if path:
            raise FileNotFoundError(f"segmentation config not found: {config_path}")
        return {}
    with config_path.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError("segmentation config root must be a JSON object")
    return config


def algorithm_params(config: dict[str, Any], algorithm_name: str) -> dict[str, Any]:
    """Extract one plugin's parameter object without leaking it into receiver."""
    # A small per-algorithm file is also accepted, e.g.
    # ``traditional.json`` containing only ``cfar_threshold_db`` and friends.
    if "algorithms" not in config:
        return dict(config)
    algorithms = config.get("algorithms", {})
    if not isinstance(algorithms, dict):
        raise ValueError("segmentation config 'algorithms' must be an object")
    item = algorithms.get(algorithm_name, {})
    if not isinstance(item, dict):
        raise ValueError(f"algorithm config for '{algorithm_name}' must be an object")
    params = item.get("params", item)
    if not isinstance(params, dict):
        raise ValueError(f"algorithm params for '{algorithm_name}' must be an object")
    return dict(params)


def configured_algorithm(config: dict[str, Any], requested: str | None) -> str:
    """Resolve CLI algorithm name, then config default, then traditional."""
    name = requested or config.get("default_algorithm") or "traditional"
    name = str(name).strip().lower()
    if not name:
        raise ValueError("algorithm name cannot be empty")
    return name
