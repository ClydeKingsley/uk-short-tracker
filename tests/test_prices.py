from __future__ import annotations

from datetime import date, datetime, timezone
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from short_tracker.prices import (  # noqa: E402
    MarketPriceProvider,
    PriceDataError,
    SymbolLookupError,
    UrlLibJsonClient,
    YahooFinanceProvider,
)


FIXED_NOW = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)


class FakeJsonClient:
    def __init__(self, handler):
        self.handler = handler
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        response = self.handler(url)
        if isinstance(response, Exception):
            raise response
        return response


def chart_payload(*, timestamps, quote, adjclose=None, meta=None, error=None):
    indicators = {"quote": [quote]}
    if adjclose is not None:
        indicators["adjclose"] = [{"adjclose": adjclose}]
    return {
        "chart": {
            "result": None
            if error
            else [
                {
                    "meta": {
                        "symbol": "BP.L",
                        "currency": "GBp",
                        "exchangeName": "LSE",
                        "exchangeTimezoneName": "Europe/London",
                        **(meta or {}),
                    },
                    "timestamp": timestamps,
                    "indicators": indicators,
                }
            ],
            "error": error,
        }
    }


class YahooSymbolTests(unittest.TestCase):
    def test_manual_override_bypasses_network(self):
        client = FakeJsonClient(lambda _url: self.fail("network should not be called"))
        provider = YahooFinanceProvider(client=client, clock=lambda: FIXED_NOW)

        resolution = provider.resolve_uk_symbol(
            "BP p.l.c.", isin="GB0007980591", manual_symbol=" bp.l "
        )

        self.assertEqual("BP.L", resolution.symbol)
        self.assertTrue(resolution.used_manual_override)
        self.assertFalse(resolution.review_recommended)
        self.assertEqual([], client.urls)

    def test_isin_and_company_search_filter_and_rank_uk_equities(self):
        def handler(url):
            query = parse_qs(urlparse(url).query)["q"][0]
            if query == "GB0007980591":
                return {
                    "quotes": [
                        {
                            "symbol": "BP.L",
                            "longname": "BP p.l.c.",
                            "exchange": "LSE",
                            "exchDisp": "London",
                            "quoteType": "EQUITY",
                        }
                    ]
                }
            return {
                "quotes": [
                    {
                        "symbol": "BP",
                        "longname": "BP p.l.c.",
                        "exchange": "NYQ",
                        "exchDisp": "NYSE",
                        "quoteType": "EQUITY",
                    },
                    {
                        "symbol": "BP.L",
                        "longname": "BP p.l.c.",
                        "exchange": "LSE",
                        "exchDisp": "London",
                        "quoteType": "EQUITY",
                    },
                    {
                        "symbol": "0HKP.L",
                        "longname": "BP p.l.c.",
                        "exchange": "LSE",
                        "exchDisp": "London",
                        "quoteType": "EQUITY",
                    },
                    {
                        "symbol": "BPETF.L",
                        "longname": "Unrelated ETF",
                        "exchange": "LSE",
                        "exchDisp": "London",
                        "quoteType": "ETF",
                    },
                ]
            }

        client = FakeJsonClient(handler)
        provider = YahooFinanceProvider(client=client, clock=lambda: FIXED_NOW)
        suggestions = provider.suggest_uk_symbols("BP p.l.c.", isin="GB0007980591")

        self.assertEqual(["BP.L", "0HKP.L"], [item.symbol for item in suggestions])
        self.assertEqual(("isin_query", "company_name_query"), suggestions[0].matched_by)
        self.assertGreater(suggestions[0].score, suggestions[1].score)
        self.assertEqual(2, len(client.urls))

    def test_no_candidates_has_actionable_manual_override_message(self):
        provider = YahooFinanceProvider(
            client=FakeJsonClient(lambda _url: {"quotes": []}), clock=lambda: FIXED_NOW
        )
        with self.assertRaisesRegex(SymbolLookupError, "manual override"):
            provider.resolve_uk_symbol("Unknown plc")

    def test_provider_satisfies_replaceable_protocol(self):
        provider = YahooFinanceProvider(
            client=FakeJsonClient(lambda _url: {"quotes": []}), clock=lambda: FIXED_NOW
        )
        self.assertIsInstance(provider, MarketPriceProvider)


class YahooHistoryTests(unittest.TestCase):
    def test_max_history_requests_true_daily_data_and_parses_adjusted_close(self):
        payload = chart_payload(
            timestamps=[1_659_312_000, 1_659_398_400, 1_659_484_800],
            quote={
                "open": [400.0, 410.0, None],
                "high": [420.0, 425.0, None],
                "low": [395.0, 405.0, None],
                "close": [415.0, 420.0, None],
                "volume": [100, 120, None],
            },
            adjclose=[390.0, 395.0, None],
            meta={
                "longName": "BP p.l.c.",
                "dataGranularity": "1d",
                "firstTradeDate": 583_743_600,
            },
        )
        client = FakeJsonClient(lambda _url: payload)
        provider = YahooFinanceProvider(client=client, clock=lambda: FIXED_NOW)

        history = provider.get_daily_history("bp.l")

        request = urlparse(client.urls[0])
        params = parse_qs(request.query)
        self.assertEqual(["1d"], params["interval"])
        self.assertEqual(["-2208988800"], params["period1"])
        self.assertNotIn("range", params)
        self.assertTrue(history.is_max_history)
        self.assertEqual(2, len(history.bars))
        self.assertEqual(395.0, history.bars[-1].adjusted_close)
        self.assertEqual("GBp", history.currency)
        self.assertEqual("1988-07-01T07:00:00Z", history.to_dict()["first_trade_time_utc"])
        self.assertEqual("2022-08-01", history.to_dict()["bars"][0]["date"])

    def test_requested_end_is_inclusive_in_period2(self):
        payload = chart_payload(
            timestamps=[1_704_067_200],
            quote={"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [5]},
            adjclose=[1.25],
            meta={"dataGranularity": "1d"},
        )
        client = FakeJsonClient(lambda _url: payload)
        provider = YahooFinanceProvider(client=client, clock=lambda: FIXED_NOW)
        provider.get_daily_history("BP.L", start=date(2024, 1, 1), end=date(2024, 1, 31))

        params = parse_qs(urlparse(client.urls[0]).query)
        self.assertEqual(
            int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp()), int(params["period2"][0])
        )

    def test_rejects_silent_non_daily_downsampling(self):
        payload = chart_payload(
            timestamps=[1_659_312_000],
            quote={"open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]},
            adjclose=[1],
            meta={"dataGranularity": "3mo"},
        )
        provider = YahooFinanceProvider(
            client=FakeJsonClient(lambda _url: payload), clock=lambda: FIXED_NOW
        )
        with self.assertRaisesRegex(PriceDataError, "instead of daily bars"):
            provider.get_daily_history("BP.L")

    def test_chart_error_is_preserved(self):
        payload = chart_payload(
            timestamps=[],
            quote={},
            error={"code": "Not Found", "description": "No data found, symbol may be delisted"},
        )
        provider = YahooFinanceProvider(
            client=FakeJsonClient(lambda _url: payload), clock=lambda: FIXED_NOW
        )
        with self.assertRaisesRegex(PriceDataError, "symbol may be delisted"):
            provider.get_daily_history("MISSING.L")


class YahooLatestTests(unittest.TestCase):
    def test_latest_uses_newer_regular_market_observation(self):
        payload = chart_payload(
            timestamps=[1_787_327_400, 1_787_327_800],
            quote={"close": [547.2, 546.8]},
            meta={
                "longName": "BP p.l.c.",
                "regularMarketTime": 1_787_327_944,
                "regularMarketPrice": 549.5,
                "previousClose": 552.2,
                "regularMarketDayHigh": 555.7,
                "regularMarketDayLow": 546.1,
                "regularMarketVolume": 53_631_242,
                "currentTradingPeriod": {
                    "regular": {"start": 1_787_554_800, "end": 1_787_585_400}
                },
            },
        )
        provider = YahooFinanceProvider(
            client=FakeJsonClient(lambda _url: payload), clock=lambda: FIXED_NOW
        )

        latest = provider.get_latest_price("BP.L")

        self.assertEqual(549.5, latest.price)
        self.assertEqual("regular_market_price", latest.price_kind)
        self.assertEqual("REGULAR", latest.market_state)
        self.assertEqual("GBp", latest.currency)
        self.assertEqual(53_631_242, latest.day_volume)
        self.assertIn("not an FCA disclosure", " ".join(latest.limitations))
        self.assertTrue(latest.to_dict()["as_of_utc"].endswith("Z"))


class UrlLibJsonClientTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, payload, status=200):
            self.payload = json.dumps(payload).encode("utf-8")
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return self.status

        def read(self, size=-1):
            return self.payload if size < 0 else self.payload[:size]

    def test_retry_timeout_and_user_agent_are_applied(self):
        calls = []
        sleeps = []

        def opener(request, *, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                headers = Message()
                headers["Retry-After"] = "0"
                raise HTTPError(
                    request.full_url,
                    503,
                    "Service Unavailable",
                    headers,
                    BytesIO(b"temporary outage"),
                )
            return self.FakeResponse({"ok": True})

        client = UrlLibJsonClient(
            timeout_seconds=3.5,
            retries=1,
            backoff_seconds=0.01,
            user_agent="ShortTracker-Test/1",
            opener=opener,
            sleeper=sleeps.append,
        )

        self.assertEqual({"ok": True}, client.get_json("https://example.test/data"))
        self.assertEqual(2, len(calls))
        self.assertEqual(3.5, calls[0][1])
        self.assertEqual("ShortTracker-Test/1", calls[0][0].get_header("User-agent"))
        self.assertEqual([0.01], sleeps)


if __name__ == "__main__":
    unittest.main()
