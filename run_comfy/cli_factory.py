"""CLI commands for the content factory, workflows and provisioning."""
from __future__ import annotations

import asyncio
import json
import logging
import tomllib
from pathlib import Path

log = logging.getLogger("run_comfy")


def register(sub):
    s = sub.add_parser("serve", help="start the dashboard + API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    b = sub.add_parser("batch", help="create / run / inspect / export ad batches")
    bs = b.add_subparsers(dest="batch_cmd", required=True)
    n = bs.add_parser("new", help="brief -> plan -> matrix (-> run)")
    n.add_argument("--recipe", default="ugc", choices=["ugc", "product", "clip"])
    n.add_argument("--name", required=True)
    n.add_argument("--brief", default="")
    n.add_argument("--url")
    n.add_argument("--brand", help="brand kit file (TOML/JSON with Brand fields) or existing kit id")
    n.add_argument("--product", type=Path)
    n.add_argument("--avatar", type=Path, action="append", default=[], help="presenter photo (repeatable)")
    n.add_argument("--face", type=Path, help="face reference for a consistent AI presenter")
    n.add_argument("--backend", choices=["demo", "pod", "serverless", "url"])
    n.add_argument("--aspects", help="comma separated, e.g. 9:16,1:1")
    n.add_argument("--mode", default="pairwise", choices=["pairwise", "full", "random"])
    n.add_argument("--n", type=int, help="max variants")
    n.add_argument("--budget", type=float)
    n.add_argument("--voices", help="comma separated voice ids (ElevenLabs)")
    n.add_argument("--run", action="store_true", help="render immediately after planning")
    r = bs.add_parser("run", help="render pending variants")
    r.add_argument("id", type=int)
    st = bs.add_parser("status", help="show a batch")
    st.add_argument("id", type=int)
    bs.add_parser("list", help="list batches")
    e = bs.add_parser("export", help="zip finished variants + manifest.csv")
    e.add_argument("id", type=int)
    e.add_argument("--all", action="store_true", help="include unreviewed variants (not just approved)")

    w = sub.add_parser("workflows", help="list or verify the workflow registry")
    ws = w.add_subparsers(dest="wf_cmd", required=True)
    ws.add_parser("list")
    c = ws.add_parser("check", help="compare graphs with a live ComfyUI's installed nodes")
    c.add_argument("--url", required=True)
    c.add_argument("--name", action="append", help="only these workflows")

    bi = sub.add_parser("build-image", help="generate a Dockerfile with pinned custom nodes (+ models)")
    bi.add_argument("--base", default="runpod/worker-comfyui:5.5.0-base",
                    help="base image (check the worker-comfyui releases page for the current tag)")
    bi.add_argument("--bake-models", action="store_true")
    bi.add_argument("--name", action="append", help="only these workflows")
    bi.add_argument("--out", type=Path, default=Path("Dockerfile"))

    sv = sub.add_parser("seed-volume", help="download every manifest model onto the RunPod network volume")
    sv.add_argument("--name", action="append", help="only these workflows")
    sv.add_argument("--image", default="python:3.12-slim")
    return {"serve": cmd_serve, "batch": cmd_batch, "workflows": cmd_workflows,
            "build-image": cmd_build_image, "seed-volume": cmd_seed_volume}


def _factory(cfg):
    from .factory.service import Factory

    return Factory(cfg)


def _logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")


def cmd_serve(cfg, args) -> int:
    import uvicorn

    from .api import create_app

    _logging()
    print(f"run-comfy studio on http://{args.host}:{args.port}  (backend: {cfg.factory.backend})")
    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="warning")
    return 0


def _print_view(f, batch_id: int):
    v = f.batch_view(batch_id)
    print(f"#{v['id']} {v['name']}  [{v['recipe']}]  {v['status']}  ${v['cost_usd']:.2f}")
    for x in v["variants"]:
        qa = x["qa"].get("score") if x["qa"] else None
        print(f"  {x['code']:<22} {x['status']:<9} {x['review']:<9} qa={qa if qa is not None else '-':<4} "
              + "  ".join(f"{a}:{p}" for a, p in x["outputs"].items()) + (f"  ! {x['error'][:80]}" if x["error"] else ""))


def cmd_batch(cfg, args) -> int:
    from .factory.brand import Brand
    from .factory.service import FactoryError
    from .store import Batch

    _logging()
    f = _factory(cfg)
    try:
        if args.batch_cmd == "list":
            for b in reversed(f.store.list(Batch, order=Batch.id)):
                vs = f.store.variants(b.id)
                print(f"#{b.id:<4} {b.status:<9} {b.recipe:<8} {sum(v.status == 'done' for v in vs)}/{len(vs)} "
                      f"${b.cost_usd:.2f}  {b.name}")
            return 0
        if args.batch_cmd == "status":
            _print_view(f, args.id)
            return 0
        if args.batch_cmd == "export":
            out = f.export(args.id, approved_only=not args.all)
            print(f"{out['files']} files -> {out['path']}" + (f"\n{out['url']}" if out.get("url") else ""))
            return 0 if out["files"] else 2
        if args.batch_cmd == "run":
            b = asyncio.run(f.run(args.id))
            _print_view(f, b.id)
            return 0 if b.status == "done" else 2
        # new
        kit_id = None
        if args.brand:
            if args.brand.isdigit():
                kit_id = int(args.brand)
            else:
                p = Path(args.brand)
                data = tomllib.loads(p.read_text()) if p.suffix == ".toml" else json.loads(p.read_text())
                kit_id = f.save_brand(Brand(**data)).id
        up = lambda kind, p: f.add_asset(kind, p.name, p).id if p else None  # noqa: E731
        settings = {}
        if args.backend:
            settings["backend"] = args.backend
        if args.aspects:
            settings["aspects"] = [a.strip() for a in args.aspects.split(",")]
        if args.budget:
            settings["max_spend_usd"] = args.budget
        if args.voices:
            settings["voices"] = [v.strip() for v in args.voices.split(",")]
        b = f.create_batch(args.name, args.recipe, brief=args.brief, url=args.url, brand_kit_id=kit_id,
                           product_asset_id=up("product", args.product),
                           avatar_asset_ids=[up("avatar", a) for a in args.avatar],
                           face_asset_id=up("face", args.face), settings=settings)
        plan = asyncio.run(f.plan(b.id))
        print(f"batch #{b.id}: {len(plan.hooks)} hooks, {len(plan.bodies)} scripts, {len(plan.ctas)} CTAs")
        for i, h in enumerate(plan.hooks, 1):
            print(f"  H{i}: {h}")
        vs = f.build_matrix(b.id, mode=args.mode, n=args.n)
        est = f.estimate(b.id)
        print(f"{len(vs)} variants ({args.mode}); GPU renders {est['renders']}; "
              f"~{est['gpu_seconds'] // 60} GPU-min, ~${est['usd']:.2f} on {est['backend']} "
              f"(without stage reuse ${est['usd_without_cache']:.2f})")
        for w in est["warnings"]:
            print("  warning:", w)
        if args.run:
            b = asyncio.run(f.run(b.id))
            _print_view(f, b.id)
            return 0 if b.status == "done" else 2
        print(f"review/edit in the dashboard, or render with: python -m run_comfy batch run {b.id}")
        return 0
    except FactoryError as e:
        print("error:", e)
        return 1


def cmd_workflows(cfg, args) -> int:
    from .comfy import ComfyClient
    from .registry import check_against_server, load_registry

    reg = load_registry(cfg.path(cfg.factory.workflows_dir))
    if args.wf_cmd == "list":
        for wf in reg.values():
            flag = "verified" if wf.verified else "UNVERIFIED"
            print(f"{wf.name:<20} {wf.kind:<10} v{wf.version} {flag:<10} >={wf.min_vram_gb}GB "
                  f"inputs: {', '.join(f'{i.name}:{i.type}' for i in wf.inputs.values())}")
        return 0

    async def info():
        c = ComfyClient(args.url)
        try:
            return await c.object_info()
        finally:
            await c.close()

    oi = asyncio.run(info())
    bad = 0
    for name, wf in reg.items():
        if args.name and name not in args.name:
            continue
        probs = check_against_server(wf, oi)
        print(f"{'OK ' if not probs else 'FAIL'} {name}")
        for p in probs:
            print(f"     - {p}")
        bad += bool(probs)
    return 1 if bad else 0


def cmd_build_image(cfg, args) -> int:
    from .provision import dockerfile
    from .registry import load_registry

    reg = load_registry(cfg.path(cfg.factory.workflows_dir))
    args.out.write_text(dockerfile(reg, args.base, bake_models=args.bake_models, names=args.name))
    print(f"wrote {args.out}. Build and push it, then use it as [serverless].image or [runpod].image:\n"
          f"  docker build -t you/run-comfy-worker:1 -f {args.out} . && docker push you/run-comfy-worker:1")
    return 0


def cmd_seed_volume(cfg, args) -> int:
    from .pods import PodManager
    from .provision import collect, seed_volume
    from .registry import load_registry

    _logging()
    if not cfg.runpod.network_volume_id:
        print("set [runpod].network_volume_id (and data_center_id) first")
        return 1
    reg = load_registry(cfg.path(cfg.factory.workflows_dir))
    models, _ = collect(reg, args.name)
    print(f"seeding {len(models)} model files onto volume {cfg.runpod.network_volume_id}")
    pm = PodManager(cfg.runpod, cfg.api_key, cfg.path(cfg.factory.data_dir) / "pods.json", require_image=False)
    import os
    env = {k: os.environ[k] for k in ("HF_TOKEN", "CIVITAI_TOKEN") if os.environ.get(k)}
    st = asyncio.run(seed_volume(pm, models, image=args.image, env=env))
    print(f"done: {len(st['done'])} files, failed: {len(st['failed'])}")
    for f in st["failed"]:
        print(f"  - {f['path']}: {f['error']}")
    return 1 if st["failed"] else 0
