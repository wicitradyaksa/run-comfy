"""Load and validate config.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunPodCfg:
    name_prefix: str = "run-comfy"
    gpu_types: list[str] = field(default_factory=lambda: ["NVIDIA GeForce RTX 4090"])
    cloud_type: str = "SECURE"
    image: str = ""
    template_id: str = ""
    network_volume_id: str = ""
    data_center_id: str = ""
    container_disk_gb: int = 40
    volume_gb: int = 0
    comfy_port: int = 8188
    env: dict = field(default_factory=dict)
    hourly_rate_estimate_usd: float = 0.80


@dataclass
class ServerlessCfg:
    endpoint_id: str = ""            # filled in by `run_comfy serverless-setup`
    max_concurrent: int = 3          # jobs in flight; match the endpoint's max workers
    cost_per_sec_usd: float = 0.00031  # per active worker; estimate, see your endpoint's pricing
    poll_interval_s: float = 2.0
    # used only by `serverless-setup`
    name: str = "run-comfy"
    image: str = ""
    gpu_ids: str = "ADA_24,AMPERE_24"
    workers_max: int = 3
    idle_timeout_s: int = 5
    network_volume_id: str = ""
    container_disk_gb: int = 20
    env: dict = field(default_factory=dict)


@dataclass
class PipelineCfg:
    backend: str = "pod"             # "pod" or "serverless"
    workflow: str = "workflows/workflow_api.json"
    jobs: str = "jobs.csv"
    output_dir: str = "output"
    pods: int = 1
    inflight_per_pod: int = 2
    max_retries: int = 2
    boot_timeout_s: int = 1200
    job_timeout_s: int = 1800
    max_runtime_min: int = 240
    max_spend_usd: float = 25.0
    max_pod_restarts: int = 1


@dataclass
class PostCfg:
    concat: bool = False
    concat_output: str = "final.mp4"
    loudnorm: bool = True


@dataclass
class Config:
    runpod: RunPodCfg
    pipeline: PipelineCfg
    post: PostCfg
    serverless: ServerlessCfg
    root: Path
    api_key: str = ""

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.root / q


def _build(cls, data: dict):
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown keys in [{cls.__name__}]: {sorted(unknown)}")
    return cls(**data)


def load(path: str | Path = "config.toml") -> Config:
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    cfg = Config(
        runpod=_build(RunPodCfg, data.get("runpod", {})),
        pipeline=_build(PipelineCfg, data.get("pipeline", {})),
        post=_build(PostCfg, data.get("post", {})),
        serverless=_build(ServerlessCfg, data.get("serverless", {})),
        root=path.parent,
        api_key=os.environ.get("RUNPOD_API_KEY", ""),
    )
    if cfg.pipeline.backend not in ("pod", "serverless"):
        raise ValueError(f"[pipeline].backend must be 'pod' or 'serverless', not {cfg.pipeline.backend!r}")
    return cfg
