"""Engine v2: registry, stage executor + cache, backends, webhooks, provisioning."""
import asyncio
import base64
import json
import uuid
from pathlib import Path

import httpx
import pytest

from run_comfy import media, provision, webhooks
from run_comfy.backends.base import Backend, StageFailed
from run_comfy.backends.comfy_pool import ComfyPoolBackend
from run_comfy.backends.demo import DemoBackend, synth_speech
from run_comfy.backends.serverless_backend import ServerlessBackend
from run_comfy.comfy import ComfyClient
from run_comfy.registry import bind, check_against_server, load_registry, resolve_values
from run_comfy.serverless import EndpointClient
from run_comfy.stages import Executor, Ref, Stage
from run_comfy.store import StageRun, Store

ROOT = Path(__file__).resolve().parents[1]
REG = load_registry(ROOT / "workflows")


# ---------------- registry ----------------
def test_registry_loads_pack_and_binds_typed_inputs():
    assert {"flux_txt2img", "wan22_s2v", "wan22_i2v", "flux_kontext_scene"} <= set(REG)
    wf = REG["flux_txt2img"]
    vals = resolve_values(wf, {"prompt": "a red shoe", "seed": "7", "width": 512})
    g = bind(wf, vals)
    assert g["4"]["inputs"]["text"] == "a red shoe" and g["7"]["inputs"]["seed"] == 7
    assert g["6"]["inputs"]["width"] == 512 and g["6"]["inputs"]["height"] == 1344  # default
    assert wf.graph["4"]["inputs"]["text"] == ""  # original untouched
    rnd = resolve_values(wf, {"prompt": "x"})["seed"]
    assert isinstance(rnd, int) and rnd >= 0  # -1 default -> random


def test_registry_missing_required_input():
    with pytest.raises(ValueError, match="missing input 'prompt'"):
        resolve_values(REG["flux_txt2img"], {})


def test_wan_frames_derived_from_voice_length(tmp_path):
    wav, _ = synth_speech("one two three four five six seven", tmp_path / "v.wav")
    secs = media.duration(wav)
    n = resolve_values(REG["wan22_s2v"], {"image": "x.png", "audio": wav})["length"]
    assert (n - 1) % 4 == 0 and n >= secs * 16


def test_check_against_server_reports_problems():
    info = {"LoadImage": {"input": {"required": {"image": []}}},
            "SaveImage": {"input": {"required": {"images": [], "filename_prefix": []}}},
            "UpscaleModelLoader": {"input": {"required": {"model_name": []}}},
            "ImageUpscaleWithModel": {"input": {"required": {"upscale_model": [], "image": []}}}}
    probs = check_against_server(REG["upscale_2x"], info)
    assert probs == ["node 4: class 'ImageScaleBy' not installed"]


# ---------------- executor ----------------
class CountingBackend(DemoBackend):
    def __init__(self, fail_times=0, delay=0.0):
        super().__init__()
        self.calls, self.fail_times, self.delay = [], fail_times, delay

    async def run(self, wf, values, out_dir, progress=None):
        self.calls.append(wf.name)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_times:
            self.fail_times -= 1
            raise StageFailed("CUDA out of memory")
        return await super().run(wf, values, out_dir, progress)


def _stages(prompt, motion="slow push in"):
    return [
        Stage("key", workflow="flux_txt2img", inputs={"prompt": prompt, "seed": 1, "width": 320, "height": 576}),
        Stage("clip", workflow="wan22_i2v", inputs={"image": Ref("key"), "prompt": motion, "seed": 2,
                                                    "seconds": 1, "width": 320, "height": 576}),
    ]


async def test_cache_reuses_unchanged_stages(tmp_path):
    store = Store(tmp_path)
    be = CountingBackend()
    ex = Executor(store, REG, be)
    r1 = await ex.run(_stages("a lake"))
    assert media.duration(r1["clip"][0]) > 0.5
    assert be.calls == ["flux_txt2img", "wan22_i2v"]
    await ex.run(_stages("a lake"))                      # identical -> all cached
    assert be.calls == ["flux_txt2img", "wan22_i2v"]
    await ex.run(_stages("a lake", motion="orbit"))      # only the clip changes
    assert be.calls == ["flux_txt2img", "wan22_i2v", "wan22_i2v"]
    assert ex.stats == {"rendered": 3, "cached": 3, "deduped": 0}
    runs = store.list(StageRun)
    assert sum(r.cached for r in runs) == 3


async def test_identical_stages_in_parallel_render_once(tmp_path):
    be = CountingBackend(delay=0.05)
    ex = Executor(Store(tmp_path), REG, be)
    await asyncio.gather(*(ex.run(_stages("same"), variant_id=i) for i in range(4)))
    assert be.calls.count("flux_txt2img") == 1 and be.calls.count("wan22_i2v") == 1
    assert ex.stats["deduped"] == 6


async def test_stage_retry_then_success(tmp_path):
    be = CountingBackend(fail_times=1)
    ex = Executor(Store(tmp_path), REG, be)
    out = await ex.run(_stages("retry me")[:1])
    assert out["key"][0].exists() and be.calls == ["flux_txt2img", "flux_txt2img"]


async def test_local_fn_stage_and_bad_dag(tmp_path):
    def concat(ctx, inputs):
        p = ctx.out_dir / "x.txt"
        p.write_text(inputs["a"] + inputs["b"])
        return [p]

    ex = Executor(Store(tmp_path), REG, DemoBackend())
    out = await ex.run([Stage("cat", fn=concat, inputs={"a": "x", "b": "y"})])
    assert out["cat"][0].read_text() == "xy"
    with pytest.raises(ValueError, match="unknown"):
        await ex.run([Stage("s", fn=concat, inputs={"a": Ref("nope")})])


# ---------------- comfy pool on a fake ComfyUI + fake RunPod ----------------
class FakeComfy:
    def __init__(self, die_after=None):
        self.history, self.uploads, self.submitted, self.die_after = {}, [], [], die_after

    def handler(self, req):
        if self.die_after is not None and len(self.submitted) > self.die_after:
            raise httpx.ConnectError("pod gone", request=req)
        p = req.url.path
        if p == "/system_stats":
            return httpx.Response(200, json={})
        if p == "/upload/image":
            self.uploads.append(req.content[:10])
            return httpx.Response(200, json={"name": f"up{len(self.uploads)}.png", "subfolder": ""})
        if p == "/prompt":
            pid = uuid.uuid4().hex
            self.submitted.append(json.loads(req.content)["prompt"])
            self.history[pid] = {"status": {"status_str": "success", "completed": True},
                                 "outputs": {"10": {"images": [{"filename": "out.png", "type": "output"}]}}}
            return httpx.Response(200, json={"prompt_id": pid})
        if p.startswith("/history/"):
            pid = p.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: self.history[pid]})
        if p == "/view":
            return httpx.Response(200, content=b"PNGDATA")
        return httpx.Response(200, json={})


class FakePods:
    def __init__(self):
        self.created, self.terminated, self.live = [], [], {}

    async def create(self, idx, **kw):
        pid = f"pod{len(self.created)}"
        self.created.append(pid)
        self.live[pid] = 1
        return pid, 0.72

    async def status(self, pid):
        return "RUNNING"

    def url(self, pid):
        return f"http://{pid}"

    async def terminate(self, pid):
        self.terminated.append(pid)
        self.live.pop(pid, None)


def _factory(servers):
    return lambda url: ComfyClient(url, transport=httpx.MockTransport(servers[url.rsplit("/", 1)[1]].handler))


async def test_pool_binds_uploads_and_downloads(tmp_path):
    srv = FakeComfy()
    face = tmp_path / "face.png"
    face.write_bytes(b"FACE")
    be = ComfyPoolBackend(base_url="http://local", client_factory=_factory({"local": srv}), poll_s=0.01)
    out = await be.run(REG["avatar_pulid"], {"prompt": "presenter", "face": face, "seed": 3}, tmp_path / "o")
    assert out[0].read_bytes() == b"PNGDATA"
    g = srv.submitted[0]
    assert g["23"]["inputs"]["image"] == "up1.png" and g["4"]["inputs"]["text"] == "presenter"
    await be.close()


async def test_pool_replaces_dead_pod_and_tears_down(tmp_path):
    servers = {"pod0": FakeComfy(die_after=1), "pod1": FakeComfy()}
    pods = FakePods()
    be = ComfyPoolBackend(pods=pods, n_pods=1, client_factory=_factory(servers), poll_s=0.01,
                          boot_interval_s=0.01, max_restarts=1)
    ex = Executor(Store(tmp_path), REG, be)
    for i in range(3):
        await ex.run([Stage("img", workflow="flux_txt2img", inputs={"prompt": f"p{i}", "seed": i})])
    assert pods.created == ["pod0", "pod1"] and pods.terminated == ["pod0"]
    assert be.spend() > 0
    await be.close()
    assert pods.live == {} and sorted(pods.terminated) == ["pod0", "pod1"]


# ---------------- serverless backend ----------------
async def test_serverless_backend_roundtrip(tmp_path):
    seen = {}

    def handler(req):
        if req.url.path.endswith("/run"):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "r1"})
        if "/status/" in req.url.path:
            return httpx.Response(200, json={"status": "COMPLETED", "executionTime": 2000, "output": {
                "images": [{"filename": "a.png", "type": "base64", "data": base64.b64encode(b"IMG").decode()}]}})
        return httpx.Response(200, json={})

    img = tmp_path / "p.png"
    img.write_bytes(b"PRODUCT")
    be = ServerlessBackend(EndpointClient("ep", "k", transport=httpx.MockTransport(handler)), 0.0005, poll_s=0.001)
    out = await be.run(REG["flux_kontext_scene"], {"image": img, "prompt": "on a beach", "seed": 1}, tmp_path / "o")
    assert out[0].read_bytes() == b"IMG"
    inp = seen["body"]["input"]
    name = inp["workflow"]["4"]["inputs"]["image"]
    assert inp["images"][0]["name"] == name and base64.b64decode(inp["images"][0]["image"]) == b"PRODUCT"
    assert be.spend() == pytest.approx(2 * 0.0005)


# ---------------- webhooks ----------------
async def test_webhook_is_signed_and_verifiable():
    secret = "whsec_" + base64.b64encode(b"supersecretkey123").decode()
    got = {}

    def handler(req):
        got["headers"], got["body"] = dict(req.headers), req.content
        return httpx.Response(204)

    ok = await webhooks.send("https://hook.example/x", secret, "batch.completed", {"id": 1},
                             transport=httpx.MockTransport(handler))
    assert ok and json.loads(got["body"])["type"] == "batch.completed"
    assert webhooks.verify(secret, got["headers"], got["body"])
    assert not webhooks.verify(secret, got["headers"], got["body"] + b"tampered")


# ---------------- provisioning ----------------
def test_dockerfile_pins_nodes_and_optionally_bakes_models():
    df = provision.dockerfile(REG, "runpod/worker-comfyui:5.5.0-base", bake_models=True,
                              names=["avatar_pulid", "upscale_2x"])
    assert df.startswith("# syntax=docker/dockerfile")
    assert "FROM runpod/worker-comfyui:5.5.0-base" in df
    assert "git clone https://github.com/balazik/ComfyUI-PuLID-Flux" in df
    assert "ADD https://github.com/xinntao/Real-ESRGAN" in df
    assert sum("flux1-dev.safetensors" in line for line in df.splitlines()) == 1  # shared models de-duplicated


async def test_seed_volume_polls_until_finished_and_terminates():
    polls = {"n": 0}

    def handler(req):
        polls["n"] += 1
        state = "finished" if polls["n"] >= 2 else "running"
        return httpx.Response(200, json={"state": state, "done": ["a"] if state == "finished" else [],
                                         "failed": [], "bytes": 10, "current": None})

    class Pods(FakePods):
        cfg = type("C", (), {"name_prefix": "run-comfy", "gpu_types": ["NVIDIA GeForce RTX 4090"],
                             "seed_gpu_types": ["NVIDIA RTX A4000"]})()

        async def create(self, idx, **kw):
            self.kw = kw
            return await super().create(idx)

    pods = Pods()
    st = await provision.seed_volume(pods, [{"url": "https://x/a", "path": "models/a"}], poll_s=0.001,
                                     transport=httpx.MockTransport(handler))
    assert st["state"] == "finished" and pods.terminated == ["pod0"]
    assert pods.kw["ports"] == "8000/http" and "base64 -d" in pods.kw["docker_args"]
    assert pods.kw["volume_mount_path"] == "/runpod-volume"  # where the SDK mounts the network volume
    assert pods.kw["gpu_types"] == ["NVIDIA RTX A4000", "NVIDIA GeForce RTX 4090"]  # cheap GPU first
    script = base64.b64decode(pods.kw["docker_args"].split("echo ")[1].split(" |")[0]).decode()
    compile(script, "seed.py", "exec")  # the script we ship to the pod is valid Python
    assert "ROOT = '/runpod-volume'" in script


def test_seed_script_really_downloads_and_verifies(tmp_path):
    """Run the exact script we ship to the seed pod, against a local HTTP server standing in for HF."""
    import hashlib
    import http.server
    import socket
    import subprocess
    import sys
    import threading
    import time
    import urllib.request

    src = tmp_path / "srv"
    src.mkdir()
    (src / "a.safetensors").write_bytes(b"A" * 5_000_000)
    (src / "b.pth").write_bytes(b"B" * 1000)
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(src), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    root = tmp_path / "volume"
    (root / "models/vae").mkdir(parents=True)
    (root / "models/vae/b.pth").write_bytes(b"B" * 1000)  # already present -> not re-downloaded
    models = [
        {"url": f"{base}/a.safetensors", "path": "models/unet/a.safetensors",
         "sha256": hashlib.sha256(b"A" * 5_000_000).hexdigest()},
        {"url": f"{base}/b.pth", "path": "models/vae/b.pth"},
        {"url": f"{base}/a.safetensors", "path": "models/bad.bin", "sha256": "0" * 64},
        {"url": f"{base}/missing", "path": "models/missing.bin"},
    ]
    cmd = provision.seed_command(models, root=str(root))
    script = base64.b64decode(cmd.split("echo ")[1].split(" |")[0]).decode()
    (tmp_path / "seed.py").write_text(script)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {"SEED_PORT": str(port), "SEED_STATUS_DIR": str(tmp_path / "status"), "PATH": "/usr/bin:/bin"}
    proc = subprocess.Popen([sys.executable, "-I", str(tmp_path / "seed.py")], env=env)
    try:
        st = {}
        for _ in range(100):
            try:
                st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json", timeout=2).read())
                if st["state"] == "finished":
                    break
            except OSError:
                pass
            time.sleep(0.1)
    finally:
        proc.kill()
        httpd.shutdown()
    assert st["state"] == "finished"
    assert sorted(st["done"]) == ["models/unet/a.safetensors", "models/vae/b.pth"]
    assert {f["path"] for f in st["failed"]} == {"models/bad.bin", "models/missing.bin"}
    assert "sha256 mismatch" in next(f["error"] for f in st["failed"] if f["path"] == "models/bad.bin")
    assert (root / "models/unet/a.safetensors").stat().st_size == 5_000_000
    assert not (root / "models/bad.bin").exists() and not (root / "models/bad.bin.part").exists()
    assert st["files"]["models/vae/b.pth"] == {"sha256": hashlib.sha256(b"B" * 1000).hexdigest(), "bytes": 1000}
