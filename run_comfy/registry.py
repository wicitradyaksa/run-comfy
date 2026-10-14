"""Workflow registry: workflows/<name>/{workflow_api.json, manifest.toml}.

The manifest turns a raw ComfyUI graph into a typed, versioned function:

    name = "flux_txt2img"
    kind = "image"                 # what it produces; also drives the demo backend
    license = "flux-1-dev-non-commercial"
    commercial_ok = false
    min_vram_gb = 24
    est_seconds = 25               # per run, for cost estimates
    verified = false               # flipped after a real-GPU run (`workflows check --url`)

    [inputs.prompt]
    type = "text"                  # text | int | float | bool | image | audio | video
    node = "6.inputs.text"         # where the value goes in the graph
    [inputs.seed]
    type = "int"
    node = "25.inputs.noise_seed"
    default = -1                   # -1 = random

    [[models]]
    url = "https://huggingface.co/.../flux1-dev.safetensors"
    path = "models/unet/flux1-dev.safetensors"
    sha256 = ""

    [[custom_nodes]]
    repo = "https://github.com/..."
    commit = "abc123"
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .jobs import PLACEHOLDER, render

MEDIA = {"image", "audio", "video"}
TYPES = {"text", "int", "float", "bool"} | MEDIA


@dataclass
class InputSpec:
    name: str
    type: str
    node: str | None = None
    default: object = None
    optional: bool = False
    description: str = ""
    derive: str = ""   # e.g. "wan_frames:audio:16" = frame count for the duration of input `audio` at 16 fps


@dataclass
class Workflow:
    name: str
    dir: Path
    graph: dict
    inputs: dict[str, InputSpec]
    kind: str
    version: str
    description: str = ""
    license: str = "unknown"
    commercial_ok: bool = True
    min_vram_gb: int = 0
    est_seconds: float = 30.0
    verified: bool = False
    models: list[dict] = field(default_factory=list)
    custom_nodes: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "version": self.version, "description": self.description,
            "license": self.license, "commercial_ok": self.commercial_ok, "min_vram_gb": self.min_vram_gb,
            "est_seconds": self.est_seconds, "verified": self.verified,
            "inputs": [vars(i) for i in self.inputs.values()],
            "models": self.models, "custom_nodes": self.custom_nodes,
        }


def load_workflow(d: Path) -> Workflow:
    graph = json.loads((d / "workflow_api.json").read_text())
    man = tomllib.loads((d / "manifest.toml").read_text()) if (d / "manifest.toml").exists() else {}
    inputs = {}
    for name, spec in (man.get("inputs") or {}).items():
        if spec.get("type", "text") not in TYPES:
            raise ValueError(f"{d.name}: input {name} has unknown type {spec.get('type')}")
        inputs[name] = InputSpec(name=name, type=spec.get("type", "text"), node=spec.get("node"),
                                 default=spec.get("default"), optional=bool(spec.get("optional", False)),
                                 description=spec.get("description", ""), derive=spec.get("derive", ""))
    # bare {{placeholders}} with no manifest entry become text inputs
    from .jobs import placeholders
    for ph in placeholders(graph):
        inputs.setdefault(ph, InputSpec(name=ph, type="text"))
    for name, spec in inputs.items():
        if spec.node and _get(graph, spec.node, missing=True) is _MISSING:
            raise ValueError(f"{d.name}: input {name} points at missing node path {spec.node}")
    blob = json.dumps({"graph": graph, "manifest": man}, sort_keys=True).encode()
    return Workflow(
        name=man.get("name", d.name), dir=d, graph=graph, inputs=inputs,
        kind=man.get("kind", "image"), version=hashlib.sha256(blob).hexdigest()[:12],
        description=man.get("description", ""), license=man.get("license", "unknown"),
        commercial_ok=bool(man.get("commercial_ok", True)), min_vram_gb=int(man.get("min_vram_gb", 0)),
        est_seconds=float(man.get("est_seconds", 30)), verified=bool(man.get("verified", False)),
        models=man.get("models", []), custom_nodes=man.get("custom_nodes", []),
    )


def load_registry(root: Path) -> dict[str, Workflow]:
    reg = {}
    if not root.exists():
        return reg
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "workflow_api.json").exists()):
        wf = load_workflow(d)
        reg[wf.name] = wf
    return reg


_MISSING = object()


def _get(graph: dict, path: str, missing=False):
    cur = graph
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            if missing:
                return _MISSING
            raise KeyError(path)
    return cur


def _set(graph: dict, path: str, value):
    *parents, last = path.split(".")
    cur = graph
    for part in parents:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def coerce(spec: InputSpec, value):
    if value is None:
        value = spec.default
    if spec.type == "int":
        v = int(value) if value not in (None, "") else -1
        return random.randint(0, 2**48) if v == -1 and spec.name in ("seed", "noise_seed") else v
    if spec.type == "float":
        return float(value)
    if spec.type == "bool":
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
    return value


def derive_value(spec: InputSpec, values: dict):
    """Compute inputs that depend on other inputs (e.g. video length from the voice-over duration)."""
    kind, src, *rest = spec.derive.split(":")
    from .media import duration

    secs = duration(Path(values[src]))
    if kind == "frames":
        return max(1, math.ceil(secs * float(rest[0])))
    if kind == "wan_frames":  # Wan models need 4n+1 frames
        n = max(1, math.ceil(secs * float(rest[0])))
        return 4 * math.ceil((n - 1) / 4) + 1
    if kind == "seconds":
        return round(secs, 3)
    raise ValueError(f"unknown derive kind {kind!r}")


def resolve_values(wf: Workflow, values: dict) -> dict:
    """Apply defaults + types; raise on missing required inputs. Media stay as local paths here."""
    out = {}
    for name, spec in wf.inputs.items():
        if spec.derive and name not in values:
            out[name] = derive_value(spec, values)
            continue
        v = values.get(name, spec.default)
        if v is None or v == "":
            if spec.optional:
                continue
            raise ValueError(f"workflow {wf.name}: missing input {name!r}")
        out[name] = v if spec.type in MEDIA else coerce(spec, v)
    return out


def bind(wf: Workflow, values: dict) -> dict:
    """Return a concrete ComfyUI API graph. Media values must already be server-side file names."""
    g = copy.deepcopy(wf.graph)
    for name, spec in wf.inputs.items():
        if name in values and spec.node:
            _set(g, spec.node, values[name])
    leftovers = {p for p in _placeholders(g)}
    if leftovers:
        missing = leftovers - set(values)
        if missing:
            raise ValueError(f"workflow {wf.name}: no value for {sorted(missing)}")
        g = render(g, {k: str(v) for k, v in values.items()})
    return g


def _placeholders(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _placeholders(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _placeholders(v)
    elif isinstance(obj, str):
        yield from PLACEHOLDER.findall(obj)


def check_against_server(wf: Workflow, object_info: dict) -> list[str]:
    """Compare a graph with a live server's /object_info: missing node classes, unknown inputs."""
    problems = []
    for node_id, node in wf.graph.items():
        cls = node.get("class_type")
        info = object_info.get(cls)
        if info is None:
            problems.append(f"node {node_id}: class {cls!r} not installed")
            continue
        spec = info.get("input", {})
        known = set(spec.get("required", {})) | set(spec.get("optional", {}))
        for inp in node.get("inputs", {}):
            if known and inp not in known:
                problems.append(f"node {node_id} ({cls}): unknown input {inp!r}")
        for req in spec.get("required", {}):
            if req not in node.get("inputs", {}):
                problems.append(f"node {node_id} ({cls}): missing required input {req!r}")
    return problems
