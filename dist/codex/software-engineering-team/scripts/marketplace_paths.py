#!/usr/bin/env python3
"""Agent Marketplace runtime path contract."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


VENDOR_HOME_ENV = "AGENTROF_HOME"
MARKETPLACE_HOME_ENV = "AGENT_MARKETPLACE_HOME"
VENDOR_HOME_DIR = ".agentrof"
MARKETPLACE_HOME_DIR = "agent-marketplace"


def vendor_home(
    environ: Mapping[str, str] | None = None,
    user_home: str | Path | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    override = values.get(VENDOR_HOME_ENV, "").strip()
    if override:
        return Path(override)
    base = Path.home() if user_home is None else Path(user_home)
    return base / VENDOR_HOME_DIR


def marketplace_home(
    environ: Mapping[str, str] | None = None,
    user_home: str | Path | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    override = values.get(MARKETPLACE_HOME_ENV, "").strip()
    if override:
        return Path(override)
    return vendor_home(values, user_home) / MARKETPLACE_HOME_DIR
