"""SQLite persistence for the content factory (projects, brand kits, assets, batches, variants, stage runs, cache)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlmodel import JSON, Column, Field, Session, SQLModel, create_engine, select


def now() -> datetime:
    return datetime.now(timezone.utc)


def _json(default=dict):
    return Field(default_factory=default, sa_column=Column(JSON))


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    created: datetime = Field(default_factory=now)


class BrandKit(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(default=None, index=True)
    name: str
    data: dict = _json()  # see factory.brand.Brand
    created: datetime = Field(default_factory=now)


class Asset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(default=None, index=True)
    kind: str = Field(index=True)  # avatar | product | voice | music | logo | endcard | image | video | audio
    name: str
    path: str  # relative to data_dir
    meta: dict = _json()
    created: datetime = Field(default_factory=now)


class Batch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(default=None, index=True)
    brand_kit_id: int | None = None
    name: str
    recipe: str
    status: str = "draft"  # draft | queued | running | done | failed | cancelled
    brief: dict = _json()     # {text, url, product_asset_id, ...}
    plan: dict = _json()      # writer output (editable)
    matrix: dict = _json()    # {axes: {axis: [option ids]}, mode, n, aspects}
    settings: dict = _json()  # {backend, max_spend_usd, qa, ...}
    cost_usd: float = 0.0
    error: str = ""
    created: datetime = Field(default_factory=now)
    started: datetime | None = None
    finished: datetime | None = None


class Variant(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(index=True)
    code: str
    axes: dict = _json()      # axis -> option index
    status: str = "pending"   # pending | running | done | failed | cancelled
    review: str = "pending"   # pending | approved | rejected
    qa: dict = _json()
    outputs: dict = _json()   # aspect -> relative path of final file
    overrides: dict = _json() # stage -> {seed, prompt...} from regenerate / QA
    cost_usd: float = 0.0
    attempts: int = 0
    error: str = ""
    updated: datetime = Field(default_factory=now)


class StageRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    variant_id: int = Field(index=True)
    stage: str
    cache_key: str = Field(index=True)
    status: str = "running"  # running | done | failed
    cached: bool = False
    outputs: list = _json(list)
    seconds: float = 0.0
    cost_usd: float = 0.0
    error: str = ""
    created: datetime = Field(default_factory=now)


class CacheEntry(SQLModel, table=True):
    key: str = Field(primary_key=True)
    stage: str
    outputs: list = _json(list)  # relative to data_dir
    created: datetime = Field(default_factory=now)


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.data_dir / 'run_comfy.db'}",
                                    connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def _pragmas(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

        SQLModel.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # ---- generic helpers ----
    def add(self, obj):
        with self.session() as s:
            s.add(obj)
            s.commit()
            s.refresh(obj)
            return obj

    def get(self, model, id_):
        with self.session() as s:
            return s.get(model, id_)

    def update(self, model, id_, **fields) -> Any:
        with self.session() as s:
            obj = s.get(model, id_)
            if obj is None:
                raise KeyError(f"{model.__name__} {id_} not found")
            for k, v in fields.items():
                setattr(obj, k, v)
            if hasattr(obj, "updated"):
                obj.updated = now()
            s.add(obj)
            s.commit()
            s.refresh(obj)
            return obj

    def delete(self, model, id_):
        with self.session() as s:
            obj = s.get(model, id_)
            if obj is not None:
                s.delete(obj)
                s.commit()

    def list(self, model, order=None, **where) -> list:
        with self.session() as s:
            q = select(model)
            for k, v in where.items():
                q = q.where(getattr(model, k) == v)
            if order is not None:
                q = q.order_by(order)
            return list(s.exec(q).all())

    # ---- specific ----
    def variants(self, batch_id: int) -> list[Variant]:
        return self.list(Variant, order=Variant.id, batch_id=batch_id)

    def stage_runs(self, variant_id: int) -> list[StageRun]:
        return self.list(StageRun, order=StageRun.id, variant_id=variant_id)

    def cache_get(self, key: str) -> CacheEntry | None:
        e = self.get(CacheEntry, key)
        if e and all((self.data_dir / p).exists() for p in e.outputs):
            return e
        return None

    def cache_put(self, key: str, stage: str, outputs: list[str]):
        with self.session() as s:
            s.merge(CacheEntry(key=key, stage=stage, outputs=outputs))
            s.commit()

    def rel(self, p: Path | str) -> str:
        return str(Path(p).resolve().relative_to(self.data_dir.resolve()))

    def abs(self, rel: str) -> Path:
        p = (self.data_dir / rel).resolve()
        if not p.is_relative_to(self.data_dir.resolve()):
            raise ValueError(f"path escapes data dir: {rel}")
        return p
