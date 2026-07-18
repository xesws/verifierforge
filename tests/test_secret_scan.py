from __future__ import annotations

from pathlib import Path

from scripts.scan_secrets import scan_paths


def test_secret_scanner_reports_category_without_echoing_value(tmp_path: Path) -> None:
    secret = "".join(("postgresql", "://", "owner", ":", "fixture", "@", "db.test", "/db"))
    path = tmp_path / "config.txt"
    path.write_text(f"DATABASE={secret}\n", encoding="utf-8")

    findings = scan_paths([path])

    assert findings == [(path, 1, "credential-bearing database URL")]
    assert secret not in repr(findings)


def test_secret_scanner_ignores_normal_environment_variable_names(tmp_path: Path) -> None:
    path = tmp_path / "settings.py"
    path.write_text(
        'key_name = "VF_CRED_KEY"\nurl_name = "SUPABASE_DB_URL"\n',
        encoding="utf-8",
    )

    assert scan_paths([path]) == []
