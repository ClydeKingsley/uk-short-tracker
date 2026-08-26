# Short Tracker

[简体中文](README.zh-CN.md)

[Website](https://ukshort.com) ·
[GitHub repository](https://github.com/ClydeKingsley/uk-short-tracker) ·
[Releases](https://github.com/ClydeKingsley/uk-short-tracker/releases)

Short Tracker is a local Windows dashboard for researching UK public net short
position disclosures. It keeps the FCA's legacy named disclosures separate
from the anonymous aggregate net short position (ANSP) regime, charts each
series through time, and can place best-effort Yahoo Finance prices on a
separate chart with the same time axis.

> **Release status:** Short Tracker is published under the [MIT License](LICENSE).
> Windows releases are currently unsigned, so Windows may display an Unknown
> publisher or SmartScreen warning. Review the checksum and release notes before
> running a downloaded build. Maintainer checks are documented in the
> [publication checklist](PUBLICATION-CHECKLIST.md).

## Windows release: no Python required

The intended path for ordinary users is the Windows x64 ZIP attached to a
GitHub Release.

1. Download `Short-Tracker-v<VERSION>-windows-x64.zip` and its SHA-256 file.
2. Use **Extract all**. Do not run an EXE while it is still inside the ZIP.
3. Double-click **Short Tracker.exe**.
4. The single visible `Short Tracker.exe` starts its loopback-only service,
   waits for a health check, and displays the dashboard in a native desktop
   window powered by pywebview and Microsoft Edge WebView2. No separate browser
   is required.
5. Close the window with its **X** button to stop the embedded local service
   safely. If an FCA sync is active, the app waits for it to finish rather than
   corrupting an in-progress import.

While the app is open, it can check the FCA sources automatically every 6, 12,
or 24 hours; the default is 6 hours. This setting does not create a Windows
scheduled task or a resident background service. Closing the window stops both
the local service and its in-app scheduler.

The release is an unsigned PyInstaller `onedir` application. It does not use
UPX, request administrator rights, install a Windows service, edit the registry,
or configure auto-start. Windows may nevertheless show an **Unknown publisher**
or SmartScreen warning until the project has a trusted code-signing identity
and reputation. A SHA-256 checksum verifies bytes; it does not replace a code
signature.

### Local files and updates

Program files stay in the extracted folder and are treated as read-only. User
data is written under:

```text
%LOCALAPPDATA%\ShortTracker\data
```

That directory can contain the SQLite database, immutable FCA downloads,
market-price cache, reviewed ticker mappings, application settings, the local
WebView2 interface profile, runtime state, and diagnostic logs. It is never included in a source tree or release
archive. In contrast, a source checkout uses `<PROJECT>/data` by default. The
two modes do not silently share data.

Short Tracker checks public GitHub Release metadata from the canonical
[`ClydeKingsley/uk-short-tracker`](https://github.com/ClydeKingsley/uk-short-tracker)
repository. It never downloads or installs an application update automatically;
a validated notification can only take the user to the official Release page.
Successful checks are cached for 24 hours, and GitHub rate-limit responses are
honoured with a backoff period.

To update:

1. close the currently running Short Tracker window and wait for it to exit;
2. extract the new ZIP into a new folder;
3. start the new `Short Tracker.exe`;
4. remove the old program folder only after the new version works.

Replacing or deleting an extracted program folder does not delete Local AppData,
so upgrading the application does not remove the database, FCA archive,
settings, or caches. To remove all local Short Tracker data, close the app first
and then manually remove `%LOCALAPPDATA%\ShortTracker`.

## What the dashboard shows

- searchable FCA securities by company name, ISIN, and reviewed ticker mapping;
- the current ANSP ranking, preserving the FCA reportable-share/ISIN grain;
- legacy named-disclosure history and ANSP history as separate series;
- a regime boundary rather than a misleading continuous comparison;
- current and historical FCA source provenance;
- optional Yahoo Finance price history and latest observations;
- Chinese and English interfaces.

The disclosure regimes are not directly comparable. Legacy public data usually
captured named holders at or above 0.50%, whereas ANSP aggregates reportable
positions, generally from 0.20%, without publishing holder identity or count.
A jump around the July 2026 transition can therefore be a measurement change,
not evidence that economic short exposure changed by the same amount. See the
[methodology](docs/methodology.md) and
[data dictionary](docs/data-dictionary.md).

## Network and privacy boundary

Short Tracker:

- binds only to `127.0.0.1`;
- downloads public disclosure files from the FCA for initial, manual, or
  app-open automatic syncs;
- contacts Yahoo Finance only for optional symbol search and price data;
- when a canonical repository is configured, contacts `api.github.com` only to
  check public Release metadata; it does not download or install updates;
- stops its local service and automatic FCA scheduler when the desktop window
  closes, and creates no Windows scheduled task;
- has no telemetry;
- has no broker or trading-account connection;
- never asks for or stores passwords, PINs, verification codes, API keys, or
  brokerage credentials;
- cannot place, modify, or cancel an order.

See [PRIVACY.md](PRIVACY.md) before sharing logs or reporting an issue. Never
upload your entire data directory, SQLite database, raw FCA archive, Yahoo
cache, or unredacted logs to an issue.

## Running from source

Source use requires Python 3.11 or later.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m short_tracker serve --host 127.0.0.1 --port 8777 --open
```

Source mode intentionally stores data in `<PROJECT>/data` (normally `./data`
from the repository root), not in the frozen application's Local AppData
directory. That entire path is ignored by Git. To use another location, place
`--data-dir` before the command:

```powershell
.\.venv\Scripts\python.exe -m short_tracker --data-dir '<DATA_DIRECTORY>' sync
```

Useful commands:

```powershell
python -m unittest discover -s tests -v
python -m tools.audit_public_tree .
python -m short_tracker --data-dir '<DATA_DIRECTORY>' verify
```

Tests use synthetic fixtures and do not require or bundle a maintainer's FCA or
Yahoo data.

## Building the Windows release

Windows release builds are intentionally pinned to uv 0.11.29's managed
CPython 3.11.15 build, because native-runtime fingerprints and their reviewed
licence sources form part of the release attestation:

```powershell
uv python install 3.11.15
uv venv --python 3.11.15 .build-venv
uv pip install --python .\.build-venv\Scripts\python.exe --require-hashes -r .\requirements-build.lock
pwsh -NoLogo -NoProfile `
  -File .\packaging\Build-WindowsRelease.ps1
```

The normal build requires a real `LICENSE`, runs the unit tests and public-tree
audit, and builds a windowed `onedir` bundle with one visible
`Short Tracker.exe`. Its extracted-package smoke test covers the native WebView2
lifecycle, duplicate launch handling, static assets, safe window-close shutdown,
automatic-sync settings, a random port, and an isolated data directory. It also
checks that the program folder was not modified at runtime. The build reconciles
the actual PyInstaller module graph and bundled binary versions with complete
third-party terms under `LICENSES`, then writes a ZIP, release manifest, and
SHA-256 file. Missing or altered licence material fails the build and archive
verification.

The maintainer may use `-AllowMissingLicense` only for an internal,
non-redistributable development preview. That preview contains a prominent
notice and must never be attached to a public release.

Tag-triggered GitHub Actions create a **draft** release only after the licence,
version, tests, privacy audit, bundle smoke test, checksums, and GitHub artifact
attestation succeed. A maintainer still reviews and publishes the draft.

## Security and support

Read [SECURITY.md](SECURITY.md) for responsible vulnerability reporting and
[CONTRIBUTING.md](CONTRIBUTING.md) before sending a pull request. Data-source or
methodology disagreements are not security vulnerabilities and should use the
dedicated data-source issue form.

Short Tracker is research software, not a trading system. Public disclosures
are thresholded and may be delayed, incomplete, corrected, or measured under
different regimes. Yahoo endpoints are unofficial, may change without notice,
and are not an execution-grade market-data feed. Nothing in this project is
investment, legal, tax, or trading advice.

Official FCA starting point:
[Notification and disclosure of net short positions](https://www.fca.org.uk/markets/short-selling/notification-disclosure-net-short-positions).
