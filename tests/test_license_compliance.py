from pathlib import Path

import pytest

from tools.license_compliance import (
    DistributionRecord,
    audit_distributions,
    select_license_text,
)


@pytest.fixture
def policy() -> dict:
    return {
        "approved_copyleft_packages": {
            "python-telegram-bot": {"license_family": "LGPL-3.0"},
            "frozendict": {"license_family": "LGPL-3.0"},
        },
        "required_license_files": ["GPL-3.0.txt", "LGPL-3.0.txt"],
    }


def test_approved_lgpl_packages_pass_with_notices_and_license_texts(
    tmp_path: Path, policy: dict
) -> None:
    notice = "\n".join(
        [
            "<!-- package: python-telegram-bot -->",
            "<!-- package: frozendict -->",
        ]
    )
    for filename in policy["required_license_files"]:
        (tmp_path / filename).write_text("license text", encoding="utf-8")

    errors = audit_distributions(
        [
            DistributionRecord(
                "python-telegram-bot", "22.7", "LGPL-3.0-only"
            ),
            DistributionRecord("frozendict", "2.4.7", "LGPL v3"),
            DistributionRecord("requests", "2.32.3", "Apache-2.0"),
        ],
        policy,
        notice,
        tmp_path,
    )

    assert errors == []


def test_unapproved_gpl_family_package_fails(tmp_path: Path, policy: dict) -> None:
    for filename in policy["required_license_files"]:
        (tmp_path / filename).write_text("license text", encoding="utf-8")

    errors = audit_distributions(
        [
            DistributionRecord(
                "python-telegram-bot", "22.7", "LGPL-3.0-only"
            ),
            DistributionRecord("frozendict", "2.4.7", "LGPL v3"),
            DistributionRecord("new-copyleft-package", "1.0", "GPL-3.0-only"),
        ],
        policy,
        "<!-- package: python-telegram-bot -->\n<!-- package: frozendict -->",
        tmp_path,
    )

    assert any("new-copyleft-package" in error for error in errors)


def test_missing_notice_or_license_file_fails(tmp_path: Path, policy: dict) -> None:
    errors = audit_distributions(
        [
            DistributionRecord(
                "python-telegram-bot", "22.7", "LGPL-3.0-only"
            ),
            DistributionRecord("frozendict", "2.4.7", "LGPL v3"),
        ],
        policy,
        "<!-- package: python-telegram-bot -->",
        tmp_path,
    )

    assert any("frozendict" in error for error in errors)
    assert any("GPL-3.0.txt" in error for error in errors)
    assert any("LGPL-3.0.txt" in error for error in errors)


def test_license_family_change_fails(tmp_path: Path, policy: dict) -> None:
    for filename in policy["required_license_files"]:
        (tmp_path / filename).write_text("license text", encoding="utf-8")

    errors = audit_distributions(
        [
            DistributionRecord("python-telegram-bot", "23.0", "GPL-3.0-only"),
            DistributionRecord("frozendict", "2.4.7", "LGPL v3"),
        ],
        policy,
        "<!-- package: python-telegram-bot -->\n<!-- package: frozendict -->",
        tmp_path,
    )

    assert any("python-telegram-bot" in error for error in errors)


def test_spdx_or_classifier_wins_over_bundled_license_body() -> None:
    bundled_text = "BSD 3-Clause License\nBundled component: GPL-3.0"

    assert (
        select_license_text("BSD-3-Clause", bundled_text, []) == "BSD-3-Clause"
    )
    assert (
        select_license_text(
            "",
            bundled_text,
            ["License :: OSI Approved :: BSD License"],
        )
        == "OSI Approved :: BSD License"
    )


def test_approved_package_may_be_absent_from_a_resolved_environment(
    tmp_path: Path, policy: dict
) -> None:
    for filename in policy["required_license_files"]:
        (tmp_path / filename).write_text("license text", encoding="utf-8")

    errors = audit_distributions(
        [
            DistributionRecord(
                "python-telegram-bot", "22.8", "LGPL-3.0-only"
            )
        ],
        policy,
        "<!-- package: python-telegram-bot -->\n<!-- package: frozendict -->",
        tmp_path,
    )

    assert errors == []
