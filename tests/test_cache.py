from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from short_tracker.cache import PriceCache


class PriceCacheTests(unittest.TestCase):
    def test_payload_round_trip_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = PriceCache(directory)
            stored = cache.put_payload("OCDO.L", "history", {"bars": [{"date": "2026-08-21", "close": 239}]})
            self.assertFalse(stored["cache"]["hit"])

            hit = cache.get_payload("ocdo.l", "history", max_age_seconds=60)
            self.assertIsNotNone(hit)
            self.assertTrue(hit["cache"]["hit"])
            self.assertEqual(hit["bars"][0]["close"], 239)

            file_path = next((Path(directory) / "cache" / "prices").glob("*.json"))
            envelope = json.loads(file_path.read_text(encoding="utf-8"))
            envelope["cached_at_utc"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            file_path.write_text(json.dumps(envelope), encoding="utf-8")
            self.assertIsNone(cache.get_payload("OCDO.L", "history", max_age_seconds=60))

    def test_symbol_mapping_is_persistent_and_reviewable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = PriceCache(directory)
            record = cache.set_symbol_mapping(
                42,
                symbol="OCDO.L",
                source="auto",
                display_name="Ocado Group plc",
                review_recommended=True,
            )
            self.assertEqual(record["symbol"], "OCDO.L")
            loaded = PriceCache(directory).get_symbol_mapping("42")
            self.assertEqual(loaded["display_name"], "Ocado Group plc")
            self.assertTrue(loaded["review_recommended"])


if __name__ == "__main__":
    unittest.main()
