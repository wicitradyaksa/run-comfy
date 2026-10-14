"""End-to-end tests against a simulated ComfyUI server and a fake RunPod."""
import asyncio
import json
import shutil
import uuid
from pathlib import Path

import httpx
import pytest

from run_comfy import config as config_mod
from run_comfy.comfy import ComfyClient
from run_comfy.jobs import load_jobs, render, validate
from run_comfy.runner import Runner

ROOT = Path(__file__).resolve().parents[1]


class FakeComfy:
    """Implements just enough of ComfyUI's HTTP API."""

    def __init__(self, fail_first: set[str] = frozenset(), die_after: int | None = None):
        self.history, self.uploads, self.submitted = {}, [], []
        self.fail_first = set(fail_first)  # job ids whose first execution errors (e.g. OOM)
        self.die_after = die_after         # simulate pod crash after N completed jobs
        self.freed = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        if self.die_after is not None and len(self.submitted) > self.die_after:
            raise httpx.ConnectError("pod gone", request=req)
        p = req.url.path
        if p == "/system_stats":
            return httpx.Response(200, json={"system": {}})
        if p == "/upload/image":
            name = f"up_{len(self.uploads)}.png"
            self.uploads.append(name)
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
        if p == "/prompt":
            wf = json.loads(req.content)["prompt"]
            job = wf["9"]["inputs"]["filename_prefix"].split("/")[-1]
            pid = uuid.uuid4().hex
            self.submitted.append((job, wf))
            if job in self.fail_first:
                self.fail_first.discard(job)
                self.history[pid] = {"status": {"status_str": "error", "completed": False, "messages": [
                    ["execution_error", {"node_type": "KSampler",
                                         "exception_message": "CUDA out of memory"}]]}, "outputs": {}}
            else:
                self.history[pid] = {"status": {"status_str": "success", "completed": True},
                                     "outputs": {"9": {"images": [
                                         {"filename": f"{job}_00001_.png", "subfolder": "run_comfy", "type": "output"},
                                         {"filename": "preview.png", "subfolder": "", "type": "temp"}]}}}
            return httpx.Response(200, json={"prompt_id": pid})
        if p.startswith("/history/"):
            pid = p.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: self.history[pid]} if pid in self.history else {})
        if p == "/view":
            return httpx.Response(200, content=f"PNG:{req.url.params['filename']}".encode())
        if p in ("/free", "/interrupt"):
            self.freed += 1
            return httpx.Response(200)
        return httpx.Response(404)


class FakePods:
    def __init__(self, servers: list[FakeComfy], rate=0.5):
        self.servers, self.rate = servers, rate
        self.live, self.created, self.terminated = {}, [], []

    async def create(self, idx):
        pid = f"pod{len(self.created)}"
        self.created.append(pid)
        self.live[pid] = {}
        return pid, self.rate

    async def status(self, pid):
        return "RUNNING"

    def url(self, pid):
        return f"http://{pid}"

    async def terminate(self, pid):
        self.terminated.append(pid)
        self.live.pop(pid, None)


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT / "workflows", tmp_path / "workflows")
    shutil.copy(ROOT / "jobs.csv", tmp_path / "jobs.csv")
    (tmp_path / "config.toml").write_text(
        '[pipeline]\npods = 1\ninflight_per_pod = 2\nmax_retries = 2\nmax_pod_restarts = 1\n')
    return tmp_path


def make_runner(project, servers, pods=None, **cfg_over):
    cfg = config_mod.load(project / "config.toml")
    for k, v in cfg_over.items():
        setattr(cfg.pipeline, k, v)

    def factory(url):
        server = servers[0] if pods is None else servers[int(url.rsplit("pod", 1)[1])]
        return ComfyClient(url, transport=httpx.MockTransport(server.handler))

    r = Runner(cfg, pods=pods, base_url=None if pods else "http://local",
               client_factory=factory, poll_interval=0.01, boot_interval=0.01)
    r.watchdog_interval = 0.01
    return r


async def test_happy_path_existing_server(project):
    srv = FakeComfy()
    s = await make_runner(project, [srv]).run()
    assert (s["done"], s["failed"], s["not_run"]) == (3, 0, 0)
    out = project / "output"
    assert (out / "cat_astronaut.png").read_bytes() == b"PNG:cat_astronaut_00001_.png"
    assert not (out / "preview.png").exists()  # temp previews are skipped
    # prompt text substituted, seed typed as int, explicit seed respected
    wf = dict(srv.submitted)["neon_city"]
    assert wf["6"]["inputs"]["text"].startswith("rainy cyberpunk")
    assert wf["3"]["inputs"]["seed"] == 42
    assert isinstance(dict(srv.submitted)["cat_astronaut"]["3"]["inputs"]["seed"], int)
    assert "neon_city,done" in (out / "report.csv").read_text()


async def test_resume_skips_finished_jobs(project):
    await make_runner(project, [FakeComfy()]).run()
    srv2 = FakeComfy()
    s = await make_runner(project, [srv2]).run()
    assert s["done"] == 3 and srv2.submitted == []


async def test_oom_is_retried_after_freeing_vram(project):
    srv = FakeComfy(fail_first={"neon_city"})
    s = await make_runner(project, [srv]).run()
    assert s["done"] == 3
    assert [j for j, _ in srv.submitted].count("neon_city") == 2
    assert srv.freed >= 1


async def test_permanent_failure_is_reported_not_fatal(project):
    srv = FakeComfy()
    srv.fail_first = {"neon_city"}

    orig = srv.handler

    def always_fail(req):  # neon_city fails every time
        if req.url.path == "/prompt":
            srv.fail_first.add("neon_city")
        return orig(req)

    srv.handler = always_fail
    s = await make_runner(project, [srv], max_retries=1).run()
    assert (s["done"], s["failed"]) == (2, 1)
    assert "CUDA out of memory" in (project / "output" / "report.csv").read_text()


async def test_dead_pod_is_replaced_and_everything_torn_down(project):
    servers = [FakeComfy(die_after=1), FakeComfy()]
    pods = FakePods(servers)
    s = await make_runner(project, servers, pods=pods, inflight_per_pod=1).run()
    assert s["done"] == 3
    assert pods.created == ["pod0", "pod1"]
    assert sorted(pods.terminated) == ["pod0", "pod1"] and pods.live == {}


async def test_multi_pod_shares_queue(project):
    class Busy(FakeComfy):  # rendering takes time, like a real GPU
        async def handler(self, req):
            await asyncio.sleep(0.02)
            return FakeComfy.handler(self, req)

    servers = [Busy(), Busy()]
    pods = FakePods(servers)
    r = make_runner(project, servers, pods=pods, inflight_per_pod=1)
    r.pc.pods = 2
    s = await r.run()
    assert s["done"] == 3 and pods.created == ["pod0", "pod1"] and pods.live == {}
    assert servers[0].submitted and servers[1].submitted  # both GPUs did work


async def test_budget_stop_terminates_pods(project):
    class Slow(FakeComfy):
        def handler(self, req):
            if req.url.path.startswith("/history/"):
                return httpx.Response(200, json={})  # never finishes
            return super().handler(req)

    servers = [Slow()]
    pods = FakePods(servers, rate=1e9)  # absurd $/hr -> budget trips immediately
    s = await make_runner(project, servers, pods=pods).run()
    assert s["done"] == 0 and s["not_run"] + s["failed"] == 3
    assert pods.live == {} and pods.terminated == ["pod0"]


async def test_uploads_local_files(project):
    (project / "faces").mkdir()
    (project / "faces" / "a.png").write_bytes(b"x")
    wf = json.loads((project / "workflows/workflow_api.json").read_text())
    wf["10"] = {"class_type": "LoadImage", "inputs": {"image": "{{face}}"}}
    (project / "workflows/workflow_api.json").write_text(json.dumps(wf))
    (project / "jobs.csv").write_text("id,prompt,negative,face\nj1,hi,lo,faces/a.png\n")
    srv = FakeComfy()
    s = await make_runner(project, [srv]).run()
    assert s["done"] == 1 and srv.uploads == ["up_0.png"]
    assert srv.submitted[0][1]["10"]["inputs"]["image"] == "up_0.png"


def test_validate_catches_missing_columns(project):
    (project / "jobs.csv").write_text("id,prompt\nj1,hello\n")
    wf = json.loads((project / "workflows/workflow_api.json").read_text())
    errs = validate(wf, load_jobs(project / "jobs.csv"))
    assert any("negative" in e for e in errs)
    assert validate({"prompt": "{{x}}"}, []) != []  # UI-format / empty detected


def test_render_types():
    assert render({"a": "{{n}}", "b": "x {{n}} y", "c": ["{{f}}"]}, {"n": "7", "f": "0.5"}) == \
        {"a": 7, "b": "x 7 y", "c": [0.5]}
