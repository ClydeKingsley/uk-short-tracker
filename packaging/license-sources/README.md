# Supplemental licence sources

The Windows build collects licence files from the locked Python distributions
installed in its isolated build environment. A few redistributed binaries do
not carry their complete terms in those wheels, so the exact upstream texts
below are retained as reviewed build inputs.

| Directory | Authoritative source | Reviewed source evidence |
|---|---|---|
| `microsoft-webview2-1.0.3856.49` | `Microsoft.Web.WebView2` 1.0.3856.49 from NuGet | `bc0f76eb911b569838dc4aa8f8d325269b966bedb592863d26211aef3a099f1a` |
| `netstandard-library-2.0.3` | `NETStandard.Library` 2.0.3 from NuGet | `3eb87644f79bcffb3c0331dbdac3c7837265f2cdf58a7bfd93e431776f77c9ba` |
| `proxy-tools-0.1.0` | upstream `LICENSE.txt` at commit `db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6`, corresponding to the locked 0.1.0 source | source distribution `ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010` |
| `openssl-3.5.7` | `LICENSE.txt` from the official OpenSSL `openssl-3.5.7` tag | normalised file SHA-256 `ff420781e0005270cd1894a265e526dec4eb332f99df62a481b06672db202290` |
| `libffi-3.4.4` | `LICENSE` from the official libffi `v3.4.4` tag; CPython 3.11.15's Windows build configuration identifies libffi 3.4.4 | normalised file SHA-256 `62f7c24c5fb5935249293019030d9abf3b132d07c75242c1ee91035a12bd04df` |
| `sqlite-3.53.1` | copyright/public-domain documentation from SQLite Fossil tag `version-3.53.1` | normalised file SHA-256 `5fd96120d2597155cbbdff87c8b9554d3915f0fe85834a75032d1e4a08b6c2a4` |

The supplemental texts were taken without substantive edits; line endings and
the presence of a final newline may be normalised in this repository. The
release collector hash-checks every one of these inputs. It also refuses any
CPython DLL whose byte fingerprint differs
from `windows-runtime-lock.json`, any WebView2 DLL whose embedded version
differs from `1.0.3856.49`, and any unreviewed .NET reference assembly. A
dependency or runtime update therefore requires a fresh licence review. These
files are third-party terms, not the Short Tracker project licence.

The Windows package targets Windows 10/11 x64 and deliberately relies on the
operating system's Universal CRT and API-set contracts. Host copies named
`ucrtbase.dll` or `api-ms-win-*.dll` are excluded from PyInstaller output. Both
the licence collector and final archive verifier reject them if a build host
tries to add them, preventing runner-specific native binaries from bypassing
the locked runtime review.
