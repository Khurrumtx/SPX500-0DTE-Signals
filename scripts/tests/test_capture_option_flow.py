"""Unit tests for Quant Data option-flow capture (no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capture_option_flow as cap  # noqa: E402
from quantdata_client import QuantDataClient, QuantDataError  # noqa: E402


class FakeHTTPResponse:
    def __init__(self, payload: dict, headers: dict | None = None):
        self._payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ClientTests(unittest.TestCase):
    def test_requires_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                QuantDataClient(api_key="")

    def test_post_ok(self):
        client = QuantDataClient(api_key="qd_" + ("a" * 32))
        fake = FakeHTTPResponse({"data": [{"id": "1"}]})
        with mock.patch("urllib.request.urlopen", return_value=fake):
            out = client.order_flow_consolidated({"size": 1})
        self.assertEqual(out["data"][0]["id"], "1")

    def test_iter_pages(self):
        client = QuantDataClient(api_key="qd_" + ("b" * 32))
        pages = [
            {"data": [{"id": "a"}], "nextSearchAfter": ["1", "a"]},
            {"data": [{"id": "b"}]},
        ]

        def fetcher(body):
            idx = 0 if "searchAfter" not in body else 1
            return pages[idx]

        got = list(client.iter_pages(fetcher, {"size": 1}, max_pages=5))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[1][1]["data"][0]["id"], "b")


class CaptureLogicTests(unittest.TestCase):
    def test_dedupe_new(self):
        state = {"seen_ids": ["keep"], "last_trade_time_ms": 0}
        rows = [
            {"id": "keep", "tradeTimeMs": 2},
            {"id": "new1", "tradeTimeMs": 3},
            {"id": "new0", "tradeTimeMs": 1},
        ]
        fresh = cap.dedupe_new(rows, state)
        self.assertEqual([r["id"] for r in fresh], ["new0", "new1"])
        self.assertEqual(state["last_trade_time_ms"], 3)
        self.assertIn("new1", state["seen_ids"])

    def test_summarize_bias(self):
        rows = [
            {"contractType": "CALL", "premium": 200, "isUnusual": True},
            {"contractType": "PUT", "premium": 50, "tradeConsolidationType": "SWEEP"},
        ]
        s = cap.summarize(rows)
        self.assertEqual(s["flowBias"], "CALL")
        self.assertEqual(s["unusualCount"], 1)
        self.assertEqual(s["sweepCount"], 1)

    def test_run_once_writes_files(self):
        sample = {
            "data": [
                {
                    "id": "t1",
                    "ticker": "SPX",
                    "contractType": "CALL",
                    "premium": 150000,
                    "tradeTime": 1_700_000_000_000,
                    "isUnusual": True,
                    "tradeConsolidationType": "SWEEP",
                    "dte": 0,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "orderflow"
            dashboard = tmp_path / "option_flow.json"
            state = tmp_path / "state.json"
            argv = [
                "once",
                "--api-key",
                "qd_" + ("c" * 32),
                "--tickers",
                "SPX",
                "--dte-max",
                "-1",
                "--out-dir",
                str(out_dir),
                "--dashboard",
                str(dashboard),
                "--state",
                str(state),
                "--max-pages",
                "1",
            ]
            with mock.patch.object(
                QuantDataClient,
                "order_flow_consolidated",
                return_value=sample,
            ):
                code = cap.main(argv)
            self.assertEqual(code, 0)
            self.assertTrue(dashboard.exists())
            snap = json.loads(dashboard.read_text())
            self.assertEqual(snap["flowBias"], "CALL")
            jsonl_files = list(out_dir.glob("flow_*.jsonl"))
            self.assertEqual(len(jsonl_files), 1)
            line = jsonl_files[0].read_text().strip()
            self.assertIn('"id":"t1"', line)

    def test_error_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            argv = [
                "once",
                "--api-key",
                "qd_" + ("d" * 32),
                "--tickers",
                "SPX",
                "--out-dir",
                str(tmp_path),
                "--dashboard",
                str(tmp_path / "d.json"),
                "--state",
                str(tmp_path / "s.json"),
            ]
            with mock.patch.object(
                QuantDataClient,
                "order_flow_consolidated",
                side_effect=QuantDataError(401, {"title": "unauthorized"}),
            ):
                code = cap.main(argv)
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
