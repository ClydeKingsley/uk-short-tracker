# Changelog

All notable changes are documented here.

## [Unreleased]

## [0.3.2] - 2026-10-23

### Fixed

- Restored FCA synchronisation after the current ANSP CSV renamed its position
  date column to "Position date (of latest position date notified)". Both the
  original and revised headings are accepted without changing date semantics.
- Validate required CSV columns before import and report the source, row and
  field for blank required values. A failed import still preserves the last good data.
- Stop polling immediately when a sync fails, show its error, and only report
  completion after success. Background failures no longer show a success toast.

## [0.3.1] - 2026-09-05

### Changed

- Refined the research workspace with a dark translucent sticky toolbar, layered
  light surfaces, inset highlights, soft shadows and clearer numeric emphasis.
- Added staggered panel and table entrances, sliding range and language indicators,
  contextual loading feedback, dialog entrances and pointer-responsive lighting.
- Motion follows semantic changes, coalesces rapid updates and leaves displayed
  values and chart geometry intact. Background checks do not replay unchanged data.

### Added

- A local motion control that follows the system preference by default and lets
  users explicitly enable full motion or reduce it. The choice persists locally.
- Five ANSP distribution bands with actual security counts and shares of the
  loaded snapshot, independent of filtering and pagination.

## [0.3.0] - 2026-09-05

### Changed

- Redesigned the desktop research workspace with compact layouts, clearer system
  typography, aligned numeric values, denser rankings and responsive panels.
- Unified chart colours with the documented contract: blue ANSP, dashed amber
  legacy positions and charcoal prices; enlarged Canvas labels and axis spacing.
- Added restrained interaction feedback that respects reduced-motion preferences.

### Added

- Current-snapshot coverage, highest ANSP, median and securities at or above 2%,
  calculated from all loaded records with explicit scope independent of filters.
- Five recent securities stored on the device, with quick reopen and clear actions.
- Chart range summaries showing visible dates, effective short-position nodes and
  price observation days without treating missing data as zero.
- Ctrl+K and slash shortcuts to focus security search, in both interface languages.

### Fixed

- Securities with unchanged short positions no longer throw an empty CSS-class
  error while updating their change indicator.

## [0.2.2] - 2026-08-26

### Fixed

- Windows downloads now configure pythonnet in an application-specific .NET
  AppDomain that permits the reviewed managed runtime files to load when
  Explorer has propagated Mark of the Web from the GitHub ZIP. This fixes the
  clean-PC `Failed to resolve Python.Runtime.Loader.Initialize` startup error
  without modifying or unblocking the extracted program files;
- Windows release builds now reproduce the downloaded-ZIP security-zone marker
  and initialize the real pythonnet/Edge WebView2 backend before an archive can
  pass, closing the gap in the earlier no-Python service-only smoke test;
- ANSP charts now link each published aggregate to its actual effective state
  interval instead of treating the constituent `position_date` as every
  transition date. The initial 13 July publication is shown from its 9 July
  position scope, later values begin when the previous value became historical,
  and a missing current row ends in a gap rather than being extended or filled
  with zero;
- draft Release checksum verification now normalizes the Windows packager's
  CRLF line ending before comparing fields on the Linux metadata runner.

## [0.2.1] - 2026-08-26

### Fixed

- Windows packages now exclude host-specific Universal CRT/API-set DLL copies
  and fail verification if a build runner reintroduces them, keeping the
  reviewed native-runtime inventory deterministic across build machines;
- the tag workflow now checks out release metadata before creating a draft,
  revalidates the exact three assets and their checksum/manifest, and retains
  the attested workflow artifact for 30 days;
- the build-only Pillow dependency is updated to 12.3.0 to incorporate its
  current security fixes before generating the bundled application icon.

This is the first downloadable stable public release. The earlier `v0.2.0` tag
is retained for provenance, but its runner-specific build was rejected during
independent post-build review and was never published as a GitHub Release.

## [0.2.0] - 2026-08-26

### Added

- a single native Windows desktop window powered by pywebview and Edge WebView2;
- automatic FCA refresh while the application is open, configurable to 6, 12,
  or 24 hours;
- persistent refresh settings, cross-process sync exclusion, retry backoff, and
  safe draining when the window closes;
- a metadata-only GitHub Release checker that never downloads or installs an
  update automatically;
- a fail-closed publication gate that requires the selected licence metadata
  and exact GitHub update repository to agree before any stable build;
- complete, hash-bound third-party licence collection reconciled against the
  actual PyInstaller module graph, WebView2 version, and .NET assemblies;
- bilingual settings, freshness, scheduling, and update-status controls.

### Changed

- closing the desktop window now owns the complete local-service lifecycle, so
  a separate Stop executable is no longer part of the Windows package;
- frozen builds continue to keep user data under
  `%LOCALAPPDATA%\ShortTracker\data`, independently of the extracted program.

This was the first stable public-release candidate under the MIT License. Its
tagged CI build was not published after post-build review identified
runner-specific native files outside the reviewed runtime inventory.

## [0.1.0] - 2026-08-24

### Added

- searchable UK public short-position dashboard;
- current ANSP ranking;
- separate legacy named-disclosure and ANSP history series;
- synchronized short-position and market-price charts;
- Chinese and English interface;
- safe loopback-only background launcher and stop protocol;
- Windows x64 `onedir` release pipeline with privacy and lifecycle checks.
- Hash-bound complete third-party terms and a pinned CPython/OpenSSL/libffi/
  SQLite/WebView2/.NET runtime inventory for Windows distributions.

This version remains a release candidate until the maintainer selects a project
licence and publishes a reviewed GitHub Release.
