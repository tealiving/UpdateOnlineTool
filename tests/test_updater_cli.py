"""标准 updater 可执行体 CLI 测试。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import update_online_tool.runtime as runtime
from update_online_tool.signature import load_hmac_key, sign_manifest_payload
from update_online_tool.updater_cli import main


def test_updater_cli_install_updates_current_json(tmp_path: Path, capsys) -> None:
    """验证 uot-updater install 可安装并切换版本。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest_path = _write_manifest(tmp_path / "latest.json", package_path, version="1.1.0")

    exit_code = main(
        [
            "install",
            "--install-root",
            str(install_root),
            "--package",
            str(package_path),
            "--manifest",
            str(manifest_path),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert output["version"] == "1.1.0"
    assert current_payload["version"] == "1.1.0"
    assert current_payload["previous_version"] == "1.0.0"


def test_updater_cli_install_dry_run_does_not_write(tmp_path: Path, capsys) -> None:
    """验证 uot-updater install --dry-run 不写安装状态。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest_path = _write_manifest(tmp_path / "latest.json", package_path, version="1.1.0")

    exit_code = main(
        [
            "install",
            "--install-root",
            str(install_root),
            "--package",
            str(package_path),
            "--manifest",
            str(manifest_path),
            "--dry-run",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert output["message"] == "dry-run ok"
    assert current_payload["version"] == "1.0.0"
    assert not (install_root / "releases" / "1.1.0").exists()


def test_updater_cli_apply_pending_update(tmp_path: Path, capsys) -> None:
    """验证 uot-updater apply 可应用 pending-update.json。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest_payload = _manifest_payload(package_path, version="1.1.0")
    pending_path = tmp_path / "pending-update.json"
    pending_path.write_text(
        json.dumps(
            {
                "package_path": str(package_path),
                "install_root": str(install_root),
                "manifest": manifest_payload,
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["apply", "--pending", str(pending_path)])

    output = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert output["version"] == "1.1.0"
    assert current_payload["version"] == "1.1.0"


def test_updater_cli_apply_rejects_tampered_pending_signature(tmp_path: Path) -> None:
    """验证 uot-updater apply --signature-key 拒绝 pending 中被篡改的 manifest。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    key_path = tmp_path / "signing.key"
    key_path.write_text("release-secret\n", encoding="utf-8")
    manifest_payload = sign_manifest_payload(
        _manifest_payload(package_path, version="1.1.0"),
        key=load_hmac_key(key_path),
        key_id="release",
    )
    manifest_payload["version"] = "9.9.9"
    pending_path = tmp_path / "pending-update.json"
    pending_path.write_text(
        json.dumps(
            {
                "package_path": str(package_path),
                "install_root": str(install_root),
                "manifest": manifest_payload,
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["apply", "--pending", str(pending_path), "--signature-key", str(key_path)])

    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert current_payload["version"] == "1.0.0"


def test_updater_cli_install_can_restart_current_release(tmp_path: Path, capsys, monkeypatch) -> None:
    """验证 uot-updater install --restart 会输出重启 PID。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest_path = _write_manifest(tmp_path / "latest.json", package_path, version="1.1.0")

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(runtime, "launch_current", lambda *, install_root: FakeProcess())

    exit_code = main(
        [
            "install",
            "--install-root",
            str(install_root),
            "--package",
            str(package_path),
            "--manifest",
            str(manifest_path),
            "--restart",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["restarted_pid"] == 4242


def test_updater_cli_install_rejects_tampered_signature(tmp_path: Path) -> None:
    """验证 uot-updater install --signature-key 拒绝被篡改的 manifest。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    key_path = tmp_path / "signing.key"
    key_path.write_text("release-secret\n", encoding="utf-8")
    manifest_payload = sign_manifest_payload(
        _manifest_payload(package_path, version="1.1.0"),
        key=load_hmac_key(key_path),
        key_id="release",
    )
    manifest_payload["version"] = "9.9.9"
    manifest_path = tmp_path / "latest.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    exit_code = main(
        [
            "install",
            "--install-root",
            str(install_root),
            "--package",
            str(package_path),
            "--manifest",
            str(manifest_path),
            "--signature-key",
            str(key_path),
        ]
    )

    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert current_payload["version"] == "1.0.0"


def test_pyproject_exposes_updater_script() -> None:
    """验证 pyproject 暴露 uot-updater console script。"""
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'uot-updater = "update_online_tool.updater_cli:main"' in pyproject


def _write_install_root(tmp_path: Path, *, current_version: str, entry_name: str) -> Path:
    """写入测试安装根。"""
    install_root = tmp_path / "install"
    release_dir = install_root / "releases" / current_version
    release_dir.mkdir(parents=True)
    (release_dir / entry_name).write_text("current", encoding="utf-8")
    (install_root / "current.json").write_text(
        json.dumps(
            {
                "app_id": "my-tool",
                "version": current_version,
                "release_dir": f"releases/{current_version}",
                "executable": entry_name,
                "entry": {
                    "kind": "executable",
                    "path": entry_name,
                    "platform": "windows",
                },
            }
        ),
        encoding="utf-8",
    )
    return install_root


def _write_package(path: Path, files: dict[str, str]) -> Path:
    """写入测试 zip 包。"""
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _write_manifest(path: Path, package_path: Path, *, version: str) -> Path:
    """写入测试 manifest。"""
    path.write_text(json.dumps(_manifest_payload(package_path, version=version)), encoding="utf-8")
    return path


def _manifest_payload(package_path: Path, *, version: str) -> dict[str, object]:
    """构建测试 manifest payload。"""
    return {
        "schema_version": 2,
        "app_id": "my-tool",
        "channel": "stable",
        "version": version,
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-18T00:00:00+00:00",
        "notes": f"v{version}",
        "platform": "windows",
        "package": {
            "url": "my-tool/package.zip",
            "size": package_path.stat().st_size,
            "sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
        },
    }
