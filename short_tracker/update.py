"""Safe, check-only GitHub Release discovery for Short Tracker.

This module deliberately stops at validated metadata.  It never downloads an
asset, launches an installer, or changes application files.  It checks only the
canonical public GitHub repository selected for Short Tracker releases.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from functools import total_ordering
import json
from pathlib import Path
import re
import threading
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cache import AtomicJsonStore


# Canonical public release source. Update checks remain metadata-only and never
# download, install, or replace application files.
DEFAULT_GITHUB_OWNER: Final[str] = "ClydeKingsley"
DEFAULT_GITHUB_REPOSITORY: Final[str] = "uk-short-tracker"

DEFAULT_CACHE_TTL: Final[timedelta] = timedelta(hours=24)
DEFAULT_TIMEOUT_SECONDS: Final[float] = 6.0
DEFAULT_MAX_RESPONSE_BYTES: Final[int] = 2_000_000
DEFAULT_RATE_LIMIT_BACKOFF: Final[timedelta] = timedelta(hours=1)
MAX_RATE_LIMIT_BACKOFF: Final[timedelta] = timedelta(days=1)
GITHUB_API_VERSION: Final[str] = "2022-11-28"
GITHUB_API_HOST: Final[str] = "api.github.com"
GITHUB_WEB_HOST: Final[str] = "github.com"
DEFAULT_ASSET_NAME_TEMPLATE: Final[str] = "Short-Tracker-v{version}-windows-x64.zip"
MAX_ASSET_BYTES: Final[int] = 512 * 1024 * 1024
_CACHE_SCHEMA: Final[int] = 1

_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NUMERIC_IDENTIFIER = r"(?:0|[1-9][0-9]*)"
_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER_RE = re.compile(
    rf"^(?P<major>{_NUMERIC_IDENTIFIER})\."
    rf"(?P<minor>{_NUMERIC_IDENTIFIER})\."
    rf"(?P<patch>{_NUMERIC_IDENTIFIER})"
    rf"(?:-(?P<prerelease>{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SAFE_ERROR_CODES = frozenset(
    {
        "check_in_progress",
        "http_error",
        "invalid_cache",
        "invalid_current_version",
        "invalid_response",
        "network_error",
        "no_published_release",
        "rate_limited",
    }
)


class UpdateChannel(str, Enum):
    """Release stream used for discovery."""

    STABLE = "stable"
    PREVIEW = "preview"


@total_ordering
@dataclass(frozen=True, slots=True, eq=False)
class SemanticVersion:
    """Strict SemVer 2.0 value with precedence comparison."""

    text: str
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]
    build: tuple[str, ...]

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        if not isinstance(value, str):
            raise ValueError("version must be a string")
        text = value.strip()
        if not text or len(text) > 128:
            raise ValueError("version is empty or too long")
        match = _SEMVER_RE.fullmatch(text)
        if match is None:
            raise ValueError(f"not a strict semantic version: {value!r}")
        prerelease = tuple((match.group("prerelease") or "").split("."))
        build = tuple((match.group("build") or "").split("."))
        return cls(
            text=text,
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=() if prerelease == ("",) else prerelease,
            build=() if build == ("",) else build,
        )

    @classmethod
    def from_tag(cls, tag: str) -> "SemanticVersion":
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise ValueError("release tag must start with lowercase 'v'")
        return cls.parse(tag[1:])

    def _compare(self, other: "SemanticVersion") -> int:
        left_core = (self.major, self.minor, self.patch)
        right_core = (other.major, other.minor, other.patch)
        if left_core != right_core:
            return 1 if left_core > right_core else -1
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return 1 if int(left) > int(right) else -1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return 1 if left > right else -1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return 1 if len(self.prerelease) > len(other.prerelease) else -1

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._compare(other) == 0

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._compare(other) < 0


@dataclass(frozen=True, slots=True)
class UpdateConfiguration:
    """Fixed repository and channel policy supplied by application code."""

    owner: str = DEFAULT_GITHUB_OWNER
    repository: str = DEFAULT_GITHUB_REPOSITORY
    channel: UpdateChannel | str = UpdateChannel.STABLE
    asset_name_template: str = DEFAULT_ASSET_NAME_TEMPLATE

    def __post_init__(self) -> None:
        owner = self.owner.strip()
        repository = self.repository.strip()
        try:
            channel = self.channel if isinstance(self.channel, UpdateChannel) else UpdateChannel(self.channel)
        except ValueError as exc:
            raise ValueError("channel must be 'stable' or 'preview'") from exc
        if owner and _SLUG_RE.fullmatch(owner) is None:
            raise ValueError("invalid GitHub owner")
        if repository and _SLUG_RE.fullmatch(repository) is None:
            raise ValueError("invalid GitHub repository")
        _validate_asset_template(self.asset_name_template)
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "channel", channel)

    @property
    def enabled(self) -> bool:
        return bool(self.owner and self.repository)

    @property
    def repository_key(self) -> str:
        if not self.enabled:
            return ""
        return f"{self.owner.casefold()}/{self.repository.casefold()}"

    def asset_name(self, version: str) -> str:
        name = self.asset_name_template.format(version=version)
        if not _is_safe_asset_name(name):
            raise ValueError("asset template produced an unsafe file name")
        return name


@dataclass(frozen=True, slots=True)
class AssetMetadata:
    """A single exact Windows ZIP asset validated from GitHub metadata."""

    name: str
    bytes: int
    sha256: str
    download_url: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "download_url": self.download_url,
        }


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    """Small allow-listed subset of one GitHub Release response."""

    version: str
    tag: str
    prerelease: bool
    release_url: str
    asset: AssetMetadata

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "tag": self.tag,
            "prerelease": self.prerelease,
            "release_url": self.release_url,
            "asset": self.asset.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    """JSON-ready result shared by status and explicit check endpoints."""

    enabled: bool
    status: str
    channel: str
    current_version: str
    source: str
    cache_fresh: bool
    checked_at_utc: str | None
    next_check_at_utc: str | None
    retry_at_utc: str | None
    error_code: str | None
    release: ReleaseMetadata | None

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "channel": self.channel,
            "current_version": self.current_version,
            "source": self.source,
            "cache_fresh": self.cache_fresh,
            "checked_at_utc": self.checked_at_utc,
            "next_check_at_utc": self.next_check_at_utc,
            "retry_at_utc": self.retry_at_utc,
            "error_code": self.error_code,
            "release": self.release.to_dict() if self.release is not None else None,
        }


@dataclass(frozen=True, slots=True)
class _CacheState:
    checked_at: datetime | None = None
    etag: str | None = None
    last_modified: str | None = None
    retry_at: datetime | None = None
    error_code: str | None = None
    release: ReleaseMetadata | None = None
    invalid: bool = False


class _InvalidResponse(ValueError):
    pass


class _SafeGitHubRedirectHandler(HTTPRedirectHandler):
    """Refuse metadata redirects outside the HTTPS GitHub API origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not _is_https_host(newurl, GITHUB_API_HOST):
            raise HTTPError(req.full_url, code, "unsafe GitHub API redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UpdateChecker:
    """Discover releases while enforcing cache, channel, and metadata policy."""

    def __init__(
        self,
        cache_path: str | Path,
        *,
        config: UpdateConfiguration | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        store: AtomicJsonStore | None = None,
    ) -> None:
        if cache_ttl.total_seconds() <= 0:
            raise ValueError("cache_ttl must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.cache_path = Path(cache_path)
        self.config = config or UpdateConfiguration()
        self.cache_ttl = cache_ttl
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if opener is None:
            safe_opener = build_opener(_SafeGitHubRedirectHandler())
            self._opener: Callable[..., Any] = safe_opener.open
        else:
            self._opener = opener
        self._store = store or AtomicJsonStore()
        self._check_lock = threading.Lock()

    def status(self, *, current_version: str) -> UpdateCheckResult:
        """Return local cached status without performing network I/O."""

        now = self._now()
        if not self.config.enabled:
            return self._disabled_result(current_version)
        current = self._parse_current_version(current_version)
        if current is None:
            return self._error_result(current_version, "invalid_current_version", source="cache")
        state = self._read_cache(now)
        return self._result_from_state(current, state, now=now, source="cache")

    def check(self, *, current_version: str, force: bool = False) -> UpdateCheckResult:
        """Check GitHub when due, or return validated cache metadata.

        ``force`` bypasses only the normal 24-hour success cache.  It never
        bypasses a persisted 403/429 rate-limit window.
        """

        now = self._now()
        if not self.config.enabled:
            return self._disabled_result(current_version)
        current = self._parse_current_version(current_version)
        if current is None:
            return self._error_result(current_version, "invalid_current_version", source="cache")
        state = self._read_cache(now)
        if state.retry_at is not None and now < state.retry_at and state.error_code == "rate_limited":
            return self._result_from_state(current, state, now=now, source="cache")
        if not force and self._is_fresh(state, now):
            return self._result_from_state(current, state, now=now, source="cache")
        if not self._check_lock.acquire(blocking=False):
            return self._result_from_state(
                current,
                state,
                now=now,
                source="cache",
                error_code="check_in_progress",
            )
        try:
            # A previous waiting request may have populated the cache before we
            # acquired the lock.  Re-read and apply the same safety gates.
            now = self._now()
            state = self._read_cache(now)
            if state.retry_at is not None and now < state.retry_at and state.error_code == "rate_limited":
                return self._result_from_state(current, state, now=now, source="cache")
            if not force and self._is_fresh(state, now):
                return self._result_from_state(current, state, now=now, source="cache")
            return self._network_check(current, state, now=now)
        finally:
            self._check_lock.release()

    def _network_check(
        self,
        current: SemanticVersion,
        state: _CacheState,
        *,
        now: datetime,
    ) -> UpdateCheckResult:
        request = self._request(state)
        try:
            payload, response_headers = self._get_json(request)
            release = self._select_release(payload)
        except HTTPError as exc:
            if exc.code == 304:
                if state.checked_at is None or state.invalid:
                    failed = self._record_error(state, now=now, error_code="invalid_response")
                    return self._result_from_state(current, failed, now=now, source="network")
                refreshed = _CacheState(
                    checked_at=now,
                    etag=_safe_header(_header(exc.headers, "ETag")) or state.etag,
                    last_modified=(
                        _safe_header(_header(exc.headers, "Last-Modified")) or state.last_modified
                    ),
                    release=state.release,
                )
                self._write_cache(refreshed)
                return self._result_from_state(current, refreshed, now=now, source="network")
            if exc.code in {403, 429}:
                retry_at = _rate_limit_retry_at(exc.headers, now)
                limited = _CacheState(
                    checked_at=state.checked_at,
                    etag=state.etag,
                    last_modified=state.last_modified,
                    retry_at=retry_at,
                    error_code="rate_limited",
                    release=state.release,
                )
                self._write_cache(limited)
                return self._result_from_state(current, limited, now=now, source="network")
            if exc.code == 404:
                empty = _CacheState(checked_at=now, error_code="no_published_release")
                self._write_cache(empty)
                return self._result_from_state(current, empty, now=now, source="network")
            failed = self._record_error(state, now=now, error_code="http_error")
            return self._result_from_state(current, failed, now=now, source="network")
        except (_InvalidResponse, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            failed = self._record_error(state, now=now, error_code="invalid_response")
            return self._result_from_state(current, failed, now=now, source="network")
        except (URLError, TimeoutError, ConnectionError, OSError):
            failed = self._record_error(state, now=now, error_code="network_error")
            return self._result_from_state(current, failed, now=now, source="network")

        checked = _CacheState(
            checked_at=now,
            etag=_safe_header(response_headers.get("etag")),
            last_modified=_safe_header(response_headers.get("last-modified")),
            error_code=None if release is not None else "no_published_release",
            release=release,
        )
        self._write_cache(checked)
        return self._result_from_state(current, checked, now=now, source="network")

    def _request(self, state: _CacheState) -> Request:
        owner = quote(self.config.owner, safe="")
        repository = quote(self.config.repository, safe="")
        if self.config.channel is UpdateChannel.STABLE:
            url = f"https://{GITHUB_API_HOST}/repos/{owner}/{repository}/releases/latest"
        else:
            url = f"https://{GITHUB_API_HOST}/repos/{owner}/{repository}/releases?per_page=20"
        headers = {
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "User-Agent": "ShortTracker-update-check/1",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified
        return Request(url, headers=headers, method="GET")

    def _get_json(self, request: Request) -> tuple[Any, dict[str, str]]:
        if not _is_https_host(request.full_url, GITHUB_API_HOST):
            raise _InvalidResponse("update request is outside the GitHub API")
        with self._opener(request, timeout=self.timeout_seconds) as response:
            status_value = getattr(response, "status", None)
            if status_value is None:
                status_value = response.getcode()
            status = int(status_value)
            if status != 200:
                raise HTTPError(request.full_url, status, "unexpected HTTP status", response.headers, None)
            final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
            if not _is_https_host(final_url, GITHUB_API_HOST):
                raise _InvalidResponse("GitHub API response used an unsafe final URL")
            content_length = _header(getattr(response, "headers", None), "Content-Length")
            if content_length:
                try:
                    if int(content_length) > self.max_response_bytes:
                        raise _InvalidResponse("GitHub response is too large")
                except ValueError as exc:
                    raise _InvalidResponse("invalid Content-Length") from exc
            raw = response.read(self.max_response_bytes + 1)
            if not isinstance(raw, (bytes, bytearray)):
                raise _InvalidResponse("GitHub response body is not bytes")
            if len(raw) > self.max_response_bytes:
                raise _InvalidResponse("GitHub response is too large")
            payload = json.loads(raw.decode("utf-8"))
            headers = getattr(response, "headers", None)
            return payload, {
                "etag": _header(headers, "ETag") or "",
                "last-modified": _header(headers, "Last-Modified") or "",
            }

    def _select_release(self, payload: Any) -> ReleaseMetadata | None:
        if self.config.channel is UpdateChannel.STABLE:
            if not isinstance(payload, Mapping):
                raise _InvalidResponse("stable release response must be an object")
            return self._release_from_api(payload)
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
            raise _InvalidResponse("preview release response must be an array")
        releases: list[tuple[SemanticVersion, ReleaseMetadata]] = []
        for raw in payload:
            if not isinstance(raw, Mapping) or raw.get("draft") is True:
                continue
            try:
                release = self._release_from_api(raw)
                releases.append((SemanticVersion.parse(release.version), release))
            except (_InvalidResponse, ValueError, TypeError):
                continue
        if not releases:
            return None
        return max(releases, key=lambda item: item[0])[1]

    def _release_from_api(self, raw: Mapping[str, Any]) -> ReleaseMetadata:
        if raw.get("draft") is not False or not isinstance(raw.get("prerelease"), bool):
            raise _InvalidResponse("release publication flags are invalid")
        prerelease_flag = bool(raw["prerelease"])
        tag = raw.get("tag_name")
        version = SemanticVersion.from_tag(tag)
        if prerelease_flag != bool(version.prerelease):
            raise _InvalidResponse("release tag and prerelease flag disagree")
        if self.config.channel is UpdateChannel.STABLE and prerelease_flag:
            raise _InvalidResponse("stable channel received a prerelease")
        expected_name = self.config.asset_name(version.text)
        assets = raw.get("assets")
        if not isinstance(assets, Sequence) or isinstance(assets, (str, bytes, bytearray)):
            raise _InvalidResponse("release assets must be an array")
        matches = [asset for asset in assets if isinstance(asset, Mapping) and asset.get("name") == expected_name]
        if len(matches) != 1:
            raise _InvalidResponse("release must contain exactly one expected Windows asset")
        asset = matches[0]
        if asset.get("state") != "uploaded":
            raise _InvalidResponse("release asset is not uploaded")
        size = asset.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_ASSET_BYTES:
            raise _InvalidResponse("release asset size is invalid")
        digest_value = asset.get("digest")
        if not isinstance(digest_value, str) or not digest_value.startswith("sha256:"):
            raise _InvalidResponse("release asset has no SHA-256 digest")
        digest = digest_value.removeprefix("sha256:").casefold()
        if _SHA256_RE.fullmatch(digest) is None:
            raise _InvalidResponse("release asset SHA-256 digest is invalid")
        release_url = self._release_url(tag)
        download_url = self._download_url(tag, expected_name)
        return ReleaseMetadata(
            version=version.text,
            tag=tag,
            prerelease=prerelease_flag,
            release_url=release_url,
            asset=AssetMetadata(
                name=expected_name,
                bytes=size,
                sha256=digest,
                download_url=download_url,
            ),
        )

    def _release_from_cache(self, raw: Any) -> ReleaseMetadata | None:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise _InvalidResponse("cached release is not an object")
        version = SemanticVersion.parse(raw.get("version"))
        tag = raw.get("tag")
        if not isinstance(tag, str) or tag != f"v{version.text}":
            raise _InvalidResponse("cached tag does not match version")
        prerelease = raw.get("prerelease")
        if not isinstance(prerelease, bool) or prerelease != bool(version.prerelease):
            raise _InvalidResponse("cached prerelease flag is invalid")
        if self.config.channel is UpdateChannel.STABLE and prerelease:
            raise _InvalidResponse("cached prerelease is invalid for stable channel")
        asset = raw.get("asset")
        if not isinstance(asset, Mapping):
            raise _InvalidResponse("cached asset is not an object")
        expected_name = self.config.asset_name(version.text)
        if asset.get("name") != expected_name:
            raise _InvalidResponse("cached asset name is invalid")
        size = asset.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_ASSET_BYTES:
            raise _InvalidResponse("cached asset size is invalid")
        digest = asset.get("sha256")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise _InvalidResponse("cached asset digest is invalid")
        # Never trust cached URLs.  Reconstruct both from the fixed repository,
        # validated tag, and exact configured asset name.
        return ReleaseMetadata(
            version=version.text,
            tag=tag,
            prerelease=prerelease,
            release_url=self._release_url(tag),
            asset=AssetMetadata(
                name=expected_name,
                bytes=size,
                sha256=digest,
                download_url=self._download_url(tag, expected_name),
            ),
        )

    def _release_url(self, tag: str) -> str:
        return (
            f"https://{GITHUB_WEB_HOST}/{quote(self.config.owner, safe='')}/"
            f"{quote(self.config.repository, safe='')}/releases/tag/{quote(tag, safe='')}"
        )

    def _download_url(self, tag: str, asset_name: str) -> str:
        return (
            f"https://{GITHUB_WEB_HOST}/{quote(self.config.owner, safe='')}/"
            f"{quote(self.config.repository, safe='')}/releases/download/"
            f"{quote(tag, safe='')}/{quote(asset_name, safe='')}"
        )

    def _read_cache(self, now: datetime) -> _CacheState:
        raw = self._store.read(self.cache_path, {})
        if not raw:
            return _CacheState()
        if not isinstance(raw, Mapping):
            return _CacheState(invalid=True, error_code="invalid_cache")
        identity_matches = (
            raw.get("schema") == _CACHE_SCHEMA
            and raw.get("repository") == self.config.repository_key
            and raw.get("channel") == self.config.channel.value
            and raw.get("asset_name_template") == self.config.asset_name_template
        )
        if not identity_matches:
            return _CacheState()
        try:
            checked_at = _parse_utc(raw.get("checked_at_utc"))
            retry_at = _parse_utc(raw.get("retry_at_utc"))
            if checked_at is not None and checked_at > now + timedelta(minutes=5):
                raise _InvalidResponse("cached check time is in the future")
            if retry_at is not None and retry_at > now + MAX_RATE_LIMIT_BACKOFF:
                retry_at = now + MAX_RATE_LIMIT_BACKOFF
            error_code = raw.get("error_code")
            if error_code is not None and error_code not in _SAFE_ERROR_CODES:
                raise _InvalidResponse("cached error code is invalid")
            return _CacheState(
                checked_at=checked_at,
                etag=_safe_header(raw.get("etag")),
                last_modified=_safe_header(raw.get("last_modified")),
                retry_at=retry_at,
                error_code=error_code,
                release=self._release_from_cache(raw.get("release")),
            )
        except (_InvalidResponse, TypeError, ValueError):
            return _CacheState(invalid=True, error_code="invalid_cache")

    def _write_cache(self, state: _CacheState) -> None:
        self._store.write(
            self.cache_path,
            {
                "schema": _CACHE_SCHEMA,
                "repository": self.config.repository_key,
                "channel": self.config.channel.value,
                "asset_name_template": self.config.asset_name_template,
                "checked_at_utc": _format_utc(state.checked_at),
                "etag": state.etag,
                "last_modified": state.last_modified,
                "retry_at_utc": _format_utc(state.retry_at),
                "error_code": state.error_code,
                "release": state.release.to_dict() if state.release is not None else None,
            },
        )

    def _record_error(self, state: _CacheState, *, now: datetime, error_code: str) -> _CacheState:
        failed = _CacheState(
            checked_at=state.checked_at,
            etag=state.etag,
            last_modified=state.last_modified,
            error_code=error_code,
            release=state.release,
        )
        self._write_cache(failed)
        return failed

    def _result_from_state(
        self,
        current: SemanticVersion,
        state: _CacheState,
        *,
        now: datetime,
        source: str,
        error_code: str | None = None,
    ) -> UpdateCheckResult:
        release = state.release
        if release is not None:
            latest = SemanticVersion.parse(release.version)
            status = "update_available" if latest > current else "up_to_date"
        elif state.checked_at is not None or state.error_code is not None or state.invalid:
            status = "unavailable"
        else:
            status = "not_checked"
        cache_fresh = self._is_fresh(state, now)
        next_check = state.checked_at + self.cache_ttl if state.checked_at is not None else None
        if state.retry_at is not None and (next_check is None or state.retry_at > next_check):
            next_check = state.retry_at
        return UpdateCheckResult(
            enabled=True,
            status=status,
            channel=self.config.channel.value,
            current_version=current.text,
            source=source,
            cache_fresh=cache_fresh,
            checked_at_utc=_format_utc(state.checked_at),
            next_check_at_utc=_format_utc(next_check),
            retry_at_utc=_format_utc(state.retry_at),
            error_code=error_code or state.error_code,
            release=release,
        )

    def _disabled_result(self, current_version: str) -> UpdateCheckResult:
        return UpdateCheckResult(
            enabled=False,
            status="disabled",
            channel=self.config.channel.value,
            current_version=current_version.strip() if isinstance(current_version, str) else "",
            source="disabled",
            cache_fresh=False,
            checked_at_utc=None,
            next_check_at_utc=None,
            retry_at_utc=None,
            error_code=None,
            release=None,
        )

    def _error_result(self, current_version: str, code: str, *, source: str) -> UpdateCheckResult:
        return UpdateCheckResult(
            enabled=True,
            status="unavailable",
            channel=self.config.channel.value,
            current_version=current_version.strip() if isinstance(current_version, str) else "",
            source=source,
            cache_fresh=False,
            checked_at_utc=None,
            next_check_at_utc=None,
            retry_at_utc=None,
            error_code=code,
            release=None,
        )

    def _is_fresh(self, state: _CacheState, now: datetime) -> bool:
        if state.checked_at is None:
            return False
        age = now - state.checked_at
        return timedelta(0) <= age < self.cache_ttl

    def _parse_current_version(self, value: str) -> SemanticVersion | None:
        try:
            return SemanticVersion.parse(value)
        except (TypeError, ValueError):
            return None

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return datetime")
        if value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)


def _validate_asset_template(template: str) -> None:
    if not isinstance(template, str) or template.count("{version}") != 1:
        raise ValueError("asset_name_template must contain exactly one {version} placeholder")
    if "{" in template.replace("{version}", "") or "}" in template.replace("{version}", ""):
        raise ValueError("asset_name_template contains an unsupported placeholder")
    try:
        rendered = template.format(version="0.0.0")
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError("invalid asset_name_template") from exc
    if not _is_safe_asset_name(rendered):
        raise ValueError("asset_name_template must produce one ZIP file name")


def _is_safe_asset_name(name: str) -> bool:
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 180
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and not any(ord(char) < 32 for char in name)
        and name.casefold().endswith(".zip")
    )


def _is_https_host(url: str, expected_host: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == expected_host.casefold()
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
    )


def _safe_header(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 256 or "\r" in text or "\n" in text:
        return None
    return text


def _header(headers: Any, name: str) -> str | None:
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get(name)
    return str(value) if value is not None else None


def _parse_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _InvalidResponse("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise _InvalidResponse("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rate_limit_retry_at(headers: Any, now: datetime) -> datetime:
    candidates = [now + DEFAULT_RATE_LIMIT_BACKOFF]
    retry_after = _header(headers, "Retry-After")
    if retry_after:
        parsed_retry = _parse_retry_after(retry_after, now)
        if parsed_retry is not None:
            candidates.append(parsed_retry)
    reset = _header(headers, "X-RateLimit-Reset")
    if reset:
        try:
            reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
            if reset_at > now:
                candidates.append(reset_at)
        except (OverflowError, ValueError):
            pass
    retry_at = max(candidates)
    return min(retry_at, now + MAX_RATE_LIMIT_BACKOFF)


def _parse_retry_after(value: str, now: datetime) -> datetime | None:
    text = value.strip()
    try:
        seconds = int(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) if parsed > now else now
    return now + timedelta(seconds=max(0, seconds))
