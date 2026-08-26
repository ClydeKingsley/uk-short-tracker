# Third-party notices

The Windows distribution is expected to bundle the following runtime
components. The final release manifest and PyInstaller analysis must be checked
against the actual bundle before publication.

| Component | Expected version | Licence | Upstream |
|---|---:|---|---|
| Python and its documented bundled notices | 3.11.15, locked Astral uv-managed build `20260623` | Python Software Foundation License Version 2 and bundled notices | <https://www.python.org/> |
| OpenSSL runtime libraries bundled by CPython | 3.5.7 | Apache-2.0 | <https://www.openssl.org/> |
| libffi runtime library bundled by CPython | 3.4.4 / ABI 8, locked binary fingerprint | MIT | <https://github.com/libffi/libffi> |
| SQLite runtime library bundled by CPython | 3.53.1 | public-domain dedication (SQLite Blessing) | <https://sqlite.org/> |
| openpyxl | 3.1.5 | MIT | <https://openpyxl.readthedocs.io/> |
| et_xmlfile | 2.0.0 | MIT | <https://foss.heptapod.net/openpyxl/et_xmlfile> |
| pywebview | 6.2.1 | BSD-3-Clause | <https://pywebview.flowrl.com/> |
| pythonnet | 3.1.0 | MIT | <https://pythonnet.github.io/> |
| clr-loader | 0.3.1 | MIT | <https://github.com/pythonnet/clr-loader> |
| cffi | 2.1.1 | MIT-0 | <https://cffi.readthedocs.io/> |
| pycparser | 3.0 | BSD-3-Clause | <https://github.com/eliben/pycparser> |
| Bottle | 0.13.4 | MIT | <https://bottlepy.org/> |
| proxy-tools | 0.1.0 | upstream BSD-form text; PyPI/sdist package metadata says MIT | <https://github.com/jtushman/proxy_tools> |
| typing_extensions | 4.16.0 | PSF-2.0 | <https://github.com/python/typing_extensions> |
| setuptools | 84.0.0 | MIT | <https://github.com/pypa/setuptools> |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | <https://github.com/pypa/packaging> |
| .NET Standard reference assemblies | `NETStandard.Library` 2.0.3 terms; bundled by pythonnet | MIT and third-party notices | <https://www.nuget.org/packages/NETStandard.Library/2.0.3> |
| Microsoft Edge WebView2 SDK loader and interop assemblies | 1.0.3856.49, bundled by pywebview | Microsoft redistribution terms and notices | <https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49> |
| PyInstaller bootloader | 6.22.2 | GPL-2.0-or-later with the PyInstaller bootloader exception | <https://pyinstaller.org/> |

Pillow is used only to generate the application icon during a build and is not
expected to be imported into the runtime bundle. The additional entries above
are runtime dependencies pulled in by pywebview/pythonnet; some pure-Python
modules are stored inside PyInstaller's module archive rather than appearing as
top-level `_internal` directories. PyInstaller build-time modules that are not
collected into the application remain build dependencies rather than bundled
application libraries.

The `proxy-tools` 0.1.0 PyPI/sdist package metadata identifies its licence as
MIT, but the upstream repository's `LICENSE.txt` at commit
`db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6` contains a BSD-form grant with an
unreplaced placeholder and a duplicated trailing fragment. The release
reproduces that upstream text without substantive modification; the local
reviewed copy normalises only its final newline. The project does not silently
rewrite a third party's terms. This discrepancy is disclosed for review and is
not represented as a clean SPDX conclusion.

pywebview hosts the dashboard in the single visible Short Tracker desktop
window. On Windows it uses Microsoft Edge WebView2; the WebView2 Runtime is a
Microsoft system runtime rather than a second Short Tracker application window.
The final bundle audit must distinguish the bundled WebView2 loader from the
machine-installed WebView2 Runtime and preserve any notices required for each
bundled file.

Every Windows release contains a `LICENSES` directory. Its `manifest.json`
maps the actual PyInstaller module graph and redistributed binary paths to the
complete texts copied from the locked distributions or reviewed supplemental
sources, with byte lengths and SHA-256 hashes. Release verification fails if a
required component, text, hash, WebView2 version, or .NET reference-assembly
mapping is absent.

The Windows build environment and resulting CPython runtime DLLs are pinned in
`packaging/windows-runtime-lock.json`. The collector verifies Python 3.11.15's
build marker, complete Python licence, and exact SHA-256/size fingerprints for
`python311.dll`, the Visual C++ runtime, OpenSSL, libffi and SQLite before it
accepts their corresponding terms. The release currently contains no Tcl/Tk
data directories; if a future PyInstaller analysis adds new native runtime
files, the lock and this review must be updated rather than silently accepting
them.

FCA files and Yahoo responses are runtime data sources, not redistributed
release assets. When a canonical public repository is configured, Short Tracker
may also query GitHub's public Releases API for version and asset metadata. The
current owner/repository configuration is empty, so that check is disabled.
The update checker does not download or install Release assets.

Short Tracker is not affiliated with or endorsed by the FCA, Yahoo, GitHub, or
Microsoft. FCA public information may be subject to its own terms and statutory
context; Yahoo market data is subject to Yahoo's terms and limitations; GitHub
API and Microsoft runtime use remain subject to their respective terms.

This notice does not select or replace the Short Tracker project licence. The
generated dependency and licence inventory must be reviewed with every release.
