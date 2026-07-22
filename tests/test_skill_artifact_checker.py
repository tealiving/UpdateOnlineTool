"""UOT Skill 制品检查器测试。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


CHECKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "uot-nas-online-update"
    / "scripts"
    / "check_uot_artifacts.py"
)


def _load_checker():  # noqa: ANN202
    """加载 Skill 制品检查脚本。

    :return: 检查脚本模块。
    """

    spec = importlib.util.spec_from_file_location(
        "uot_skill_artifact_checker", CHECKER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_valid_legacy_install(
    install_dir: Path, *, current_payload: object | None = None
) -> Path:
    """创建最小 legacy 安装根。

    :param install_dir: 安装根。
    :param current_payload: 可选 current.json 根数据。
    :return: updater 入口。
    """

    release_dir = install_dir / "releases" / "1.2.8"
    release_dir.mkdir(parents=True)
    (release_dir / "SmartIngest.exe").write_text("app", encoding="utf-8")
    updater = install_dir / "updater" / "SmartIngestUpdater.exe"
    updater.parent.mkdir(parents=True)
    updater.write_text("updater", encoding="utf-8")
    payload = (
        current_payload
        if current_payload is not None
        else {
            "version": "1.2.8",
            "executable": "SmartIngest.exe",
        }
    )
    (install_dir / "current.json").write_text(json.dumps(payload), encoding="utf-8")
    return updater


def _legacy_main_args(install_dir: Path, *extra: str) -> list[str]:
    """构造 legacy 检查器命令行。

    :param install_dir: 安装根。
    :param extra: 追加参数。
    :return: sys.argv 数据。
    """

    return [
        str(CHECKER_PATH),
        "--install-dir",
        str(install_dir),
        "--version",
        "1.2.8",
        "--platform",
        "windows",
        "--entry-path",
        "SmartIngest.exe",
        "--mode",
        "legacy",
        *extra,
    ]


def test_resolve_updater_entry_supports_flat_and_nested_layouts(tmp_path: Path) -> None:
    """检查器同时支持 onefile 与 onedir updater。

    :param tmp_path: pytest 临时目录。
    :return: None。
    """

    checker = _load_checker()
    flat = tmp_path / "flat" / "updater" / "Updater"
    flat.parent.mkdir(parents=True)
    flat.write_text("flat", encoding="utf-8")
    nested = tmp_path / "nested" / "updater" / "Updater" / "Updater"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested", encoding="utf-8")

    assert checker._resolve_updater_entry(tmp_path / "flat", "updater/Updater") == flat
    assert (
        checker._resolve_updater_entry(tmp_path / "nested", "updater/Updater") == nested
    )


def test_resolve_updater_entry_normalizes_relative_install_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相对安装根解析出的 updater 必须可在自身目录中启动。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest 替换工具。
    :return: None。
    """

    checker = _load_checker()
    updater = tmp_path / "relative-install" / "updater" / "Updater"
    updater.parent.mkdir(parents=True)
    updater.write_text("updater", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolved = checker._resolve_updater_entry(
        Path("relative-install"),
        "updater/Updater",
    )

    assert resolved == updater
    assert resolved.is_absolute()


@pytest.mark.parametrize("relative_path", ["../outside/Updater", "/tmp/Updater"])
def test_resolve_updater_entry_rejects_install_root_escape(
    tmp_path: Path,
    relative_path: str,
) -> None:
    """检查器不得执行安装目录外的 updater。

    :param tmp_path: pytest 临时目录。
    :param relative_path: 越界的 updater 路径。
    :return: None。
    """

    checker = _load_checker()

    with pytest.raises(ValueError, match="escapes the install directory"):
        checker._resolve_updater_entry(tmp_path / "install", relative_path)


def test_path_exists_rejects_release_root_escape(tmp_path: Path) -> None:
    """普通制品存在性检查也不得借用 release 根外文件。"""
    checker = _load_checker()
    outside = tmp_path / "outside.exe"
    outside.write_text("outside", encoding="utf-8")

    assert checker._path_exists(tmp_path / "release", "../outside.exe") is False


def test_smoke_updater_rejects_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检查器必须拒绝无法加载 runtime 的 updater。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest 替换工具。
    :return: None。
    """

    checker = _load_checker()
    updater = tmp_path / "Updater"
    updater.write_text("updater", encoding="utf-8")
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=255, stdout="", stderr="missing Python"
        ),
    )

    ok, message = checker._smoke_updater(updater, timeout=30.0)

    assert ok is False
    assert "exited with 255" in message
    assert "missing Python" in message


def test_smoke_updater_accepts_successful_help_and_main_returns_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同平台 updater help 成功时主流程应放行。"""
    checker = _load_checker()
    install_dir = tmp_path / "install"
    updater = _write_valid_legacy_install(install_dir)
    calls: list[tuple[list[str], str]] = []

    def run(args: list[str], **kwargs):  # noqa: ANN003, ANN202
        """记录 smoke 命令并返回成功。"""
        calls.append((args, kwargs["cwd"]))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="help")

    monkeypatch.setattr(checker, "_host_platform", lambda: "windows")
    monkeypatch.setattr(checker.subprocess, "run", run)
    monkeypatch.setattr(
        checker.sys,
        "argv",
        _legacy_main_args(
            install_dir,
            "--updater-relative",
            "updater/SmartIngestUpdater.exe",
            "--smoke-updater",
        ),
    )

    assert checker.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert calls == [([str(updater), "--help"], str(updater.parent))]


@pytest.mark.parametrize(
    ("extra", "host_platform", "message"),
    [
        (("--smoke-updater",), "windows", "requires --updater-relative"),
        (
            (
                "--updater-relative",
                "updater/SmartIngestUpdater.exe",
                "--smoke-updater",
            ),
            "macos",
            "requires windows host",
        ),
        (
            ("--updater-relative", "../outside.exe", "--smoke-updater"),
            "windows",
            "escapes the install directory",
        ),
        (
            ("--updater-relative", "updater/missing.exe", "--smoke-updater"),
            "windows",
            "smoke entry missing",
        ),
    ],
)
def test_smoke_updater_main_fails_closed_for_invalid_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
    host_platform: str,
    message: str,
) -> None:
    """缺参数、跨平台、越界与缺失 updater 都必须失败关闭。"""
    checker = _load_checker()
    install_dir = tmp_path / "install"
    _write_valid_legacy_install(install_dir)
    monkeypatch.setattr(checker, "_host_platform", lambda: host_platform)
    monkeypatch.setattr(checker.sys, "argv", _legacy_main_args(install_dir, *extra))

    assert checker.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert any(message in item["message"] for item in output["checks"])


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (subprocess.TimeoutExpired(cmd="Updater", timeout=1), "timed out"),
        (OSError("permission denied"), "could not start"),
    ],
)
def test_smoke_updater_reports_execution_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    message: str,
) -> None:
    """updater 超时或无法启动时必须返回结构化失败。"""
    checker = _load_checker()
    updater = tmp_path / "Updater"
    updater.write_text("updater", encoding="utf-8")

    def run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        """抛出指定启动异常。"""
        raise exception

    monkeypatch.setattr(checker.subprocess, "run", run)

    ok, detail = checker._smoke_updater(updater, timeout=1.0)

    assert ok is False
    assert message in detail


def test_main_rejects_non_object_current_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """current.json 根不是对象时主流程必须返回一。"""
    checker = _load_checker()
    install_dir = tmp_path / "install"
    _write_valid_legacy_install(install_dir, current_payload=[])
    monkeypatch.setattr(checker.sys, "argv", _legacy_main_args(install_dir))

    assert checker.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert any(
        item["message"] == "current.json root must be an object"
        for item in output["checks"]
    )
