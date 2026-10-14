"""bulkgen CLI:  run | validate | cleanup | gpus"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import os
import signal
import sys
from pathlib import Path

from . import config as config_mod
from .jobs import load_jobs, validate
from .post import concat

log = logging.getLogger("bulkgen")


def _load_dotenv(root: Path):
    env = root / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _setup_logging(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(out / "run.log")):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def cmd_validate(cfg) -> int:
    wf = json.loads(cfg.path(cfg.pipeline.workflow).read_text())
    jobs = load_jobs(cfg.path(cfg.pipeline.jobs))
    errs = validate(wf, jobs)
    for e in errs:
        print("ERROR:", e)
    if not errs:
        ups = sum(len(j.uploads) for j in jobs)
        print(f"OK: {len(jobs)} jobs, {ups} files to upload, workflow nodes: {len(wf)}")
    return 1 if errs else 0


def cmd_run(cfg, args) -> int:
    from .pods import PodManager
    from .runner import Runner

    _setup_logging(cfg.path(cfg.pipeline.output_dir))
    if args.fresh:
        (cfg.path(cfg.pipeline.output_dir) / "state.jsonl").unlink(missing_ok=True)
    pods = None
    if not args.url:
        pods = PodManager(cfg.runpod, cfg.api_key, cfg.path(cfg.pipeline.output_dir) / "pods.json")
        atexit.register(pods.terminate_all_sync)  # belt and braces
    runner = Runner(cfg, pods=pods, base_url=args.url)

    async def main():
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            try:
                loop.add_signal_handler(sig, task.cancel)
            except (NotImplementedError, RuntimeError):
                pass
        return await runner.run()

    try:
        summary = asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.error("interrupted - pods terminated; re-run the same command to resume")
        return 130
    files = summary.pop("ordered_files")
    if cfg.post.concat and summary["done"]:
        concat(files, cfg.path(cfg.pipeline.output_dir) / cfg.post.concat_output, cfg.post.loudnorm)
    log.info("SUMMARY %s  (details: %s)", summary, cfg.path(cfg.pipeline.output_dir) / "report.csv")
    return 0 if summary["failed"] == 0 and summary["not_run"] == 0 else 2


def cmd_cleanup(cfg) -> int:
    from .pods import PodManager

    pm = PodManager(cfg.runpod, cfg.api_key, cfg.path(cfg.pipeline.output_dir) / "pods.json")
    killed = pm.sweep()
    print(f"terminated {len(killed)} pod(s): {killed}" if killed else "no bulkgen pods running")
    return 0


def cmd_gpus(cfg) -> int:
    import runpod

    runpod.api_key = cfg.api_key
    for g in sorted(runpod.get_gpus(), key=lambda g: g.get("memoryInGb", 0)):
        print(f"{g['memoryInGb']:>4} GB  {g['id']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bulkgen", description="Bulk ComfyUI generation on RunPod")
    ap.add_argument("-c", "--config", default="config.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="spin up pods, process the CSV, download, tear down")
    r.add_argument("--url", help="use an existing ComfyUI server instead of creating pods")
    r.add_argument("--fresh", action="store_true", help="ignore previous progress and redo every job")
    sub.add_parser("validate", help="check workflow + CSV without spending money")
    sub.add_parser("cleanup", help="terminate any bulkgen pods left running")
    sub.add_parser("gpus", help="list RunPod GPU type ids")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config).resolve()
    _load_dotenv(cfg_path.parent)
    cfg = config_mod.load(cfg_path)
    return {"run": lambda: cmd_run(cfg, args), "validate": lambda: cmd_validate(cfg),
            "cleanup": lambda: cmd_cleanup(cfg), "gpus": lambda: cmd_gpus(cfg)}[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
