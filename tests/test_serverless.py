"""Serverless backend against a simulated RunPod Serverless endpoint (worker-comfyui)."""
import asyncio
import base64
import json
import shutil
from pathlib import Path

import httpx
import pytest

from run_comfy import config as config_mod
from run_comfy.serverless import EndpointClient, ServerlessRunner, output_items

ROOT = Path(__file__).resolve().parents[1]


class FakeEndpoint:
    def __init__(self, fail_first=(), always_fail=(), output_style="v5", never_finish=False):
        self.reqs: dict[str, dict] = {}
        self.polls: dict[str, int] = {}
        self.fail_first, self.always_fail = set(fail_first), set(always_fail)
        self.style, self.never_finish = output_style, never_finish
        self.cancelled, self.peak, self.active = [], 0, set()

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.url.host in ("api.runpod.ai", "bucket.example")
        if req.url.host == "bucket.example":
            return httpx.Response(200, content=b"S3:" + req.url.path.encode())
        assert req.headers["authorization"] == "Bearer k"
        p = req.url.path
        if p.endswith("/run"):
            body = json.loads(req.content)
            rid = f"r{len(self.reqs)}"
            self.reqs[rid] = body
            self.polls[rid] = 0
            self.active.add(rid)
            self.peak = max(self.peak, len(self.active))
            return httpx.Response(200, json={"id": rid, "status": "IN_QUEUE"})
        if "/status/" in p:
            rid = p.rsplit("/", 1)[1]
            self.polls[rid] += 1
            if self.never_finish or self.polls[rid] < 3:
                return httpx.Response(200, json={"id": rid, "status": "IN_QUEUE" if self.polls[rid] == 1 else "IN_PROGRESS"})
            self.active.discard(rid)
            job = self.reqs[rid]["input"]["workflow"]["9"]["inputs"]["filename_prefix"].split("/")[-1]
            if job in self.always_fail or job in self.fail_first:
                self.fail_first.discard(job)
                return httpx.Response(200, json={"id": rid, "status": "FAILED", "executionTime": 1000,
                                                 "error": "CUDA out of memory"})
            out = {
                "v5": {"images": [{"filename": f"{job}.png", "type": "base64",
                                   "data": base64.b64encode(f"IMG:{job}".encode()).decode()}]},
                "s3": {"images": [{"filename": f"{job}.mp4", "type": "s3_url",
                                   "data": f"https://bucket.example/{job}.mp4?sig=x"}]},
                "legacy": {"status": "success", "message": base64.b64encode(b"OLD").decode()},
            }[self.style]
            return httpx.Response(200, json={"id": rid, "status": "COMPLETED", "executionTime": 4000,
                                             "delayTime": 100, "output": out})
        if "/cancel/" in p:
            rid = p.rsplit("/", 1)[1]
            self.cancelled.append(rid)
            self.active.discard(rid)
            return httpx.Response(200, json={"id": rid, "status": "CANCELLED"})
        return httpx.Response(404)


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT / "workflows", tmp_path / "workflows")
    shutil.copy(ROOT / "jobs.csv", tmp_path / "jobs.csv")
    (tmp_path / "config.toml").write_text(
        '[pipeline]\nbackend = "serverless"\nmax_retries = 2\n'
        '[serverless]\nendpoint_id = "ep1"\nmax_concurrent = 2\npoll_interval_s = 0.001\n')
    return tmp_path


def make(project, ep: FakeEndpoint, **pipeline):
    cfg = config_mod.load(project / "config.toml")
    for k, v in pipeline.items():
        setattr(cfg.pipeline, k, v)
    client = EndpointClient("ep1", "k", transport=httpx.MockTransport(ep.handler))
    r = ServerlessRunner(cfg, client=client)
    r.watchdog_interval = 0.005
    return r


async def test_happy_path(project):
    ep = FakeEndpoint()
    s = await make(project, ep).run()
    assert (s["done"], s["failed"], s["not_run"]) == (3, 0, 0)
    assert (project / "output/neon_city.png").read_bytes() == b"IMG:neon_city"
    wf = ep.reqs["r1"]["input"]["workflow"]
    assert wf["3"]["inputs"]["seed"] == 42 and "cyberpunk" in wf["6"]["inputs"]["text"]
    assert ep.reqs["r0"]["policy"]["executionTimeout"] == 1800 * 1000
    assert s["gpu_cost_usd"] == round(3 * 4 * 0.00031, 2)
    assert ep.peak <= 2  # max_concurrent respected


async def test_resume(project):
    await make(project, FakeEndpoint()).run()
    ep = FakeEndpoint()
    s = await make(project, ep).run()
    assert s["done"] == 3 and ep.reqs == {}


async def test_retry_then_success_and_permanent_failure(project):
    ep = FakeEndpoint(fail_first={"cat_astronaut"}, always_fail={"forest_spirit"})
    s = await make(project, ep, max_retries=1).run()
    assert (s["done"], s["failed"]) == (2, 1)
    assert "CUDA out of memory" in (project / "output/report.csv").read_text()


@pytest.mark.parametrize("style,name,content", [("s3", "cat_astronaut.mp4", b"S3:/cat_astronaut.mp4"),
                                                ("legacy", "cat_astronaut.png", b"OLD")])
async def test_output_formats(project, style, name, content):
    s = await make(project, FakeEndpoint(output_style=style)).run()
    assert s["done"] == 3 and (project / "output" / name).read_bytes() == content


async def test_input_images_sent_as_base64(project):
    (project / "faces").mkdir()
    (project / "faces/a b.png").write_bytes(b"face")
    wf = json.loads((project / "workflows/workflow_api.json").read_text())
    wf["10"] = {"class_type": "LoadImage", "inputs": {"image": "{{face}}"}}
    (project / "workflows/workflow_api.json").write_text(json.dumps(wf))
    (project / "jobs.csv").write_text("id,prompt,negative,face\nj1,hi,lo,faces/a b.png\n")
    ep = FakeEndpoint()
    assert (await make(project, ep).run())["done"] == 1
    inp = ep.reqs["r0"]["input"]
    assert inp["images"] == [{"name": "j1_face.png", "image": base64.b64encode(b"face").decode()}]
    assert inp["workflow"]["10"]["inputs"]["image"] == "j1_face.png"


async def test_oversized_payload_fails_fast(project):
    (project / "big.png").write_bytes(b"x" * 8_000_000)
    wf = json.loads((project / "workflows/workflow_api.json").read_text())
    wf["10"] = {"class_type": "LoadImage", "inputs": {"image": "{{face}}"}}
    (project / "workflows/workflow_api.json").write_text(json.dumps(wf))
    (project / "jobs.csv").write_text("id,prompt,negative,face\nj1,hi,lo,big.png\n")
    ep = FakeEndpoint()
    s = await make(project, ep).run()
    assert s["failed"] == 1 and ep.reqs == {}
    assert "limit 10 MB" in (project / "output/report.csv").read_text()


async def test_budget_cancels_inflight_requests(project):
    ep = FakeEndpoint(never_finish=True)
    r = make(project, ep)
    r.sc.cost_per_sec_usd = 1e6
    s = await r.run()
    assert s["done"] == 0
    assert sorted(ep.cancelled) == sorted(ep.reqs)  # nothing left running on RunPod


def test_output_items_reports_worker_errors():
    with pytest.raises(Exception, match="missing model"):
        output_items({"errors": ["missing model flux.safetensors"]})
