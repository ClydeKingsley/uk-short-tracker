# Security policy

## Supported versions

Only the most recent published GitHub Release is supported with security fixes.
Source snapshots and older releases may be used for diagnosis but should not be
assumed to receive patches.

## Reporting a vulnerability

Do not disclose a suspected vulnerability in a public issue. Use GitHub Private
Vulnerability Reporting for this repository. If that option is not available,
do not post exploit details; open a minimal public issue asking the maintainer to
enable a private reporting channel.

Please include the affected version, Windows/Python environment, concise impact,
reproduction conditions, and a minimal proof of concept. Do not attach real
databases, credentials, complete data directories, unredacted logs, or private
financial information.

Security-relevant areas include:

- exposure beyond the loopback interface;
- origin/host validation and cross-site requests;
- static-file path traversal;
- launcher process identity and safe shutdown;
- release or dependency supply-chain integrity;
- handling of writable paths and untrusted local files.

A disagreement about an FCA value, disclosure regime, Yahoo price, company
mapping, or chart interpretation is normally a data or methodology issue rather
than a security vulnerability. Use the data-source issue form for those cases.

The project cannot promise a response deadline before a public maintainer
identity and support capacity are established. Reports will be assessed in good
faith, and coordinated disclosure is preferred.
