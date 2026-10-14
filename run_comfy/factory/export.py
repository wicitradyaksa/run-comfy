"""Export finished variants: a zip named for ad-platform bulk upload + manifest.csv for split-test attribution."""
from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from pathlib import Path

from ..store import Batch, Store, Variant


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "batch"


def export_batch(store: Store, batch: Batch, variants: list[Variant], labels: dict[int, dict],
                 approved_only: bool = True) -> tuple[Path, int]:
    chosen = [v for v in variants if v.status == "done" and v.outputs
              and (v.review == "approved" if approved_only else v.review != "rejected")]
    out_dir = store.data_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"{slug(batch.name)}_b{batch.id}_{time.strftime('%Y%m%d-%H%M%S')}.zip"
    rows = []
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for v in chosen:
            for aspect, rel in v.outputs.items():
                src = store.abs(rel)
                name = f"{slug(batch.name)}_{v.code}_{aspect.replace(':', 'x')}{src.suffix}"
                z.write(src, name)
                rows.append({"file": name, "variant": v.code, "aspect": aspect, "review": v.review,
                             "qa_score": (v.qa or {}).get("score", ""), "cost_usd": round(v.cost_usd, 4),
                             **{f"{k}": val for k, val in labels.get(v.id, {}).items()}})
        if rows:
            buf = io.StringIO()
            keys = list(dict.fromkeys(k for r in rows for k in r))
            w = csv.DictWriter(buf, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
            z.writestr("manifest.csv", buf.getvalue())
    return zpath, len(rows)
