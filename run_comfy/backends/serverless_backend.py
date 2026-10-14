"""RunPod Serverless (worker-comfyui) as a stage backend."""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path

import httpx

from ..registry import MEDIA, Workflow, bind, resolve_values
from ..serverless import (MAX_PAYLOAD, EndpointClient, ServerlessError, execute_request, output_items,
                          save_items)
from .base import Backend, BackendDown, Progress, StageFailed


class ServerlessBackend(Backend):
    name = "serverless"

    def __init__(self, client: EndpointClient, cost_per_sec: float, job_timeout_s: float = 1800,
                 poll_s: float = 2.0):
        self.client = client
        self.rate_per_s = cost_per_sec
        self.job_timeout_s, self.poll_s = job_timeout_s, poll_s
        self.billed_s = 0.0
        self.running: dict[str, float] = {}

    def spend(self) -> float:
        live = sum(time.monotonic() - t for t in self.running.values())
        return (self.billed_s + live) * self.rate_per_s

    async def close(self):
        await self.client.close()

    async def run(self, wf: Workflow, values: dict, out_dir: Path, progress: Progress | None = None) -> list[Path]:
        vals = resolve_values(wf, values)
        images = []
        for name, spec in wf.inputs.items():
            if spec.type in MEDIA and name in vals:
                p = Path(vals[name])
                remote = re.sub(r"[^\w.-]", "_", f"{wf.name}_{name}_{p.stem[:40]}{p.suffix}")
                images.append({"name": remote, "image": base64.b64encode(p.read_bytes()).decode()})
                vals[name] = remote
        payload = {"input": {"workflow": bind(wf, vals)},
                   "policy": {"executionTimeout": int(self.job_timeout_s * 1000)}}
        if images:
            payload["input"]["images"] = images
        import json
        if len(json.dumps(payload)) > MAX_PAYLOAD:
            raise StageFailed(f"{wf.name}: request over 10 MB - use smaller inputs or S3 inputs")

        def on_status(st):
            if progress:
                progress(0.1 if st == "IN_QUEUE" else 0.5 if st == "IN_PROGRESS" else 1.0, st.lower())

        try:
            st, exec_s = await execute_request(self.client, payload, self.job_timeout_s + 3600, self.poll_s,
                                               self.running, on_status)
        except ServerlessError as e:
            self.billed_s += getattr(e, "exec_s", 0.0)
            if "HTTP" in str(e) and not str(e).startswith(("FAILED", "TIMED_OUT", "CANCELLED")):
                raise BackendDown(str(e)) from e
            raise StageFailed(str(e)) from e
        except httpx.TransportError as e:
            raise BackendDown(str(e)) from e
        self.billed_s += exec_s
        items = output_items(st.get("output"))
        out_dir.mkdir(parents=True, exist_ok=True)
        return await save_items(self.client, items,
                                lambda i, n, fn: out_dir / f"{i:02d}_{Path(fn).name}")
