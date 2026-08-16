#!/usr/bin/env python3
"""Resolve the Software Engineering Team owner from project config."""

from __future__ import annotations

from collections.abc import Mapping


def team_from_config(config: Mapping[str, object]) -> str:
    """Resolve the sole team from the current project config contract."""
    return str(config.get("team_id", "")).strip()
