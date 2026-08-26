"""Fail closed unless the tree is configured for a public GitHub release."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import sys
import tomllib


_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SPDX_EXPRESSION_RE = re.compile(r"^[A-Za-z0-9.+()\- ]+$")
_PLACEHOLDER_MARKERS = (
    "choose a project licence",
    "license decision required",
    "licence decision required",
    "replace with licence",
    "replace with license",
)


def _repository_constants(path: Path) -> tuple[str, str]:
    """Read literal repository coordinates without importing application code."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    wanted = {"DEFAULT_GITHUB_OWNER", "DEFAULT_GITHUB_REPOSITORY"}
    for node in tree.body:
        name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                value = node.value
        if name in wanted and isinstance(value, ast.Constant) and isinstance(value.value, str):
            values[name] = value.value.strip()
    return values.get("DEFAULT_GITHUB_OWNER", ""), values.get(
        "DEFAULT_GITHUB_REPOSITORY", ""
    )


def audit_publication_config(
    root: Path, *, expected_repository: str | None = None
) -> list[str]:
    """Return publication blockers; an empty result is release-ready."""

    root = root.resolve()
    failures: list[str] = []

    license_path = root / "LICENSE"
    if not license_path.is_file():
        failures.append("LICENSE is missing")
    else:
        try:
            license_text = license_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            failures.append(f"LICENSE is not readable UTF-8 text: {exc}")
        else:
            if len(license_text) < 200:
                failures.append("LICENSE is unexpectedly short")
            folded = license_text.casefold()
            if any(marker in folded for marker in _PLACEHOLDER_MARKERS):
                failures.append("LICENSE still contains a decision placeholder")

    if (root / "LICENSE-DECISION-REQUIRED.md").exists():
        failures.append("LICENSE-DECISION-REQUIRED.md must be removed")

    pyproject_path = root / "pyproject.toml"
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        failures.append(f"pyproject.toml cannot be parsed: {exc}")
    else:
        project = pyproject.get("project")
        if not isinstance(project, dict):
            failures.append("pyproject.toml has no [project] table")
        else:
            licence = project.get("license")
            if (
                not isinstance(licence, str)
                or not licence.strip()
                or not _SPDX_EXPRESSION_RE.fullmatch(licence.strip())
            ):
                failures.append("[project].license must be a non-empty SPDX expression")
            license_files = project.get("license-files")
            if not isinstance(license_files, list) or "LICENSE" not in license_files:
                failures.append('[project].license-files must include "LICENSE"')

    update_path = root / "short_tracker" / "update.py"
    try:
        owner, repository = _repository_constants(update_path)
    except (OSError, UnicodeError, SyntaxError) as exc:
        failures.append(f"GitHub update configuration cannot be read: {exc}")
        owner = repository = ""
    if not _SLUG_RE.fullmatch(owner):
        failures.append("DEFAULT_GITHUB_OWNER is empty or invalid")
    if not _SLUG_RE.fullmatch(repository):
        failures.append("DEFAULT_GITHUB_REPOSITORY is empty or invalid")

    if expected_repository is not None:
        expected = expected_repository.strip()
        parts = expected.split("/")
        if len(parts) != 2 or not all(_SLUG_RE.fullmatch(part) for part in parts):
            failures.append("--github-repository must be an owner/repository slug")
        elif owner and repository and f"{owner}/{repository}".casefold() != expected.casefold():
            failures.append(
                "configured update repository "
                f"{owner}/{repository} does not match workflow repository {expected}"
            )

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--github-repository",
        help="expected GitHub Actions repository in owner/repository form",
    )
    args = parser.parse_args(argv)
    failures = audit_publication_config(
        args.root,
        expected_repository=args.github_repository,
    )
    if failures:
        print("Public-release configuration failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Public-release configuration passed: {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
