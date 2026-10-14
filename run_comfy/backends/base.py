"""Backend interface: run one registered workflow with concrete inputs and return local output files."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from ..registry import Workflow

Progress = Callable[[float, str], None]  # (0..1, message)


class StageFailed(Exception):
    """The workflow failed (bad input, OOM, ...). Retrying with fresh VRAM may help."""


class BackendDown(Exception):
    """The compute disappeared (pod died, endpoint unreachable). Retry after the backend recovers."""


class Backend(ABC):
    name = "base"
    rate_per_s = 0.0  # $ per GPU-second, for per-stage cost attribution

    async def start(self) -> None:
        """Optional warm-up (boot pods, check endpoint)."""

    async def close(self) -> None:
        """Release everything that costs money. Must be idempotent."""

    @abstractmethod
    async def run(self, wf: Workflow, values: dict, out_dir: Path, progress: Progress | None = None) -> list[Path]:
        """Execute `wf`. Media values are local Paths; return the files written into out_dir."""

    def spend(self) -> float:
        return 0.0

    def cost_of(self, seconds: float) -> float:
        return seconds * self.rate_per_s
