#!/usr/bin/env python3
from __future__ import annotations

import sys

import bridge_orchestrator
from _bridge_common import guarded_main


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    return bridge_orchestrator.run(state, argv)


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
