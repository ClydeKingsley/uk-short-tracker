from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import urlparse


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from short_tracker.update import (  # noqa: E402
    SemanticVersion,
    UpdateChannel,
    UpdateChecker,
    UpdateConfiguration,
)


FIXED_NOW = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
DIGEST = "a" * 64


class MutableClock:
    def __init__(self, value: datetime = FIXED_NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        status: int = 200,
        headers: Message | None = None,
        final_url: str | None = None,
        raw: bytes | None = None,
    ) -> None:
        self.payload = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = headers or Message()
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.final_url or "https://api.github.com/mock"

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


class QueueOpener:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if not self.outcomes:
            raise AssertionError("unexpected network request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome.final_url is None:
            outcome.final_url = request.full_url
        return outcome


def release_payload(
    version: str = "0.2.0",
    *,
    prerelease: bool | None = None,
    digest: str = DIGEST,
    size: int = 12_345,
    assets: list[dict] | None = None,
) -> dict:
    is_preview = "-" in version if prerelease is None else prerelease
    name = f"Short-Tracker-v{version}-windows-x64.zip"
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": is_preview,
        # These two values are deliberately hostile.  The checker must ignore
        # them and derive URLs only from its fixed repository configuration.
        "html_url": "https://evil.example/release",
        "assets": assets
        if assets is not None
        else [
            {
                "name": name,
                "state": "uploaded",
                "size": size,
                "digest": f"sha256:{digest}",
                "browser_download_url": "https://evil.example/payload.exe",
            }
        ],
    }


def http_error(url: str, code: int, headers: Message | None = None) -> HTTPError:
    return HTTPError(url, code, "test error", headers or Message(), BytesIO(b"ignored"))


class SemanticVersionTests(unittest.TestCase):
    def test_strict_semver_precedence(self):
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        parsed = [SemanticVersion.parse(value) for value in ordered]
        self.assertEqual(parsed, sorted(reversed(parsed)))
        self.assertEqual(SemanticVersion.parse("1.0.0+build.1"), SemanticVersion.parse("1.0.0+2"))

    def test_rejects_non_strict_versions_and_tags(self):
        for value in ("v1.2.3", "1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3-01", "  "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SemanticVersion.parse(value)
        with self.assertRaises(ValueError):
            SemanticVersion.from_tag("1.2.3")


class UpdateCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache_path = Path(self.temporary.name) / "settings" / "update-check.json"
        self.clock = MutableClock()
        self.config = UpdateConfiguration(owner="ExampleOrg", repository="short-tracker")

    def checker(self, opener, **kwargs) -> UpdateChecker:
        return UpdateChecker(
            self.cache_path,
            config=kwargs.pop("config", self.config),
            opener=opener,
            clock=self.clock,
            **kwargs,
        )

    def test_unconfigured_checker_is_safely_disabled_without_network_or_cache_write(self):
        opener = QueueOpener()
        checker = self.checker(
            opener,
            config=UpdateConfiguration(owner="", repository=""),
        )

        status = checker.status(current_version="0.1.0").to_dict()
        checked = checker.check(current_version="0.1.0", force=True).to_dict()

        self.assertEqual("disabled", status["status"])
        self.assertEqual("disabled", checked["status"])
        self.assertFalse(checked["enabled"])
        self.assertEqual([], opener.calls)
        self.assertFalse(self.cache_path.exists())

    def test_partial_repository_configuration_is_also_disabled(self):
        owner_only = self.checker(
            QueueOpener(),
            config=UpdateConfiguration(owner="ExampleOrg", repository=""),
        )
        repository_only = self.checker(
            QueueOpener(),
            config=UpdateConfiguration(owner="", repository="short-tracker"),
        )
        self.assertEqual("disabled", owner_only.check(current_version="0.1.0").status)
        self.assertEqual("disabled", repository_only.check(current_version="0.1.0").status)

    def test_stable_check_returns_only_canonical_validated_metadata(self):
        headers = Message()
        headers["ETag"] = 'W/"release-1"'
        headers["Last-Modified"] = "Mon, 24 Aug 2026 09:00:00 GMT"
        opener = QueueOpener(FakeResponse(release_payload(), headers=headers))
        checker = self.checker(opener, timeout_seconds=3.5)

        result = checker.check(current_version="0.1.0").to_dict()

        self.assertEqual("update_available", result["status"])
        self.assertEqual("network", result["source"])
        self.assertTrue(result["cache_fresh"])
        self.assertEqual("0.2.0", result["release"]["version"])
        self.assertEqual(
            "https://github.com/ExampleOrg/short-tracker/releases/tag/v0.2.0",
            result["release"]["release_url"],
        )
        self.assertEqual(
            "https://github.com/ExampleOrg/short-tracker/releases/download/"
            "v0.2.0/Short-Tracker-v0.2.0-windows-x64.zip",
            result["release"]["asset"]["download_url"],
        )
        self.assertEqual(DIGEST, result["release"]["asset"]["sha256"])
        self.assertNotIn("evil.example", json.dumps(result))
        self.assertEqual(1, len(opener.calls))
        request, timeout = opener.calls[0]
        self.assertEqual(3.5, timeout)
        self.assertEqual(
            "https://api.github.com/repos/ExampleOrg/short-tracker/releases/latest",
            request.full_url,
        )
        self.assertEqual("application/vnd.github+json", request.get_header("Accept"))
        self.assertEqual("2022-11-28", request.get_header("X-github-api-version"))
        self.assertEqual("identity", request.get_header("Accept-encoding"))
        self.assertTrue(self.cache_path.is_file())

    def test_normal_check_uses_24_hour_cache_but_force_revalidates(self):
        opener = QueueOpener(FakeResponse(release_payload()), FakeResponse(release_payload("0.3.0")))
        checker = self.checker(opener)

        first = checker.check(current_version="0.1.0")
        self.clock.value += timedelta(hours=23, minutes=59)
        cached = checker.check(current_version="0.1.0", force=False)
        forced = checker.check(current_version="0.1.0", force=True)

        self.assertEqual("0.2.0", first.release.version)
        self.assertEqual("cache", cached.source)
        self.assertEqual("0.2.0", cached.release.version)
        self.assertEqual("network", forced.source)
        self.assertEqual("0.3.0", forced.release.version)
        self.assertEqual(2, len(opener.calls))

    def test_status_is_cache_only_and_marks_stale_metadata(self):
        opener = QueueOpener(FakeResponse(release_payload()))
        checker = self.checker(opener)
        checker.check(current_version="0.1.0")
        self.clock.value += timedelta(days=2)

        status = checker.status(current_version="0.1.0")

        self.assertEqual("update_available", status.status)
        self.assertEqual("cache", status.source)
        self.assertFalse(status.cache_fresh)
        self.assertEqual(1, len(opener.calls))

    def test_etag_and_last_modified_revalidate_with_304(self):
        initial_headers = Message()
        initial_headers["ETag"] = '"abc"'
        initial_headers["Last-Modified"] = "Sun, 23 Aug 2026 10:00:00 GMT"
        not_modified_headers = Message()
        not_modified_headers["ETag"] = '"abc"'
        opener = QueueOpener(
            FakeResponse(release_payload(), headers=initial_headers),
            http_error("https://api.github.com/mock", 304, not_modified_headers),
        )
        checker = self.checker(opener)
        checker.check(current_version="0.1.0")
        self.clock.value += timedelta(hours=25)

        result = checker.check(current_version="0.1.0")

        self.assertEqual("update_available", result.status)
        self.assertEqual("network", result.source)
        request = opener.calls[1][0]
        self.assertEqual('"abc"', request.get_header("If-none-match"))
        self.assertEqual(
            "Sun, 23 Aug 2026 10:00:00 GMT", request.get_header("If-modified-since")
        )
        self.assertEqual("2026-08-25T11:00:00Z", result.checked_at_utc)

    def test_equal_or_newer_current_version_is_up_to_date(self):
        for current in ("0.2.0", "0.3.0"):
            with self.subTest(current=current):
                opener = QueueOpener(FakeResponse(release_payload()))
                checker = UpdateChecker(
                    Path(self.temporary.name) / current / "cache.json",
                    config=self.config,
                    opener=opener,
                    clock=self.clock,
                )
                self.assertEqual("up_to_date", checker.check(current_version=current).status)

    def test_preview_channel_lists_releases_and_uses_semver_precedence(self):
        preview_config = UpdateConfiguration(
            owner="ExampleOrg", repository="short-tracker", channel=UpdateChannel.PREVIEW
        )
        payload = [
            release_payload("1.0.0"),
            release_payload("1.1.0-beta.2"),
            release_payload("1.1.0-beta.10"),
            {**release_payload("9.0.0-alpha.1"), "draft": True},
        ]
        opener = QueueOpener(FakeResponse(payload))
        checker = self.checker(opener, config=preview_config)

        result = checker.check(current_version="1.0.0")

        self.assertEqual("update_available", result.status)
        self.assertEqual("1.1.0-beta.10", result.release.version)
        self.assertTrue(result.release.prerelease)
        self.assertEqual(
            "https://api.github.com/repos/ExampleOrg/short-tracker/releases?per_page=20",
            opener.calls[0][0].full_url,
        )

    def test_stable_channel_rejects_prerelease_payload(self):
        opener = QueueOpener(FakeResponse(release_payload("0.2.0-beta.1")))
        result = self.checker(opener).check(current_version="0.1.0")
        self.assertEqual("unavailable", result.status)
        self.assertEqual("invalid_response", result.error_code)
        self.assertIsNone(result.release)

    def test_invalid_semver_tags_are_never_returned(self):
        for tag in ("0.2.0", "v00.2.0", "v0.2", "v0.2.0-01"):
            with self.subTest(tag=tag):
                payload = release_payload()
                payload["tag_name"] = tag
                path = Path(self.temporary.name) / tag.replace("/", "_") / "cache.json"
                result = UpdateChecker(
                    path,
                    config=self.config,
                    opener=QueueOpener(FakeResponse(payload)),
                    clock=self.clock,
                ).check(current_version="0.1.0")
                self.assertEqual("invalid_response", result.error_code)
                self.assertIsNone(result.release)

    def test_missing_duplicate_or_unhashed_asset_is_never_returned(self):
        name = "Short-Tracker-v0.2.0-windows-x64.zip"
        valid = {"name": name, "state": "uploaded", "size": 10, "digest": f"sha256:{DIGEST}"}
        cases = {
            "missing": [],
            "duplicate": [valid, dict(valid)],
            "no_digest": [{**valid, "digest": None}],
            "wrong_hash": [{**valid, "digest": "sha256:not-a-hash"}],
            "zero_size": [{**valid, "size": 0}],
            "not_uploaded": [{**valid, "state": "new"}],
        }
        for label, assets in cases.items():
            with self.subTest(label=label):
                path = Path(self.temporary.name) / label / "cache.json"
                result = UpdateChecker(
                    path,
                    config=self.config,
                    opener=QueueOpener(FakeResponse(release_payload(assets=assets))),
                    clock=self.clock,
                ).check(current_version="0.1.0")
                self.assertEqual("invalid_response", result.error_code)
                self.assertIsNone(result.release)

    def test_response_size_is_bounded_before_json_parsing(self):
        headers = Message()
        headers["Content-Length"] = "1000"
        opener = QueueOpener(FakeResponse(None, headers=headers, raw=b"{}"))
        checker = self.checker(opener, max_response_bytes=50)

        result = checker.check(current_version="0.1.0")

        self.assertEqual("invalid_response", result.error_code)
        self.assertIsNone(result.release)

    def test_403_retry_after_is_persisted_and_force_cannot_bypass_it(self):
        headers = Message()
        headers["Retry-After"] = "7200"
        opener = QueueOpener(
            http_error("https://api.github.com/mock", 403, headers),
            FakeResponse(release_payload()),
        )
        checker = self.checker(opener)

        limited = checker.check(current_version="0.1.0", force=True)
        blocked = checker.check(current_version="0.1.0", force=True)

        self.assertEqual("rate_limited", limited.error_code)
        self.assertEqual("2026-08-24T12:00:00Z", limited.retry_at_utc)
        self.assertEqual("cache", blocked.source)
        self.assertEqual(1, len(opener.calls))

        self.clock.value += timedelta(hours=2, seconds=1)
        recovered = checker.check(current_version="0.1.0", force=True)
        self.assertEqual("update_available", recovered.status)
        self.assertIsNone(recovered.error_code)
        self.assertEqual(2, len(opener.calls))

    def test_429_http_date_and_rate_limit_reset_use_later_safe_backoff(self):
        headers = Message()
        headers["Retry-After"] = "Mon, 24 Aug 2026 11:30:00 GMT"
        headers["X-RateLimit-Reset"] = str(int((FIXED_NOW + timedelta(hours=3)).timestamp()))
        opener = QueueOpener(http_error("https://api.github.com/mock", 429, headers))

        result = self.checker(opener).check(current_version="0.1.0", force=True)

        self.assertEqual("rate_limited", result.error_code)
        self.assertEqual("2026-08-24T13:00:00Z", result.retry_at_utc)

    def test_rate_limit_backoff_is_capped_to_one_day(self):
        headers = Message()
        headers["Retry-After"] = str(30 * 24 * 60 * 60)
        opener = QueueOpener(http_error("https://api.github.com/mock", 429, headers))

        result = self.checker(opener).check(current_version="0.1.0", force=True)

        self.assertEqual("2026-08-25T10:00:00Z", result.retry_at_utc)

    def test_timeout_and_invalid_json_return_safe_codes_without_raw_details(self):
        cases = {
            "timeout": (TimeoutError("private network detail"), "network_error"),
            "json": (FakeResponse(None, raw=b"not-json"), "invalid_response"),
        }
        for label, (outcome, expected) in cases.items():
            with self.subTest(label=label):
                path = Path(self.temporary.name) / label / "cache.json"
                result = UpdateChecker(
                    path,
                    config=self.config,
                    opener=QueueOpener(outcome),
                    clock=self.clock,
                ).check(current_version="0.1.0")
                payload = result.to_dict()
                self.assertEqual(expected, payload["error_code"])
                self.assertNotIn("private network detail", json.dumps(payload))

    def test_unsafe_final_response_url_is_rejected(self):
        opener = QueueOpener(
            FakeResponse(release_payload(), final_url="http://evil.example/releases/latest")
        )
        result = self.checker(opener).check(current_version="0.1.0")
        self.assertEqual("invalid_response", result.error_code)
        self.assertIsNone(result.release)

    def test_cached_urls_are_ignored_and_reconstructed(self):
        opener = QueueOpener(FakeResponse(release_payload()))
        checker = self.checker(opener)
        checker.check(current_version="0.1.0")
        cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
        cached["release"]["release_url"] = "https://evil.example/release"
        cached["release"]["asset"]["download_url"] = "https://evil.example/payload"
        self.cache_path.write_text(json.dumps(cached), encoding="utf-8")

        status = checker.status(current_version="0.1.0").to_dict()

        self.assertNotIn("evil.example", json.dumps(status))
        self.assertEqual(
            "https://github.com/ExampleOrg/short-tracker/releases/tag/v0.2.0",
            status["release"]["release_url"],
        )

    def test_invalid_current_version_never_calls_network(self):
        opener = QueueOpener()
        result = self.checker(opener).check(current_version="0.1")
        self.assertEqual("unavailable", result.status)
        self.assertEqual("invalid_current_version", result.error_code)
        self.assertEqual([], opener.calls)

    def test_only_github_api_metadata_endpoint_is_requested(self):
        opener = QueueOpener(FakeResponse(release_payload()))
        self.checker(opener).check(current_version="0.1.0", force=True)
        self.assertEqual(1, len(opener.calls))
        parsed = urlparse(opener.calls[0][0].full_url)
        self.assertEqual("https", parsed.scheme)
        self.assertEqual("api.github.com", parsed.hostname)
        self.assertNotIn("download", parsed.path)


if __name__ == "__main__":
    unittest.main()
