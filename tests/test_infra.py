"""Automatic RunPod setup: volume, model seeding via ledger, image choice, endpoint, factory integration."""
import base64
import json
import re
import tomllib
from pathlib import Path

import httpx
import pytest

from run_comfy import config as config_mod
from run_comfy.backends.demo import DemoBackend
from run_comfy.factory.brand import Brand
from run_comfy.factory.service import Factory, FactoryError
from run_comfy.factory.writer import demo_plan
from run_comfy.infra import Infra, InfraError, Ledger
from run_comfy.registry import load_registry

ROOT = Path(__file__).resolve().parents[1]
REG = load_registry(ROOT / "workflows")


class FakeCloud:
    """RunPod REST + model hosts + seed pod status + GHCR, behind one MockTransport."""

    def __init__(self, gated=False, image_built=True):
        self.volumes, self.created_volume_body, self.seeded = {}, None, []
        self.gated, self.image_built = gated, image_built
        self.pods = None

    def handler(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if host == "rest.runpod.io":
            if req.method == "POST" and path == "/v1/networkvolumes":
                self.created_volume_body = json.loads(req.content)
                vid = f"vol{len(self.volumes) + 1}"
                self.volumes[vid] = self.created_volume_body
                return httpx.Response(201, json={"id": vid, **self.created_volume_body})
            vid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"id": vid}) if vid in self.volumes else httpx.Response(404)
        if req.method == "HEAD" and host in ("huggingface.co", "github.com"):
            if self.gated and "black-forest-labs" in path and "authorization" not in req.headers:
                return httpx.Response(401)
            return httpx.Response(200, headers={"content-length": str(2 * 10**9)})
        if host.endswith("proxy.runpod.net"):
            models = self.pods.last_models
            self.seeded.append([m["path"] for m in models])
            return httpx.Response(200, json={
                "state": "finished", "done": [m["path"] for m in models], "failed": [], "bytes": 1,
                "files": {m["path"]: {"sha256": "ab" * 32, "bytes": 1} for m in models}})
        if host == "ghcr.io":
            if path == "/token":
                return httpx.Response(200, json={"token": "t"})
            return httpx.Response(200 if self.image_built else 404)
        return httpx.Response(404)


class FakePods:
    def __init__(self, cfg):
        self.cfg, self.created, self.terminated, self.last_models = cfg, [], [], []

    async def create(self, idx, **kw):
        script = base64.b64decode(kw["docker_args"].split("echo ")[1].split(" |")[0]).decode()
        self.last_models = json.loads(re.search(r"MODELS = (\[.*?\])\n", script).group(1))
        self.created.append(kw)
        return f"seed{len(self.created)}", 0.25

    async def terminate(self, pid):
        self.terminated.append(pid)


class FakeOps:
    def __init__(self):
        self.created, self.images = [], []

    async def create(self, cfg, image, volume_id, auth):
        self.created.append((image, volume_id))
        return "ep1"

    async def set_image(self, cfg, endpoint_id, image, auth):
        self.images.append(image)

    async def registry_auth(self, user, token):
        return "auth1"


def make(tmp_path, monkeypatch, cloud=None, extra="", key=True):
    if key:
        monkeypatch.setenv("RUNPOD_API_KEY", "k")
    else:
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf")
    (tmp_path / "config.toml").write_text(
        f'[runpod]\nnetwork_volume_id = ""\n\n[serverless]\nendpoint_id = ""\n\n[factory]\ndata_dir = "data"\n'
        f'workflows_dir = "{ROOT / "workflows"}"\n' + extra)
    cfg = config_mod.load(tmp_path / "config.toml")
    cloud = cloud or FakeCloud()
    pods = FakePods(cfg.runpod)
    cloud.pods = pods
    ops = FakeOps()

    def infra(f=None, on_event=None):
        from run_comfy.infra import RunPodRest
        t = httpx.MockTransport(cloud.handler)
        return Infra(cfg, REG, tmp_path / "data", rest=RunPodRest("k", t), pods=pods, endpoint_ops=ops,
                     transport=t, seed_poll_s=0.001, on_event=on_event)
    return cfg, cloud, pods, ops, infra


def test_plan_is_offline_and_prices_first_run(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    (tmp_path / "config.toml").write_text(f'[factory]\nworkflows_dir = "{ROOT / "workflows"}"\n')
    cfg = config_mod.load(tmp_path / "config.toml")
    p = Infra(cfg, REG, tmp_path / "data").plan(["upscale_2x", "wan22_i2v"], "serverless")
    assert p["creates_volume"] and p["volume_gb"] >= 50 and p["creates_endpoint"]
    assert p["download_gb"] == round(0.07 + 10.0 + 6.7 + 1.4, 1)  # reported to 0.1 GB
    assert p["est_minutes"] > 0 and p["est_usd"] > 0 and p["image"] == cfg.serverless.base_image
    assert not p["custom_nodes"] and not p["ready"]
    assert "RUNPOD_API_KEY is not set" in p["warnings"]
    flux = Infra(cfg, REG, tmp_path / "data").plan(["flux_txt2img"], "serverless")
    assert any("HF_TOKEN" in w and "black-forest-labs/FLUX.1-dev" in w for w in flux["warnings"])
    pulid = Infra(cfg, REG, tmp_path / "data").plan(["avatar_pulid"], "serverless")
    assert pulid["custom_nodes"] and any("custom_image_repo" in w for w in pulid["warnings"])


async def test_ensure_creates_everything_once_and_persists_config(tmp_path, monkeypatch):
    cfg, cloud, pods, ops, infra = make(tmp_path, monkeypatch)
    rep = await infra().ensure(["upscale_2x", "wan22_i2v"], "serverless")
    assert cloud.created_volume_body["dataCenterId"] == "EU-RO-1" and cloud.created_volume_body["size"] >= 50
    assert any("created network volume vol1" in a for a in rep.actions)
    assert cloud.seeded == [[m["path"] for m in REG["upscale_2x"].models + REG["wan22_i2v"].models]]
    assert pods.created[0]["volume_mount_path"] == "/runpod-volume" and pods.terminated == ["seed1"]
    assert ops.created == [(cfg.serverless.base_image, "vol1")]
    saved = tomllib.loads((tmp_path / "config.toml").read_text())
    assert saved["runpod"]["network_volume_id"] == "vol1" and saved["serverless"]["endpoint_id"] == "ep1"
    assert saved["serverless"]["network_volume_id"] == "vol1"
    assert rep.plan["ready"]

    # second run: nothing to do, nothing re-downloaded
    rep2 = await infra().ensure(["upscale_2x", "wan22_i2v"], "serverless")
    assert rep2.actions == ["everything already in place"] and len(cloud.seeded) == 1
    # a new workflow only downloads what it adds (the umt5 text encoder is shared)
    await infra().ensure(["wan22_s2v"], "serverless")
    assert sorted(cloud.seeded[1]) == sorted(m["path"] for m in REG["wan22_s2v"].models
                                             if "umt5" not in m["path"])
    assert len(Ledger(tmp_path / "data", "vol1").files) == 7  # 1 + 3 + 3 unique files


async def test_gated_model_without_licence_fails_fast(tmp_path, monkeypatch):
    cfg, cloud, pods, ops, infra = make(tmp_path, monkeypatch, cloud=FakeCloud(gated=True))
    monkeypatch.delenv("HF_TOKEN")
    with pytest.raises(InfraError, match="HF_TOKEN.*black-forest-labs/FLUX.1-dev"):
        await infra().ensure(["flux_txt2img"], "serverless")
    assert pods.created == []  # no pod started for a download that can't succeed


async def test_custom_node_image_must_exist_then_endpoint_switches(tmp_path, monkeypatch):
    cfg, cloud, pods, ops, infra = make(tmp_path, monkeypatch, cloud=FakeCloud(image_built=False))
    cfg.serverless.custom_image_repo = "ghcr.io/me/run-comfy-worker"
    with pytest.raises(InfraError, match="isn't built yet"):
        await infra().ensure(["avatar_pulid"], "serverless")
    cloud.image_built = True
    await infra().ensure(["upscale_2x"], "serverless")            # core-only -> stock image
    assert ops.created[0][0] == cfg.serverless.base_image
    await infra().ensure(["avatar_pulid"], "serverless")          # needs custom nodes -> switch image
    assert ops.images and ops.images[0].startswith("ghcr.io/me/run-comfy-worker:n")


async def test_pod_backend_gets_default_image_and_start_command(tmp_path, monkeypatch):
    cfg, cloud, pods, ops, infra = make(tmp_path, monkeypatch)
    await infra().ensure(["upscale_2x"], "pod")
    assert cfg.runpod.image == cfg.serverless.base_image and "main.py --listen 0.0.0.0" in cfg.runpod.docker_args
    saved = tomllib.loads((tmp_path / "config.toml").read_text())
    assert "extra-model-paths-config" in saved["runpod"]["docker_args"] and ops.created == []


async def test_missing_volume_is_reported(tmp_path, monkeypatch):
    cfg, cloud, pods, ops, infra = make(tmp_path, monkeypatch)
    cfg.runpod.network_volume_id = "gone"
    with pytest.raises(InfraError, match="gone not found"):
        await infra().ensure(["upscale_2x"], "serverless")


async def test_factory_provisions_before_a_paid_batch(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg, cloud, pods, ops, infra = make(tmp_path, monkeypatch, extra='backend = "serverless"\nqa = false\n')
    from run_comfy import media
    f = Factory(cfg, backend_override=DemoBackend(), infra_factory=lambda fac, on_event: infra(on_event=on_event))
    prod = f.add_asset("product", "p.png", media.text_card(tmp_path / "p.png", 400, 400, "P"))
    kit = f.save_brand(Brand(aspects=["1:1"]))
    b = f.create_batch("Paid", "product", brand_kit_id=kit.id, product_asset_id=prod.id, settings={"upscale": False})
    f.set_plan(b.id, demo_plan("", Brand(), None, {}).model_dump())
    f.build_matrix(b.id, {"style": [0], "headline": [0], "cta": [0]}, mode="full")
    est = f.estimate(b.id)
    assert est["setup"]["creates_volume"] and est["setup"]["download_gb"] > 20  # Kontext + encoders
    events = f.bus.subscribe(b.id)
    done = await f.run(b.id)
    assert done.status == "done" and cloud.seeded and ops.created
    kinds = []
    while not events.empty():
        kinds.append(events.get_nowait())
    assert any(e.get("status") == "provisioning" for e in kinds)
    assert any(e.get("type") == "setup" for e in kinds)
    assert f.estimate(b.id)["setup"]["ready"]


async def test_factory_setup_failure_fails_batch_with_reason(tmp_path, monkeypatch):
    cfg, cloud, pods, ops, infra = make(tmp_path, monkeypatch, cloud=FakeCloud(gated=True),
                                        extra='backend = "serverless"\nqa = false\n')
    monkeypatch.delenv("HF_TOKEN")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = Factory(cfg, backend_override=DemoBackend(), infra_factory=lambda fac, on_event: infra(on_event=on_event))
    b = f.create_batch("Clip", "clip")
    f.set_plan(b.id, demo_plan("", Brand(), None, {}).model_dump())
    f.build_matrix(b.id, mode="random", n=1)
    with pytest.raises(FactoryError, match="setup failed: gated"):
        await f.run(b.id)
    assert f.store.get(type(b), b.id).status == "failed"
