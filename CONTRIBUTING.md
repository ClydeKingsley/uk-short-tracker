# Contributing

Thank you for helping improve Short Tracker. The project favours reviewable,
source-backed changes and a strict boundary between public code and local data.

## Development setup

Use Python 3.11 through 3.14 for source development. The canonical Windows
release runtime is the exact uv-managed CPython 3.11.15 build recorded in
`packaging/windows-runtime-lock.json`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tools.audit_public_tree .
```

Tests must not depend on live FCA or Yahoo availability. Mock outbound requests
and use synthetic fixtures. Do not commit captured production responses, FCA
workbooks, databases, cache files, mappings, logs, or screenshots containing a
local path or personal information.

## Pull-request checklist

- keep the HTTP service bound to loopback and preserve origin/host checks;
- do not add broker login, credentials, order execution, or autonomous trading;
- update both Chinese and English UI text when user-facing wording changes;
- update methodology, data dictionary, and chart contract when a metric or
  visual interpretation changes;
- explain each new dependency and verify its licence;
- run all unit tests and the public-tree audit;
- for launcher, packaging, path, or dependency changes, run the extracted
  Windows bundle lifecycle smoke test;
- state any testing boundary honestly, especially SmartScreen, Defender, code
  signing, network access, and clean-VM coverage.

The maintainer retains final decisions on financial definitions and release
scope. Contributions are accepted under the project's [MIT License](LICENSE);
by submitting a contribution, you agree that it may be distributed under those
terms.
