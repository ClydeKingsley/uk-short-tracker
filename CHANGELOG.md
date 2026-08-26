# Changelog

All notable changes are documented here.

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

This is the first stable public release under the MIT License. Its Windows
executable is intentionally unsigned; users should review the published
SHA-256 checksum and release notes before running it.

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
