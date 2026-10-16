"""Minimal Quant Data REST client (stdlib only).

Docs: https://quantdata.us/api/docs
Auth: Authorization: Bearer $QUANTDATA_API_KEY  (qd_… keys)
All endpoints are POST + JSON. Native WebSocket streaming is not available yet;
poll these endpoints for near-live option flow.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "https://api.quantdata.us"
ORDER_FLOW_CONSOLIDATED = "/v1/options/tool/order-flow/consolidated"
ORDER_FLOW_UNCONSOLIDATED = "/v1/options/tool/order-flow/unconsolidated"


class QuantDataError(RuntimeError):
    def __init__(self, status: int, payload: Any, headers: dict[str, str] | None = None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        detail = payload if isinstance(payload, str) else json.dumps(payload)[:500]
        super().__init__(f"Quant Data HTTP {status}: {detail}")


class QuantDataClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        key = (api_key or os.environ.get("QUANTDATA_API_KEY") or "").strip()
        if not key:
            raise ValueError(
                "QUANTDATA_API_KEY is required. Create a key at "
                "https://v3.quantdata.us and export it (or set the GitHub secret)."
            )
        if not key.startswith("qd_"):
            raise ValueError("QUANTDATA_API_KEY must look like qd_<32 alphanumerics>")
        self.api_key = key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        payload = json.dumps(body or {}).encode("utf-8")
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "SPX500-0DTE-Signals/quantdata-flow (+https://github.com/Khurrumtx/SPX500-0DTE-Signals)",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    problem = json.loads(raw) if raw else {"detail": str(exc)}
                except json.JSONDecodeError:
                    problem = {"detail": raw or str(exc)}
                headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
                if exc.code == 429 and attempt <= self.max_retries:
                    retry_after = float(headers.get("retry-after") or "1")
                    sleep_s = retry_after + random.uniform(0.05, 0.5)
                    time.sleep(sleep_s)
                    continue
                if exc.code >= 500 and attempt <= self.max_retries:
                    time.sleep(min(2 ** attempt, 8) + random.uniform(0.05, 0.5))
                    continue
                raise QuantDataError(exc.code, problem, headers) from None
            except urllib.error.URLError as exc:
                if attempt <= self.max_retries:
                    time.sleep(min(2 ** attempt, 8) + random.uniform(0.05, 0.5))
                    continue
                raise QuantDataError(0, {"detail": str(exc.reason)}) from None

    def order_flow_consolidated(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.post(ORDER_FLOW_CONSOLIDATED, body)

    def order_flow_unconsolidated(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.post(ORDER_FLOW_UNCONSOLIDATED, body)

    def iter_pages(
        self,
        fetcher,
        body: dict[str, Any],
        *,
        max_pages: int = 20,
    ):
        """Yield (page_index, response) walking nextSearchAfter cursors."""
        cursor_body = dict(body)
        for page in range(max_pages):
            resp = fetcher(cursor_body)
            yield page, resp
            nxt = resp.get("nextSearchAfter")
            if not nxt:
                break
            cursor_body = dict(body)
            cursor_body["searchAfter"] = nxt
