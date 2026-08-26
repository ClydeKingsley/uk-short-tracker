"""Replaceable, best-effort market-price adapters for Short Tracker.

The FCA short-position disclosures do not contain market prices.  This module
therefore keeps price data behind a small provider protocol and labels Yahoo
Finance as a third-party, non-regulatory source.  It deliberately has no
dependency on the application's SQLite implementation: callers can cache the
frozen dataclass results (or their ``to_dict`` payloads) in whatever database
layer they inject elsewhere.

Yahoo's search and chart endpoints used here are public web endpoints, not a
documented or guaranteed API.  They require no API key at the time of writing,
but can be delayed, incomplete, rate limited, corrected retrospectively, or
changed without notice.  They must not be treated as an execution feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from difflib import SequenceMatcher
import json
import math
import re
import time
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


PROVIDER_ID = "yahoo_finance"
PROVIDER_NAME = "Yahoo Finance"
PROVIDER_HOME = "https://finance.yahoo.com/"
PROVIDER_ATTRIBUTION = "Market-price data: Yahoo Finance (best-effort public web endpoints)."
PROVIDER_LIMITATIONS = (
    "Third-party market data, not an FCA disclosure and not an execution-grade feed.",
    "The endpoints are undocumented and have no availability, completeness, or stability guarantee.",
    "Prices may be delayed, missing, adjusted, or corrected retrospectively.",
    "London prices are commonly labelled GBp (pence); they are not automatically converted to GBP.",
    "Company-name and ISIN ticker matches are suggestions and should be reviewed before use.",
)

DEFAULT_USER_AGENT = (
    "ShortTracker/0.1 (local, read-only market research; "
    "+https://www.fca.org.uk/markets/short-selling)"
)
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 0.35

_SEARCH_ENDPOINT = "https://query2.finance.yahoo.com/v1/finance/search"
_CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart"
_EARLIEST_HISTORY_EPOCH = -2_208_988_800  # 1900-01-01T00:00:00Z
_RETRIABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_UK_EXCHANGES = frozenset({"LSE", "LON", "IOB"})
_SYMBOL_RE = re.compile(r"^[A-Z0-9.^=_-]{1,40}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class PriceAdapterError(RuntimeError):
    """Base class for actionable price adapter failures."""


class PriceNetworkError(PriceAdapterError):
    """The provider could not be reached or returned an HTTP failure."""


class PriceDataError(PriceAdapterError):
    """The provider response was missing, malformed, or semantically invalid."""


class SymbolLookupError(PriceAdapterError):
    """No usable symbol could be found or all lookup requests failed."""


@dataclass(frozen=True, slots=True)
class SymbolSuggestion:
    """A reviewable UK ticker candidate returned by a provider search."""

    symbol: str
    display_name: str
    exchange: str
    exchange_display: str
    quote_type: str
    score: float
    matched_by: tuple[str, ...]
    source_url: str
    provider: str = PROVIDER_ID
    is_manual_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "exchange": self.exchange,
            "exchange_display": self.exchange_display,
            "quote_type": self.quote_type,
            "score": self.score,
            "matched_by": list(self.matched_by),
            "source_url": self.source_url,
            "provider": self.provider,
            "is_manual_override": self.is_manual_override,
        }


@dataclass(frozen=True, slots=True)
class SymbolResolution:
    """A selected ticker plus alternatives that a UI can offer for review."""

    selected: SymbolSuggestion
    alternatives: tuple[SymbolSuggestion, ...]
    review_recommended: bool

    @property
    def symbol(self) -> str:
        return self.selected.symbol

    @property
    def used_manual_override(self) -> bool:
        return self.selected.is_manual_override

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected.to_dict(),
            "alternatives": [candidate.to_dict() for candidate in self.alternatives],
            "review_recommended": self.review_recommended,
        }


@dataclass(frozen=True, slots=True)
class DailyPriceBar:
    """One exchange trading date of OHLC, adjusted close, and volume."""

    trading_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    adjusted_close: float
    volume: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.trading_date.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "adjusted_close": self.adjusted_close,
            "volume": self.volume,
        }


@dataclass(frozen=True, slots=True)
class DailyPriceHistory:
    """Cache-friendly daily history returned independently of any database."""

    symbol: str
    display_name: str
    currency: str
    exchange: str
    exchange_timezone: str
    bars: tuple[DailyPriceBar, ...]
    requested_start: date | None
    requested_end: date | None
    is_max_history: bool
    first_trade_time_utc: datetime | None
    fetched_at_utc: datetime
    source_url: str
    provider: str = PROVIDER_ID
    attribution: str = PROVIDER_ATTRIBUTION
    limitations: tuple[str, ...] = PROVIDER_LIMITATIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "currency": self.currency,
            "exchange": self.exchange,
            "exchange_timezone": self.exchange_timezone,
            "bars": [bar.to_dict() for bar in self.bars],
            "requested_start": self.requested_start.isoformat() if self.requested_start else None,
            "requested_end": self.requested_end.isoformat() if self.requested_end else None,
            "is_max_history": self.is_max_history,
            "first_trade_time_utc": _datetime_to_iso(self.first_trade_time_utc),
            "fetched_at_utc": _datetime_to_iso(self.fetched_at_utc),
            "source_url": self.source_url,
            "provider": self.provider,
            "attribution": self.attribution,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class LatestPrice:
    """The freshest regular-session observation exposed by the chart endpoint."""

    symbol: str
    display_name: str
    price: float
    currency: str
    as_of_utc: datetime
    price_kind: str
    previous_close: float | None
    day_high: float | None
    day_low: float | None
    day_volume: int | None
    market_state: str
    exchange: str
    exchange_timezone: str
    delayed_by_minutes: int | None
    fetched_at_utc: datetime
    source_url: str
    provider: str = PROVIDER_ID
    attribution: str = PROVIDER_ATTRIBUTION
    limitations: tuple[str, ...] = PROVIDER_LIMITATIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "price": self.price,
            "currency": self.currency,
            "as_of_utc": _datetime_to_iso(self.as_of_utc),
            "price_kind": self.price_kind,
            "previous_close": self.previous_close,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "day_volume": self.day_volume,
            "market_state": self.market_state,
            "exchange": self.exchange,
            "exchange_timezone": self.exchange_timezone,
            "delayed_by_minutes": self.delayed_by_minutes,
            "fetched_at_utc": _datetime_to_iso(self.fetched_at_utc),
            "source_url": self.source_url,
            "provider": self.provider,
            "attribution": self.attribution,
            "limitations": list(self.limitations),
        }


@runtime_checkable
class MarketPriceProvider(Protocol):
    """Interface implemented by replaceable market-price providers."""

    def suggest_uk_symbols(
        self,
        company_name: str,
        *,
        isin: str | None = None,
        manual_symbol: str | None = None,
        limit: int = 5,
    ) -> tuple[SymbolSuggestion, ...]: ...

    def resolve_uk_symbol(
        self,
        company_name: str,
        *,
        isin: str | None = None,
        manual_symbol: str | None = None,
        limit: int = 5,
    ) -> SymbolResolution: ...

    def get_daily_history(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> DailyPriceHistory: ...

    def get_latest_price(self, symbol: str) -> LatestPrice: ...


class JsonClient(Protocol):
    """Minimal injectable HTTP boundary used by the Yahoo provider."""

    def get_json(self, url: str) -> Mapping[str, Any]: ...


class UrlLibJsonClient:
    """Small standard-library JSON client with bounded retries and timeouts."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
        max_response_bytes: int = 25_000_000,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if retries < 0:
            raise ValueError("retries cannot be negative")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")
        if not user_agent.strip():
            raise ValueError("user_agent cannot be empty")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.retries = int(retries)
        self.backoff_seconds = float(backoff_seconds)
        self.user_agent = user_agent.strip()
        self.max_response_bytes = int(max_response_bytes)
        self._opener = opener
        self._sleeper = sleeper

    def get_json(self, url: str) -> Mapping[str, Any]:
        attempts = self.retries + 1
        last_error = "unknown network failure"
        for attempt in range(attempts):
            request = Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                method="GET",
            )
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    status = int(getattr(response, "status", response.getcode()))
                    if status < 200 or status >= 300:
                        raise PriceNetworkError(f"HTTP {status} from {url}")
                    raw = response.read(self.max_response_bytes + 1)
                    if len(raw) > self.max_response_bytes:
                        raise PriceDataError(
                            f"Response from {url} exceeded {self.max_response_bytes} bytes"
                        )
                    try:
                        payload = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise PriceDataError(f"Provider returned invalid JSON for {url}: {exc}") from exc
                    if not isinstance(payload, Mapping):
                        raise PriceDataError(f"Provider returned a non-object JSON response for {url}")
                    return payload
            except HTTPError as exc:
                detail = _http_error_detail(exc)
                last_error = f"HTTP {exc.code}{': ' + detail if detail else ''}"
                if exc.code not in _RETRIABLE_HTTP_STATUS or attempt + 1 >= attempts:
                    raise PriceNetworkError(
                        f"Request to Yahoo Finance failed after {attempt + 1}/{attempts} "
                        f"attempts ({last_error}). URL: {url}"
                    ) from exc
                self._sleep_before_retry(attempt, exc.headers.get("Retry-After") if exc.headers else None)
            except PriceDataError:
                raise
            except PriceNetworkError:
                raise
            except (URLError, TimeoutError, ConnectionError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                last_error = f"{type(exc).__name__}: {reason}"
                if attempt + 1 >= attempts:
                    raise PriceNetworkError(
                        f"Request to Yahoo Finance failed after {attempts} attempts "
                        f"({last_error}). URL: {url}"
                    ) from exc
                self._sleep_before_retry(attempt, None)
        raise PriceNetworkError(f"Request to Yahoo Finance failed ({last_error}). URL: {url}")

    def _sleep_before_retry(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff_seconds * (2**attempt)
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 30.0))
            except ValueError:
                pass
        if delay:
            self._sleeper(delay)


class YahooFinanceProvider:
    """No-key Yahoo Finance adapter, suitable for read-only research displays."""

    def __init__(
        self,
        *,
        client: JsonClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client or UrlLibJsonClient(
            timeout_seconds=timeout_seconds,
            retries=retries,
            backoff_seconds=backoff_seconds,
            user_agent=user_agent,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def suggest_uk_symbols(
        self,
        company_name: str,
        *,
        isin: str | None = None,
        manual_symbol: str | None = None,
        limit: int = 5,
    ) -> tuple[SymbolSuggestion, ...]:
        company_name = company_name.strip()
        if limit < 1 or limit > 25:
            raise ValueError("limit must be between 1 and 25")
        if manual_symbol and manual_symbol.strip():
            symbol = normalize_symbol(manual_symbol)
            return (
                SymbolSuggestion(
                    symbol=symbol,
                    display_name=company_name or symbol,
                    exchange="MANUAL",
                    exchange_display="Manual override",
                    quote_type="EQUITY",
                    score=1.0,
                    matched_by=("manual_override",),
                    source_url="",
                    is_manual_override=True,
                ),
            )
        normalized_isin = normalize_isin(isin) if isin and isin.strip() else None
        if not company_name and not normalized_isin:
            raise ValueError("company_name or isin is required for symbol lookup")

        queries: list[tuple[str, str]] = []
        if normalized_isin:
            queries.append((normalized_isin, "isin_query"))
        if company_name:
            queries.append((company_name, "company_name_query"))

        merged: dict[str, dict[str, Any]] = {}
        successful_queries = 0
        failures: list[str] = []
        source_order = 0
        for query_text, match_reason in queries:
            url = _build_search_url(query_text, max(limit * 3, 10))
            try:
                payload = self._client.get_json(url)
            except PriceAdapterError as exc:
                failures.append(f"{match_reason}: {exc}")
                continue
            successful_queries += 1
            quotes = payload.get("quotes", ())
            if not isinstance(quotes, Sequence) or isinstance(quotes, (str, bytes, bytearray)):
                raise PriceDataError(f"Yahoo symbol search returned an invalid quotes list. URL: {url}")
            for raw in quotes:
                if not isinstance(raw, Mapping) or not _is_uk_equity_quote(raw):
                    continue
                raw_symbol = str(raw.get("symbol") or "").strip().upper()
                if not raw_symbol or not _SYMBOL_RE.fullmatch(raw_symbol):
                    continue
                display_name = str(
                    raw.get("longname") or raw.get("longName") or raw.get("shortname") or raw_symbol
                ).strip()
                exchange = str(raw.get("exchange") or "").strip().upper()
                exchange_display = str(raw.get("exchDisp") or raw.get("exchangeDisplay") or exchange).strip()
                quote_type = str(raw.get("quoteType") or "EQUITY").strip().upper()
                score = _suggestion_score(
                    company_name=company_name,
                    candidate_name=display_name,
                    symbol=raw_symbol,
                    exchange=exchange,
                    match_reason=match_reason,
                )
                existing = merged.get(raw_symbol)
                if existing is None:
                    merged[raw_symbol] = {
                        "symbol": raw_symbol,
                        "display_name": display_name,
                        "exchange": exchange,
                        "exchange_display": exchange_display,
                        "quote_type": quote_type,
                        "score": score,
                        "matched_by": [match_reason],
                        "source_url": url,
                        "source_order": source_order,
                    }
                    source_order += 1
                else:
                    existing["score"] = max(float(existing["score"]), score)
                    if match_reason not in existing["matched_by"]:
                        existing["matched_by"].append(match_reason)

        if successful_queries == 0:
            joined = " | ".join(failures) if failures else "no response"
            raise SymbolLookupError(f"All Yahoo UK symbol searches failed. {joined}")

        suggestions = [
            SymbolSuggestion(
                symbol=item["symbol"],
                display_name=item["display_name"],
                exchange=item["exchange"],
                exchange_display=item["exchange_display"],
                quote_type=item["quote_type"],
                score=round(float(item["score"]), 4),
                matched_by=tuple(item["matched_by"]),
                source_url=item["source_url"],
            )
            for item in merged.values()
        ]
        suggestions.sort(
            key=lambda candidate: (
                -candidate.score,
                0 if candidate.exchange == "LSE" else 1,
                merged[candidate.symbol]["source_order"],
            )
        )
        return tuple(suggestions[:limit])

    def resolve_uk_symbol(
        self,
        company_name: str,
        *,
        isin: str | None = None,
        manual_symbol: str | None = None,
        limit: int = 5,
    ) -> SymbolResolution:
        candidates = self.suggest_uk_symbols(
            company_name,
            isin=isin,
            manual_symbol=manual_symbol,
            limit=limit,
        )
        if not candidates:
            context = f"company={company_name!r}"
            if isin:
                context += f", ISIN={isin!r}"
            raise SymbolLookupError(
                f"Yahoo Finance returned no London equity symbol suggestions for {context}. "
                "Enter a verified Yahoo symbol such as BP.L as a manual override."
            )
        selected = candidates[0]
        ambiguous = len(candidates) > 1 and candidates[0].score - candidates[1].score < 0.08
        review_recommended = not selected.is_manual_override and (selected.score < 0.82 or ambiguous)
        return SymbolResolution(
            selected=selected,
            alternatives=candidates[1:],
            review_recommended=review_recommended,
        )

    def get_daily_history(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> DailyPriceHistory:
        symbol = normalize_symbol(symbol)
        if start is not None and not isinstance(start, date):
            raise TypeError("start must be a datetime.date or None")
        if end is not None and not isinstance(end, date):
            raise TypeError("end must be a datetime.date or None")
        if start and end and start > end:
            raise ValueError("start cannot be after end")

        period1 = _date_epoch(start) if start else _EARLIEST_HISTORY_EPOCH
        if end:
            period2 = _date_epoch(end + timedelta(days=1))
        else:
            period2 = int(self._now_utc().timestamp()) + 86_400
        url = _build_chart_url(
            symbol,
            {
                "period1": str(period1),
                "period2": str(period2),
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            },
        )
        result, meta = self._chart_result(url)
        granularity = str(meta.get("dataGranularity") or "")
        if granularity and granularity != "1d":
            raise PriceDataError(
                f"Yahoo silently returned {granularity!r} data instead of daily bars for {symbol}. "
                f"No data was accepted. URL: {url}"
            )

        timestamps = _sequence(result.get("timestamp"))
        indicators = _mapping(result.get("indicators"))
        quote_blocks = _sequence(indicators.get("quote"))
        if not quote_blocks or not isinstance(quote_blocks[0], Mapping):
            raise PriceDataError(f"Yahoo returned no OHLC data for {symbol}. URL: {url}")
        quote_block = quote_blocks[0]
        adj_blocks = _sequence(indicators.get("adjclose"))
        adj_values: Sequence[Any] = ()
        if adj_blocks and isinstance(adj_blocks[0], Mapping):
            adj_values = _sequence(adj_blocks[0].get("adjclose"))

        opens = _sequence(quote_block.get("open"))
        highs = _sequence(quote_block.get("high"))
        lows = _sequence(quote_block.get("low"))
        closes = _sequence(quote_block.get("close"))
        volumes = _sequence(quote_block.get("volume"))
        bars_by_date: dict[date, DailyPriceBar] = {}
        for index, raw_timestamp in enumerate(timestamps):
            timestamp = _optional_int(raw_timestamp)
            close = _value_at_float(closes, index)
            if timestamp is None or close is None:
                continue
            trading_date = datetime.fromtimestamp(timestamp, timezone.utc).date()
            adjusted_close = _value_at_float(adj_values, index)
            bar = DailyPriceBar(
                trading_date=trading_date,
                open=_value_at_float(opens, index),
                high=_value_at_float(highs, index),
                low=_value_at_float(lows, index),
                close=close,
                adjusted_close=adjusted_close if adjusted_close is not None else close,
                volume=_value_at_int(volumes, index),
            )
            bars_by_date[trading_date] = bar
        bars = tuple(bars_by_date[key] for key in sorted(bars_by_date))
        if not bars:
            raise PriceDataError(
                f"Yahoo returned no usable daily close observations for {symbol}. URL: {url}"
            )

        return DailyPriceHistory(
            symbol=str(meta.get("symbol") or symbol).upper(),
            display_name=str(meta.get("longName") or meta.get("shortName") or symbol),
            currency=str(meta.get("currency") or ""),
            exchange=str(meta.get("exchangeName") or meta.get("fullExchangeName") or ""),
            exchange_timezone=str(meta.get("exchangeTimezoneName") or meta.get("timezone") or ""),
            bars=bars,
            requested_start=start,
            requested_end=end,
            is_max_history=start is None and end is None,
            first_trade_time_utc=_epoch_datetime(meta.get("firstTradeDate")),
            fetched_at_utc=self._now_utc(),
            source_url=url,
        )

    def get_latest_price(self, symbol: str) -> LatestPrice:
        symbol = normalize_symbol(symbol)
        url = _build_chart_url(
            symbol,
            {
                "range": "1d",
                "interval": "1m",
                "includePrePost": "false",
                "events": "div,splits",
            },
        )
        result, meta = self._chart_result(url)
        timestamps = _sequence(result.get("timestamp"))
        indicators = _mapping(result.get("indicators"))
        quote_blocks = _sequence(indicators.get("quote"))
        closes: Sequence[Any] = ()
        if quote_blocks and isinstance(quote_blocks[0], Mapping):
            closes = _sequence(quote_blocks[0].get("close"))

        candidates: list[tuple[int, float, str]] = []
        for index, raw_timestamp in enumerate(timestamps):
            timestamp = _optional_int(raw_timestamp)
            close = _value_at_float(closes, index)
            if timestamp is not None and close is not None:
                candidates.append((timestamp, close, "intraday_close"))
        market_time = _optional_int(meta.get("regularMarketTime"))
        market_price = _optional_float(meta.get("regularMarketPrice"))
        if market_time is not None and market_price is not None:
            candidates.append((market_time, market_price, "regular_market_price"))
        if not candidates:
            chart_previous = _optional_float(meta.get("chartPreviousClose"))
            if chart_previous is not None and timestamps:
                fallback_time = _optional_int(timestamps[-1])
                if fallback_time is not None:
                    candidates.append((fallback_time, chart_previous, "previous_close_fallback"))
        if not candidates:
            raise PriceDataError(
                f"Yahoo returned no current or intraday price for {symbol}. URL: {url}"
            )
        as_of_epoch, latest_price, price_kind = max(
            candidates,
            key=lambda item: (item[0], 1 if item[2] == "regular_market_price" else 0),
        )
        now = self._now_utc()
        return LatestPrice(
            symbol=str(meta.get("symbol") or symbol).upper(),
            display_name=str(meta.get("longName") or meta.get("shortName") or symbol),
            price=latest_price,
            currency=str(meta.get("currency") or ""),
            as_of_utc=datetime.fromtimestamp(as_of_epoch, timezone.utc),
            price_kind=price_kind,
            previous_close=_optional_float(meta.get("previousClose") or meta.get("chartPreviousClose")),
            day_high=_optional_float(meta.get("regularMarketDayHigh")),
            day_low=_optional_float(meta.get("regularMarketDayLow")),
            day_volume=_optional_int(meta.get("regularMarketVolume")),
            market_state=_market_state(meta, int(now.timestamp())),
            exchange=str(meta.get("exchangeName") or meta.get("fullExchangeName") or ""),
            exchange_timezone=str(meta.get("exchangeTimezoneName") or meta.get("timezone") or ""),
            delayed_by_minutes=_optional_int(meta.get("exchangeDataDelayedBy")),
            fetched_at_utc=now,
            source_url=url,
        )

    def _chart_result(self, url: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        payload = self._client.get_json(url)
        chart = _mapping(payload.get("chart"))
        error = chart.get("error")
        if isinstance(error, Mapping):
            code = str(error.get("code") or "provider error")
            description = str(error.get("description") or "no description")
            raise PriceDataError(f"Yahoo chart error ({code}): {description}. URL: {url}")
        results = _sequence(chart.get("result"))
        if not results or not isinstance(results[0], Mapping):
            raise PriceDataError(f"Yahoo returned no chart result. URL: {url}")
        result = results[0]
        return result, _mapping(result.get("meta"))

    def _now_utc(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)


def normalize_symbol(symbol: str) -> str:
    """Validate and normalize an exact Yahoo symbol supplied by a user or search."""

    normalized = symbol.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise ValueError(
            "symbol must contain only letters, digits, dot, caret, equals, underscore, or hyphen"
        )
    return normalized


def normalize_isin(isin: str) -> str:
    """Validate basic ISIN shape without claiming to verify issuer identity."""

    normalized = re.sub(r"\s+", "", isin).upper()
    if not _ISIN_RE.fullmatch(normalized):
        raise ValueError("isin must be a 12-character ISIN such as GB0007980591")
    return normalized


def _build_search_url(query_text: str, quote_count: int) -> str:
    return f"{_SEARCH_ENDPOINT}?{urlencode({'q': query_text, 'quotesCount': quote_count, 'newsCount': 0, 'enableFuzzyQuery': 'true'})}"


def _build_chart_url(symbol: str, parameters: Mapping[str, str]) -> str:
    encoded_symbol = quote(symbol, safe=".^=_-")
    return f"{_CHART_ENDPOINT}/{encoded_symbol}?{urlencode(parameters)}"


def _is_uk_equity_quote(raw: Mapping[str, Any]) -> bool:
    quote_type = str(raw.get("quoteType") or "").upper()
    if quote_type and quote_type != "EQUITY":
        return False
    symbol = str(raw.get("symbol") or "").upper()
    exchange = str(raw.get("exchange") or "").upper()
    exchange_display = str(raw.get("exchDisp") or raw.get("exchangeDisplay") or "").lower()
    return exchange in _UK_EXCHANGES or symbol.endswith(".L") or "london" in exchange_display


def _suggestion_score(
    *,
    company_name: str,
    candidate_name: str,
    symbol: str,
    exchange: str,
    match_reason: str,
) -> float:
    if match_reason == "isin_query":
        base = 0.95
    elif company_name:
        wanted = _normalized_company_name(company_name)
        candidate = _normalized_company_name(candidate_name)
        similarity = SequenceMatcher(None, wanted, candidate).ratio() if wanted and candidate else 0.0
        base = 0.55 + 0.36 * similarity
    else:
        base = 0.55
    if exchange == "LSE":
        base += 0.025
    if symbol.endswith(".L"):
        base += 0.015
    return max(0.0, min(base, 1.0))


def _normalized_company_name(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    removable_suffixes = {"plc", "limited", "ltd", "inc", "incorporated", "sa", "ag", "nv"}
    while words and words[-1] in removable_suffixes:
        words.pop()
    return " ".join(words)


def _chart_period(meta: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    periods = _mapping(meta.get("currentTradingPeriod"))
    return _mapping(periods.get(name))


def _market_state(meta: Mapping[str, Any], now_epoch: int) -> str:
    regular = _chart_period(meta, "regular")
    regular_start = _optional_int(regular.get("start"))
    regular_end = _optional_int(regular.get("end"))
    if regular_start is not None and regular_end is not None and regular_start <= now_epoch < regular_end:
        return "REGULAR"
    pre = _chart_period(meta, "pre")
    pre_start = _optional_int(pre.get("start"))
    pre_end = _optional_int(pre.get("end"))
    if pre_start is not None and pre_end is not None and pre_start <= now_epoch < pre_end:
        return "PRE"
    post = _chart_period(meta, "post")
    post_start = _optional_int(post.get("start"))
    post_end = _optional_int(post.get("end"))
    if post_start is not None and post_end is not None and post_start <= now_epoch < post_end:
        return "POST"
    return "CLOSED" if regular_start is not None else "UNKNOWN"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _value_at_float(values: Sequence[Any], index: int) -> float | None:
    return _optional_float(values[index]) if index < len(values) else None


def _value_at_int(values: Sequence[Any], index: int) -> int | None:
    return _optional_int(values[index]) if index < len(values) else None


def _date_epoch(value: date) -> int:
    return int(datetime.combine(value, datetime_time.min, tzinfo=timezone.utc).timestamp())


def _epoch_datetime(value: Any) -> datetime | None:
    epoch = _optional_int(value)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _http_error_detail(error: HTTPError) -> str:
    try:
        raw = error.read(500)
    except Exception:
        return ""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace").replace("\n", " ").strip()[:300]


__all__ = [
    "DailyPriceBar",
    "DailyPriceHistory",
    "JsonClient",
    "LatestPrice",
    "MarketPriceProvider",
    "PriceAdapterError",
    "PriceDataError",
    "PriceNetworkError",
    "PROVIDER_ATTRIBUTION",
    "PROVIDER_LIMITATIONS",
    "SymbolLookupError",
    "SymbolResolution",
    "SymbolSuggestion",
    "UrlLibJsonClient",
    "YahooFinanceProvider",
    "normalize_isin",
    "normalize_symbol",
]
