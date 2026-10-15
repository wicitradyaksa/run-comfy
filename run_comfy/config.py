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
    docker_args: str = ""            # start command override (set by `run_comfy setup` for the worker image)
    network_volume_id: str = ""
    data_center_id: str = ""
    container_disk_gb: int = 40
    volume_gb: int = 0
    comfy_port: int = 8188
    env: dict = field(default_factory=dict)
    hourly_rate_estimate_usd: float = 0.80
    # cheap GPUs tried first for the model-download pod (it only needs bandwidth, not compute)
    seed_gpu_types: list[str] = field(default_factory=lambda: [
        "NVIDIA RTX A4000", "NVIDIA RTX A4500", "NVIDIA RTX A5000", "NVIDIA GeForce RTX 3090"])
    seed_hourly_rate_usd: float = 0.25


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
    # automatic image choice (`run_comfy setup`): stock worker when only core nodes are needed,
    # otherwise the image built by .github/workflows/build-worker.yml
    base_image: str = "runpod/worker-comfyui:5.5.0-base"
    custom_image_repo: str = ""      # e.g. "ghcr.io/<you>/run-comfy-worker"; tag = custom-node hash


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
class FactoryCfg:
    data_dir: str = "data"
    workflows_dir: str = "workflows"
    backend: str = "demo"              # demo | pod | serverless | url
    comfy_url: str = ""                # for backend = "url"
    max_parallel_variants: int = 4
    max_spend_usd: float = 25.0        # per batch
    anthropic_model: str = "claude-opus-5-5"
    qa: bool = True
    qa_threshold: int = 7              # 0-10, Claude vision score needed to pass
    qa_max_regens: int = 1             # automatic re-renders of the suspect stage
    target_lufs: float = -14.0
    caption_style: str = "bold_pop"    # bold_pop | karaoke | minimal | none
    demo_delay_s: float = 0.0          # make the demo backend feel real in walkthroughs
    auto_provision: bool = True        # pod/serverless batches create the volume, endpoint and models first
    volume_headroom: float = 1.25      # volume size = models x this
    default_data_center: str = "EU-RO-1"


@dataclass
class VoiceCfg:
    provider: str = "demo"             # demo | elevenlabs | comfy
    elevenlabs_model: str = "eleven_multilingual_v2"
    comfy_workflow: str = "tts"        # registry name when provider = "comfy"


@dataclass
class StorageCfg:
    s3_bucket: str = ""                # empty = local only
    s3_prefix: str = "run-comfy/"
    s3_endpoint_url: str = ""          # R2 / MinIO / etc.
    public_base_url: str = ""          # if set, links use this instead of presigned URLs


@dataclass
class WebhookCfg:
    url: str = ""
    secret: str = ""                   # "whsec_..." (Standard Webhooks); empty = unsigned


@dataclass
class Config:
    runpod: RunPodCfg
    pipeline: PipelineCfg
    post: PostCfg
    serverless: ServerlessCfg
    root: Path
    config_file: Path | None = None
    factory: FactoryCfg = field(default_factory=FactoryCfg)
    voice: VoiceCfg = field(default_factory=VoiceCfg)
    storage: StorageCfg = field(default_factory=StorageCfg)
    webhooks: WebhookCfg = field(default_factory=WebhookCfg)
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


def set_value(path: Path, section: str, key: str, value: str) -> bool:
    """Write `key = "value"` into [section] of a TOML file in place, keeping comments. Returns True on success."""
    import re

    text = path.read_text() if path.exists() else ""
    m = re.search(rf"(?m)^\[{re.escape(section)}\]\s*$", text)
    line = f'{key} = "{value}"'
    if not m:
        path.write_text(text.rstrip() + f"\n\n[{section}]\n{line}\n")
        return True
    start = m.end()
    nxt = re.search(r"(?m)^\[", text[start:])
    end = start + nxt.start() if nxt else len(text)
    body = text[start:end]
    new_body, n = re.subn(rf'(?m)^{re.escape(key)}\s*=\s*"[^"\n]*"', line, body, count=1)
    if not n:
        new_body = "\n" + line + body  # key not present yet: first line of the section
    path.write_text(text[:start] + new_body + text[end:])
    return True


def load(path: str | Path = "config.toml") -> Config:
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    cfg = Config(
        runpod=_build(RunPodCfg, data.get("runpod", {})),
        pipeline=_build(PipelineCfg, data.get("pipeline", {})),
        post=_build(PostCfg, data.get("post", {})),
        serverless=_build(ServerlessCfg, data.get("serverless", {})),
        root=path.parent,
        config_file=path,
        factory=_build(FactoryCfg, data.get("factory", {})),
        voice=_build(VoiceCfg, data.get("voice", {})),
        storage=_build(StorageCfg, data.get("storage", {})),
        webhooks=_build(WebhookCfg, data.get("webhooks", {})),
        api_key=os.environ.get("RUNPOD_API_KEY", ""),
    )
    if cfg.pipeline.backend not in ("pod", "serverless"):
        raise ValueError(f"[pipeline].backend must be 'pod' or 'serverless', not {cfg.pipeline.backend!r}")
    if cfg.factory.backend not in ("demo", "pod", "serverless", "url"):
        raise ValueError(f"[factory].backend must be demo|pod|serverless|url, not {cfg.factory.backend!r}")
    return cfg
