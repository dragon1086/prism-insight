#!/usr/bin/env python3
"""Fail builds when unreviewed copyleft packages enter the environment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DistributionRecord:
    name: str
    version: str
    license_text: str


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_family(license_text: str) -> str | None:
    value = license_text.lower()
    if "affero" in value or "agpl" in value:
        return "AGPL-3.0" if "3" in value else "AGPL"
    if "lesser general public license" in value or "lgpl" in value:
        return "LGPL-3.0" if "3" in value else "LGPL"
    if "general public license" in value or re.search(r"\bgpl\b", value):
        return "GPL-3.0" if "3" in value else "GPL"
    return None


def select_license_text(
    license_expression: str,
    license_value: str,
    classifiers: Iterable[str],
) -> str:
    if license_expression.strip():
        return license_expression.strip()
    license_classifiers = [
        classifier.removeprefix("License :: ")
        for classifier in classifiers
        if classifier.startswith("License :: ")
    ]
    if license_classifiers:
        return "; ".join(license_classifiers)
    first_line = license_value.strip().splitlines()[0] if license_value.strip() else ""
    return first_line


def installed_distributions() -> list[DistributionRecord]:
    records: list[DistributionRecord] = []
    for distribution in metadata.distributions():
        package_metadata = distribution.metadata
        name = package_metadata.get("Name")
        if not name:
            continue
        selected_license = select_license_text(
            package_metadata.get("License-Expression", ""),
            package_metadata.get("License", ""),
            package_metadata.get_all("Classifier", []),
        )
        records.append(
            DistributionRecord(
                name=normalize_name(name),
                version=distribution.version,
                license_text=selected_license,
            )
        )
    return records


def audit_distributions(
    distributions: Iterable[DistributionRecord],
    policy: dict,
    notice_text: str,
    license_directory: Path,
) -> list[str]:
    approved = {
        normalize_name(name): settings
        for name, settings in policy["approved_copyleft_packages"].items()
    }
    installed = {normalize_name(item.name): item for item in distributions}
    errors: list[str] = []

    for name, item in installed.items():
        family = license_family(item.license_text)
        if family is None:
            continue
        if name not in approved:
            errors.append(
                f"Unreviewed copyleft package: {name} {item.version} ({family})"
            )
            continue
        expected_family = approved[name]["license_family"]
        if family != expected_family:
            errors.append(
                f"License changed for {name} {item.version}: "
                f"expected {expected_family}, found {family}"
            )

    for name in approved:
        if f"<!-- package: {name} -->" not in notice_text:
            errors.append(f"THIRD_PARTY_NOTICES.md is missing {name}")

    for filename in policy["required_license_files"]:
        path = license_directory / filename
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"Missing third-party license text: {filename}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT, help="repository root"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    policy = json.loads(
        (root / "licenses" / "third-party-policy.json").read_text(encoding="utf-8")
    )
    notice_text = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    errors = audit_distributions(
        installed_distributions(),
        policy,
        notice_text,
        root / "licenses" / "third-party",
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Third-party copyleft license check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
