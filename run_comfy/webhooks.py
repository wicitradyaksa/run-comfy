"""Outgoing webhooks, signed per the Standard Webhooks spec (verifiable with svix / standardwebhooks libs).

Headers: webhook-id, webhook-timestamp, webhook-signature: "v1,<base64 HMAC-SHA256(id.timestamp.body)>"
Secret format: "whsec_<base64 key>" (a raw string is also accepted and used as-is).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx

log = logging.getLogger("run_comfy")


def _key(secret: str) -> bytes:
    return base64.b64decode(secret[6:]) if secret.startswith("whsec_") else secret.encode()


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(_key(secret), f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify(secret: str, headers: dict, body: bytes, tolerance_s: int = 300) -> bool:
    ts = int(headers["webhook-timestamp"])
    if abs(time.time() - ts) > tolerance_s:
        return False
    expected = sign(secret, headers["webhook-id"], ts, body).split(",", 1)[1]
    return any(hmac.compare_digest(expected, s.split(",", 1)[1])
               for s in headers["webhook-signature"].split() if s.startswith("v1,"))


async def send(url: str, secret: str, event_type: str, data: dict,
               transport: httpx.AsyncBaseTransport | None = None, attempts: int = 4) -> bool:
    if not url:
        return False
    body = json.dumps({"type": event_type, "timestamp": int(time.time()), "data": data}, default=str).encode()
    msg_id = "msg_" + uuid.uuid4().hex
    async with httpx.AsyncClient(timeout=15, transport=transport) as c:
        for i in range(attempts):
            ts = int(time.time())
            headers = {"content-type": "application/json", "webhook-id": msg_id, "webhook-timestamp": str(ts)}
            if secret:
                headers["webhook-signature"] = sign(secret, msg_id, ts, body)
            try:
                r = await c.post(url, content=body, headers=headers)
                if r.status_code < 300:
                    return True
                log.warning("webhook %s -> HTTP %s", event_type, r.status_code)
            except httpx.HTTPError as e:
                log.warning("webhook %s failed: %s", event_type, e)
            await asyncio.sleep(min(2 ** i, 30))
    return False
