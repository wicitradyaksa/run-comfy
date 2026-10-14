"""Minimal async ComfyUI HTTP API client."""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import httpx


class ComfyError(Exception):
    """The workflow itself failed (bad node input, OOM, ...). Retryable."""


class ComfyUnreachable(Exception):
    """The server could not be reached. Points at a dead pod."""


class ComfyClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self.http = httpx.AsyncClient(
            base_url=self.base_url, timeout=60, transport=transport, follow_redirects=True
        )

    async def close(self):
        await self.http.aclose()

    async def _req(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            r = await self.http.request(method, url, **kw)
        except httpx.TransportError as e:
            raise ComfyUnreachable(f"{method} {url}: {e!r}") from e
        if r.status_code in (502, 503, 504):  # RunPod proxy: pod down / not ready
            raise ComfyUnreachable(f"{method} {url}: HTTP {r.status_code}")
        return r

    async def healthy(self) -> bool:
        try:
            r = await self._req("GET", "/system_stats")
            return r.status_code == 200
        except ComfyUnreachable:
            return False

    async def wait_healthy(self, timeout: float, interval: float = 10, on_tick=None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.healthy():
                return
            if on_tick:
                await on_tick()
            await asyncio.sleep(interval)
        raise TimeoutError(f"ComfyUI at {self.base_url} not healthy after {timeout:.0f}s")

    async def upload(self, path: Path) -> str:
        r = await self._req(
            "POST", "/upload/image",
            files={"image": (path.name, path.read_bytes())},
            data={"overwrite": "true"},
        )
        r.raise_for_status()
        j = r.json()
        return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    async def submit(self, workflow: dict) -> str:
        r = await self._req("POST", "/prompt", json={"prompt": workflow, "client_id": self.client_id})
        if r.status_code != 200:
            # 400 = workflow validation error (missing model, bad input) -> not a pod problem
            raise ComfyError(f"submit rejected: HTTP {r.status_code} {r.text[:800]}")
        return r.json()["prompt_id"]

    async def wait(self, prompt_id: str, timeout: float, interval: float = 3) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = await self._req("GET", f"/history/{prompt_id}")
            if r.status_code == 200:
                entry = r.json().get(prompt_id)
                if entry:
                    status = entry.get("status", {})
                    if status.get("status_str") == "error":
                        raise ComfyError(_error_text(status))
                    if status.get("completed", True) and entry.get("outputs") is not None:
                        return entry["outputs"]
            await asyncio.sleep(interval)
        await self.interrupt()
        raise ComfyError(f"prompt {prompt_id} timed out after {timeout:.0f}s")

    async def interrupt(self):
        try:
            await self._req("POST", "/interrupt")
        except ComfyUnreachable:
            pass

    async def free_memory(self):
        """Release VRAM after a failure (e.g. OOM) before retrying."""
        try:
            await self._req("POST", "/free", json={"unload_models": True, "free_memory": True})
        except ComfyUnreachable:
            pass

    async def download(self, info: dict, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        params = {"filename": info["filename"], "subfolder": info.get("subfolder", ""),
                  "type": info.get("type", "output")}
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            async with self.http.stream("GET", "/view", params=params, timeout=600) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
        except httpx.TransportError as e:
            raise ComfyUnreachable(f"download {info['filename']}: {e!r}") from e
        tmp.replace(dest)
        return dest


def output_files(outputs: dict) -> list[dict]:
    """Every saved file in a history 'outputs' block (images, gifs, videos, audio...)."""
    files = []
    for node_out in outputs.values():
        for items in node_out.values():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and "filename" in it and it.get("type", "output") == "output":
                    files.append(it)
    return files


def _error_text(status: dict) -> str:
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return f"{data.get('node_type')}: {data.get('exception_message', '').strip()}"
    return "execution failed"
