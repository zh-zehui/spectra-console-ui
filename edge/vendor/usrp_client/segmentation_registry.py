# -*- coding: utf-8 -*-
"""Registry and dynamic loader for segmentation plugins."""
from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from segmentation_contracts import SegmentationPlugin


PluginFactory = Callable[[dict[str, Any]], SegmentationPlugin]
_FACTORIES: dict[str, PluginFactory] = {}
_BUILTINS_LOADED = False


def register_algorithm(name: str, factory: PluginFactory) -> None:
    """Register a factory under a stable wire/configuration name."""
    key = str(name).strip().lower()
    if not key:
        raise ValueError("algorithm name cannot be empty")
    _FACTORIES[key] = factory


def _load_builtin_algorithms() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    from segmentation_model import ModelSegmentationPlugin
    from segmentation_traditional import TraditionalSegmentationPlugin

    register_algorithm("model", ModelSegmentationPlugin)
    register_algorithm("traditional", TraditionalSegmentationPlugin)
    _BUILTINS_LOADED = True


def _load_extra_modules(module_names: list[str]) -> None:
    for module_name in module_names:
        importlib.import_module(module_name)


def create_algorithm(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    plugin_modules: list[str] | None = None,
) -> SegmentationPlugin:
    """Create an algorithm from built-ins or externally imported plugins."""
    _load_builtin_algorithms()
    _load_extra_modules(plugin_modules or [])
    key = str(name).strip().lower()
    try:
        factory = _FACTORIES[key]
    except KeyError as exc:
        available = ", ".join(sorted(_FACTORIES)) or "none"
        raise ValueError(f"unknown segmentation algorithm '{name}'; available: {available}") from exc
    return factory(dict(params or {}))


def available_algorithms() -> tuple[str, ...]:
    _load_builtin_algorithms()
    return tuple(sorted(_FACTORIES))
