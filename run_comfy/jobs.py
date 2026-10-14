"""Job loading, workflow templating and resumable state."""
from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
BUILTINS = {"seed", "job_id"}


@dataclass
class Job:
    id: str
    values: dict[str, str]
    uploads: dict[str, Path] = field(default_factory=dict)  # column -> local file to upload
    attempts: int = 0


def load_jobs(csv_path: Path) -> list[Job]:
    jobs, seen = [], set()
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=1):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not any(row.values()):
                continue
            jid = re.sub(r"[^\w.-]", "_", row.pop("id", "") or f"{i:04d}")
            if jid in seen:
                raise ValueError(f"duplicate job id {jid!r} in {csv_path}")
            seen.add(jid)
            uploads = {}
            for k, v in row.items():
                p = (csv_path.parent / v) if v else None
                if p and len(v) < 300 and p.is_file():
                    uploads[k] = p
            jobs.append(Job(jid, row, uploads))
    return jobs


def placeholders(obj) -> set[str]:
    if isinstance(obj, dict):
        return set().union(*(placeholders(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(placeholders(v) for v in obj)) if obj else set()
    if isinstance(obj, str):
        return set(PLACEHOLDER.findall(obj))
    return set()


def _coerce(v):
    if isinstance(v, str):
        for t in (int, float):
            try:
                return t(v)
            except ValueError:
                pass
    return v


def render(workflow, values: dict):
    """Fill {{name}} placeholders. A string that is *only* a placeholder takes the typed value."""
    if isinstance(workflow, dict):
        return {k: render(v, values) for k, v in workflow.items()}
    if isinstance(workflow, list):
        return [render(v, values) for v in workflow]
    if isinstance(workflow, str):
        m = PLACEHOLDER.fullmatch(workflow.strip())
        if m:
            return _coerce(values[m.group(1)])
        return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), workflow)
    return workflow


def job_values(job: Job, uploaded: dict[str, str]) -> dict:
    v = dict(job.values)
    v.update(uploaded)
    v["job_id"] = job.id
    if not str(v.get("seed", "")).strip():
        v["seed"] = random.randint(0, 2**48)
    return v


def validate(workflow: dict, jobs: list[Job]) -> list[str]:
    errors = []
    if not isinstance(workflow, dict) or not all(
        isinstance(n, dict) and "class_type" in n for n in workflow.values()
    ):
        errors.append("workflow is not in ComfyUI *API* format (use 'Export (API)' in ComfyUI)")
    needed = placeholders(workflow) - BUILTINS
    if not needed:
        errors.append("workflow has no {{placeholders}}; every job would render the same thing")
    if not jobs:
        errors.append("jobs file has no rows")
    for j in jobs:
        missing = needed - {k for k, v in j.values.items() if v}
        if missing:
            errors.append(f"job {j.id}: missing values for {sorted(missing)}")
    return errors


class State:
    """Append-only journal so a crashed/cancelled run resumes where it stopped."""

    def __init__(self, path: Path):
        self.path = path
        self.done: dict[str, dict] = {}
        self.failed: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                (self.done if rec["status"] == "done" else self.failed)[rec["id"]] = rec
                if rec["status"] == "done":
                    self.failed.pop(rec["id"], None)

    def record(self, rec: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        (self.done if rec["status"] == "done" else self.failed)[rec["id"]] = rec
        if rec["status"] == "done":
            self.failed.pop(rec["id"], None)
