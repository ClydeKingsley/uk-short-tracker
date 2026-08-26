# Public GitHub publication checklist

Use this checklist when preparing a public Short Tracker release. Checked
maintainer decisions record the standing policy selected for `v0.2.0`; the
remaining operational items must be re-verified for each applicable release.

## Maintainer decisions

- [x] Use the MIT License and include its full text as `LICENSE`.
- [x] Update `pyproject.toml` with the MIT SPDX licence expression and
  `license-files = ["LICENSE"]`.
- [x] Remove `LICENSE-DECISION-REQUIRED.md` and the release-candidate warning in
  both READMEs.
- [x] Use `ClydeKingsley/uk-short-tracker` as the sole canonical public Release
  source and configure the metadata-only update checker to that exact repository.
- [x] Publish `v0.2.0` as a stable release.
- [x] Accept an unsigned `v0.2.0` and disclose the resulting Unknown publisher
  and SmartScreen risk in the READMEs and Release notes.

## Local verification

- [ ] `python -m tools.verify_publication_config --root . --github-repository
  '<OWNER>/<REPOSITORY>'`
- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m tools.audit_public_tree .`
- [ ] use uv 0.11.29 to create a fresh managed Python 3.11.15 x64 environment,
  then install `requirements-build.lock` with `--require-hashes`;
- [ ] run `packaging/Build-WindowsRelease.ps1` without
  `-AllowMissingLicense`;
- [ ] run `packaging/Test-NoPythonRuntime.ps1` against the exact final ZIP;
- [ ] run `packaging/Test-LiveFcaSync.ps1` against the exact final ZIP from a
  disposable directory, and confirm six source archives plus four active
  datasets pass frozen-runtime verification;
- [ ] verify the ZIP hash, internal manifest, PE version/icon/GUI subsystem, and
  Authenticode status;
- [ ] verify `LICENSES/manifest.json` maps every actual PyInstaller module and
  locked native runtime to complete, hash-bound terms, including Python,
  OpenSSL, libffi, SQLite, WebView2, .NET Standard and the PyInstaller
  bootloader; review any dependency or binary drift before updating the lock;
- [ ] confirm the extracted package exposes one user-facing
  `Short Tracker.exe`, opens one native pywebview/WebView2 window, and opens no
  separate browser or console window;
- [ ] verify clicking the window **X** waits for an active FCA import, then
  stops the owned loopback service and releases its port without killing an
  unrelated process;
- [ ] verify FCA automatic-sync choices are exactly 6, 12, and 24 hours, default
  to 6 hours, run only while the app is open, and create no Windows scheduled
  task, service, auto-start entry, or resident process;
- [ ] verify source mode defaults to `<PROJECT>/data`, while the frozen EXE
  defaults to `%LOCALAPPDATA%\ShortTracker\data`; upgrading or replacing the
  program folder must preserve the frozen data directory;
- [ ] with repository coordinates empty, verify update status is disabled/not
  configured and makes no request; with test coordinates and mocked network,
  verify 24-hour caching, stable/preview channels, ETag/Last-Modified, response
  limits, and 403/429 backoff;
- [ ] confirm update handling checks metadata only, opens at most a validated
  official Release page on explicit user action, and contains no automatic
  asset download, installer execution, or program-file replacement path;
- [ ] confirm no `data/`, database, FCA/Yahoo files, mappings, runtime state,
  logs, credentials, personal paths, build paths, virtual environment, or Git
  metadata is present.

## GitHub repository setup

- [ ] Initialize Git only in this `short-tracker` directory, never in its parent
  project directory.
- [ ] Review `git status --short` and `git ls-files` before the first commit.
- [ ] Configure a repository-local public Git identity; do not inherit a private
  email by accident.
- [ ] Create the remote only after the tracked-file audit is clean.
- [ ] Enable branch protection, read-only default Actions permissions,
  Dependabot, dependency graph, secret scanning/push protection, CodeQL, and
  Private Vulnerability Reporting.
- [ ] Require CI, public-tree audit, and Windows bundle smoke checks on `main`.
- [ ] Protect `v*` tags and review action SHA updates rather than auto-merging
  them.

## Release acceptance

- [ ] Let the tag workflow create a draft Release; do not publish directly.
- [ ] Review generated notes, ZIP, checksum, manifest, and artifact attestation.
- [ ] Download the draft asset through a browser onto a clean Windows 10 and
  Windows 11 x64 VM with Defender and SmartScreen enabled.
- [ ] Test full extraction, first launch, first FCA sync, optional Yahoo price,
  duplicate launch, 6/12/24-hour automatic-sync settings, window-close during
  and after sync, metadata-only update checks, upgrade from another folder,
  preserved Local AppData, and uninstall instructions without system Python
  installed.
- [ ] On clean Windows 10/11 test both WebView2-present and WebView2-missing
  outcomes; a missing runtime must produce a clear error and must not silently
  fall back to an obsolete browser engine.
- [ ] Record any SmartScreen or antivirus warning honestly in the Release notes.
- [ ] Publish only after the exact downloaded asset passes acceptance testing.

Any `v0.2.0-preview` ZIP is an internal technical preview and must not be
attached to the public Release. Only the stable, licensed artifact produced by
the reviewed tag workflow is eligible for publication.
