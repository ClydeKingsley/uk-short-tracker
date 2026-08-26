# Privacy and network behaviour

Short Tracker is designed as a local research application. It has no analytics,
advertising, telemetry, account system, broker integration, or credential
storage.

## Outbound connections

The application may make these outbound HTTPS requests:

| Destination | Trigger | Purpose |
|---|---|---|
| `www.fca.org.uk` | initial, manual, or app-open automatic FCA sync | download public disclosure and reportable-share files |
| `query1.finance.yahoo.com` | optional price request | retrieve chart/latest market-price observations |
| `query2.finance.yahoo.com` | optional symbol search | suggest London ticker candidates for human review |
| `api.github.com` | application-update metadata check, only after a canonical repository is configured | read public Release version and asset metadata; no asset download or installation |

Yahoo endpoints are unofficial public web endpoints and may change, rate-limit,
or return incomplete data. No brokerage order or account request is made.

The GitHub update checker sends no GitHub token and returns only strictly
validated version, Release-page, and Windows asset metadata. It does not
download an application ZIP, execute an installer, or replace program files.
The canonical GitHub owner and repository are intentionally unconfigured in the
current release candidate, so this check is disabled and no GitHub request is
made. If the user later clicks a validated Release-page link, the resulting page
navigation is an explicit user action outside the metadata check.

## Automatic FCA sync lifecycle

Automatic FCA sync runs only while the Short Tracker desktop window is open.
The user can select a 6-, 12-, or 24-hour interval; the default is 6 hours.
Closing the window safely stops the embedded loopback service and the in-app
scheduler. Short Tracker does not create a Windows scheduled task, install a
Windows service, configure auto-start, or remain resident after the app closes.

## Local storage

The frozen Windows EXE stores writable state below
`%LOCALAPPDATA%\ShortTracker\data`. A source checkout instead defaults to
`<PROJECT>/data` (normally `./data` from the repository root) unless
`--data-dir` or `SHORT_TRACKER_DATA_DIR` explicitly selects another location.
The source and frozen defaults are separate.

Local state may include:

- `short_tracker.sqlite`, including imported public FCA disclosures and source
  provenance;
- immutable copies of public FCA files under `raw/`;
- Yahoo price responses under `cache/`;
- reviewed and automatically suggested ticker mappings under `settings/`;
- automatic-sync preferences and, once configured, update-check timestamps,
  cache validators, public Release metadata, and rate-limit backoff state under
  `settings/`;
- the embedded WebView2 profile and local interface storage under
  `webview-profile/` (for example the language choice and dismissed update
  notice);
- launcher state, stop requests, and logs under `runtime/`.

Update checks do not store credentials. Replacing or deleting an extracted
program folder does not delete this writable state, so a normal application
upgrade preserves the database, raw FCA archive, settings, and caches.

Diagnostic logs can contain application paths, dates, error messages, queried
company names, tickers, or ISINs. They are not transmitted by the application.

## What is not collected

Short Tracker does not ask for or store:

- a brokerage username or password;
- a PIN, verification code, recovery phrase, or private key;
- a trading API key or secret;
- bank, card, or payment credentials;
- portfolio holdings or order history from a broker;
- an advertising or telemetry identifier.

## Deletion and issue reports

Close the Short Tracker desktop window before deleting data. Removing the
extracted application folder leaves Local AppData intact. To remove all Short
Tracker local state, manually delete `%LOCALAPPDATA%\ShortTracker` after the app
has closed.

Before filing an issue, reproduce the problem if possible with synthetic or
public data. Never upload the whole data directory, SQLite/WAL/SHM files, raw FCA
archive, Yahoo cache, ticker mapping file, or an unredacted log. Include only the
minimum relevant excerpt and remove usernames, local paths, tokens, and unrelated
queries.
