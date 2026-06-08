# PyQt NAS Online Updater Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not run `git add`, `git commit`, or `git push` unless the user explicitly requests it.

**Goal:** Build `UpdateOnlineTool` into a pip-installable Python SDK and CLI for PyQt tools that update from a NAS-hosted `latest.json` and zip package.

**Architecture:** The package exposes a GUI-agnostic backend API through `UpdateService`, with domain models for manifests and update decisions, infrastructure adapters for NAS filesystem access and updater process launching, and a `uot` CLI for publishing and verification. PyQt projects keep their UI and thread wrappers, then call the SDK from worker threads.

**Tech Stack:** Python 3.11, standard library only for runtime, pytest for tests, src-layout package, argparse CLI, pathlib/shutil/hashlib/subprocess.

---

## File Map

- Create: `pyproject.toml` - package metadata, pytest config, `uot` console script.
- Create: `src/update_online_tool/__init__.py` - public API exports.
- Create: `src/update_online_tool/errors.py` - structured error codes and exception type.
- Create: `src/update_online_tool/manifest.py` - manifest and package dataclasses.
- Create: `src/update_online_tool/versioning.py` - semantic version comparison and update decisions.
- Create: `src/update_online_tool/settings.py` - `config/settings.json` parser.
- Create: `src/update_online_tool/nas.py` - NAS root and package path resolution.
- Create: `src/update_online_tool/downloader.py` - streaming NAS package copy, progress, cancellation, sha256.
- Create: `src/update_online_tool/launcher.py` - standalone updater executable launcher.
- Create: `src/update_online_tool/service.py` - `UpdateService` facade.
- Create: `src/update_online_tool/cli.py` - `uot publish`, `uot verify`, and `uot check`.
- Modify: `config/settings.template.json` - replace multi-platform token settings with NAS-only settings.
- Modify: `_scripts/publish.py` - keep backward compatibility or delegate to `update_online_tool.cli`.
- Modify: `_scripts/verify_manifests.py` - keep backward compatibility or delegate to SDK verifier.
- Modify: `README.md` - document NAS-only first-version usage.
- Create: `tests/test_manifest.py`
- Create: `tests/test_versioning.py`
- Create: `tests/test_settings.py`
- Create: `tests/test_nas.py`
- Create: `tests/test_downloader.py`
- Create: `tests/test_service.py`
- Create: `tests/test_cli.py`

## Task 1: Package Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/update_online_tool/__init__.py`
- Create: `src/update_online_tool/errors.py`
- Test: `tests/test_package_import.py`

- [ ] **Step 1: Write the package import test**

```python
"""包入口测试。"""

from __future__ import annotations

import update_online_tool
from update_online_tool import UpdateError, UpdateErrorCode


def test_public_package_exports_version_and_errors() -> None:
    """验证包入口导出版本与错误类型。

    :return: None
    """
    assert isinstance(update_online_tool.__version__, str)
    assert UpdateErrorCode.MANIFEST_INVALID.value == "MANIFEST_INVALID"
    assert str(UpdateError(UpdateErrorCode.MANIFEST_INVALID, "bad manifest")) == "MANIFEST_INVALID: bad manifest"
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
python -m pytest tests/test_package_import.py -q
```

Expected: import fails because `update_online_tool` does not exist.

- [ ] **Step 3: Add package metadata**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "update-online-tool"
version = "0.1.0"
description = "NAS-based online update SDK and CLI for PyQt tools"
requires-python = ">=3.11"
readme = "README.md"
dependencies = []

[project.scripts]
uot = "update_online_tool.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Add errors and public exports**

Create `src/update_online_tool/errors.py`:

```python
"""在线升级结构化错误。"""

from __future__ import annotations

from enum import Enum


class UpdateErrorCode(str, Enum):
    """在线升级错误码。"""

    NAS_SOURCE_UNAVAILABLE = "NAS_SOURCE_UNAVAILABLE"
    SETTINGS_INVALID = "SETTINGS_INVALID"
    MANIFEST_NOT_FOUND = "MANIFEST_NOT_FOUND"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    UPDATE_NOT_AVAILABLE = "UPDATE_NOT_AVAILABLE"
    PACKAGE_NOT_FOUND = "PACKAGE_NOT_FOUND"
    PACKAGE_SIZE_MISMATCH = "PACKAGE_SIZE_MISMATCH"
    PACKAGE_HASH_MISMATCH = "PACKAGE_HASH_MISMATCH"
    UPDATER_NOT_FOUND = "UPDATER_NOT_FOUND"
    UPDATER_LAUNCH_FAILED = "UPDATER_LAUNCH_FAILED"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"


class UpdateError(RuntimeError):
    """在线升级异常。

    :param code: 结构化错误码。
    :param message: 可展示或记录的错误消息。
    :return: None
    """

    def __init__(self, code: UpdateErrorCode, message: str) -> None:
        """保存错误码和消息。

        :param code: 结构化错误码。
        :param message: 错误消息。
        :return: None
        """
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")
```

Create `src/update_online_tool/__init__.py`:

```python
"""UpdateOnlineTool 公共 API。"""

from __future__ import annotations

from update_online_tool.errors import UpdateError, UpdateErrorCode

__version__ = "0.1.0"

__all__ = [
    "UpdateError",
    "UpdateErrorCode",
    "__version__",
]
```

- [ ] **Step 5: Run the package import test**

Run:

```bash
python -m pytest tests/test_package_import.py -q
```

Expected: 1 passed.

## Task 2: Manifest Contract

**Files:**
- Create: `src/update_online_tool/manifest.py`
- Modify: `src/update_online_tool/__init__.py`
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write manifest tests**

```python
"""manifest 契约测试。"""

from __future__ import annotations

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest


def _payload() -> dict[str, object]:
    """构造有效 manifest 载荷。

    :return: manifest 字典。
    """
    return {
        "schema_version": 2,
        "app_id": "automation-manual-studio",
        "channel": "stable",
        "version": "1.0.6",
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-08T00:00:00+00:00",
        "notes": "release",
        "package": {
            "url": "automation-manual-studio/v1.0.6/package.zip",
            "size": 7,
            "sha256": "0" * 64,
        },
    }


def test_manifest_parses_v2_payload() -> None:
    """验证 v2 manifest 可解析为模型。

    :return: None
    """
    manifest = UpdateManifest.from_payload(_payload())

    assert manifest.app_id == "automation-manual-studio"
    assert manifest.package.url == "automation-manual-studio/v1.0.6/package.zip"
    assert manifest.to_payload()["version"] == "1.0.6"


def test_manifest_rejects_ifw_fields() -> None:
    """验证第一版契约拒绝 IFW 字段。

    :return: None
    """
    payload = _payload()
    payload["installer_kind"] = "qt_ifw"

    with pytest.raises(UpdateError) as error:
        UpdateManifest.from_payload(payload)

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_manifest_rejects_bad_sha256() -> None:
    """验证 sha256 格式错误会被拒绝。

    :return: None
    """
    payload = _payload()
    package = dict(payload["package"])  # type: ignore[arg-type]
    package["sha256"] = "bad"
    payload["package"] = package

    with pytest.raises(UpdateError) as error:
        UpdateManifest.from_payload(payload)

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
```

- [ ] **Step 2: Run failing manifest tests**

Run:

```bash
python -m pytest tests/test_manifest.py -q
```

Expected: import fails because `manifest.py` does not exist.

- [ ] **Step 3: Implement manifest models**

Create `src/update_online_tool/manifest.py` with dataclasses `UpdatePackageInfo` and `UpdateManifest`. Use `additionalProperties` behavior by rejecting any top-level key outside the approved set and any package key outside `url`, `size`, and `sha256`. Raise `UpdateError(UpdateErrorCode.MANIFEST_INVALID, "...")` for invalid payloads.

Key validation rules:

- `schema_version` must be `2`.
- `app_id`, `channel`, `version`, `min_supported_version`, `published_at`, and `notes` must be non-empty strings.
- `mandatory` must be `bool`.
- `package` must be a dictionary.
- `package.url` must be non-empty.
- `package.size` must be a positive integer.
- `package.sha256` must be 64 hex characters.

- [ ] **Step 4: Export manifest models**

Modify `src/update_online_tool/__init__.py` to export:

```python
from update_online_tool.manifest import UpdateManifest, UpdatePackageInfo
```

Add names to `__all__`.

- [ ] **Step 5: Run manifest tests**

Run:

```bash
python -m pytest tests/test_manifest.py -q
```

Expected: 3 passed.

## Task 3: Versioning and Update Decisions

**Files:**
- Create: `src/update_online_tool/versioning.py`
- Modify: `src/update_online_tool/__init__.py`
- Test: `tests/test_versioning.py`

- [ ] **Step 1: Write versioning tests**

```python
"""版本判断测试。"""

from __future__ import annotations

from update_online_tool.versioning import UpdateDecision, decide_update, parse_version_tuple


def test_parse_version_tuple_ignores_suffix() -> None:
    """验证语义化版本后缀不影响主版本比较。

    :return: None
    """
    assert parse_version_tuple("1.2.3-beta.1") == (1, 2, 3)


def test_decide_update_returns_available_for_newer_version() -> None:
    """验证远端版本更新时返回可升级。

    :return: None
    """
    result = decide_update(
        current_version="1.0.5",
        latest_version="1.0.6",
        mandatory=False,
        min_supported_version="1.0.0",
        skipped_version=None,
    )

    assert result is UpdateDecision.OPTIONAL_UPDATE


def test_decide_update_honors_skipped_version() -> None:
    """验证已跳过版本不会在自动检查时提示。

    :return: None
    """
    result = decide_update(
        current_version="1.0.5",
        latest_version="1.0.6",
        mandatory=False,
        min_supported_version="1.0.0",
        skipped_version="1.0.6",
    )

    assert result is UpdateDecision.SKIPPED


def test_decide_update_returns_mandatory_when_below_minimum() -> None:
    """验证当前版本低于最低支持版本时强制升级。

    :return: None
    """
    result = decide_update(
        current_version="1.0.0",
        latest_version="1.0.6",
        mandatory=False,
        min_supported_version="1.0.5",
        skipped_version="1.0.6",
    )

    assert result is UpdateDecision.MANDATORY_UPDATE
```

- [ ] **Step 2: Run failing versioning tests**

Run:

```bash
python -m pytest tests/test_versioning.py -q
```

Expected: import fails because `versioning.py` does not exist.

- [ ] **Step 3: Implement versioning**

Create `src/update_online_tool/versioning.py`:

```python
"""在线升级版本判断。"""

from __future__ import annotations

import re
from enum import Enum


class UpdateDecision(str, Enum):
    """升级决策。"""

    NOT_AVAILABLE = "not_available"
    OPTIONAL_UPDATE = "optional_update"
    MANDATORY_UPDATE = "mandatory_update"
    SKIPPED = "skipped"


def parse_version_tuple(version: str) -> tuple[int, int, int]:
    """解析主语义化版本。

    :param version: 版本字符串。
    :return: 三段整数版本。
    """
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def decide_update(
    *,
    current_version: str,
    latest_version: str,
    mandatory: bool,
    min_supported_version: str,
    skipped_version: str | None,
) -> UpdateDecision:
    """判断当前版本是否需要升级。

    :param current_version: 当前版本。
    :param latest_version: 远端最新版本。
    :param mandatory: manifest 是否声明强制升级。
    :param min_supported_version: 最低支持版本。
    :param skipped_version: 用户跳过版本。
    :return: 升级决策。
    """
    current = parse_version_tuple(current_version)
    latest = parse_version_tuple(latest_version)
    minimum = parse_version_tuple(min_supported_version)
    if current >= latest:
        return UpdateDecision.NOT_AVAILABLE
    if mandatory or current < minimum:
        return UpdateDecision.MANDATORY_UPDATE
    if skipped_version and skipped_version == latest_version:
        return UpdateDecision.SKIPPED
    return UpdateDecision.OPTIONAL_UPDATE
```

- [ ] **Step 4: Export `UpdateDecision`**

Modify `src/update_online_tool/__init__.py` to export `UpdateDecision`.

- [ ] **Step 5: Run versioning tests**

Run:

```bash
python -m pytest tests/test_versioning.py -q
```

Expected: 4 passed.

## Task 4: Settings and NAS Resolution

**Files:**
- Create: `src/update_online_tool/settings.py`
- Create: `src/update_online_tool/nas.py`
- Modify: `config/settings.template.json`
- Test: `tests/test_settings.py`
- Test: `tests/test_nas.py`

- [ ] **Step 1: Write settings tests**

```python
"""设置解析测试。"""

from __future__ import annotations

import json
from pathlib import Path

from update_online_tool.settings import UpdateToolSettings


def test_settings_loads_nas_root(tmp_path: Path) -> None:
    """验证 settings.json 可解析 NAS 根路径。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "nas": {"root": str(tmp_path / "nas")},
                "publish": {
                    "default_channel": "stable",
                    "default_minimum_version": "1.0.0",
                    "package_filename": "package.zip",
                },
                "updater": {"executable_name": "AutomationManualUpdater.exe"},
            }
        ),
        encoding="utf-8",
    )

    settings = UpdateToolSettings.load(settings_path)

    assert settings.nas_root == tmp_path / "nas"
    assert settings.default_channel == "stable"
    assert settings.package_filename == "package.zip"
```

- [ ] **Step 2: Write NAS resolution tests**

```python
"""NAS 路径解析测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.nas import NasReleaseSource


def test_nas_resolves_manifest_path(tmp_path: Path) -> None:
    """验证 app/channel manifest 路径解析。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)

    assert source.manifest_path("demo-app", "stable") == tmp_path / "demo-app" / "stable" / "latest.json"


def test_nas_rejects_parent_relative_package_url(tmp_path: Path) -> None:
    """验证 package.url 不允许跳出 NAS 根目录。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)

    with pytest.raises(UpdateError) as error:
        source.resolve_package_path("../secret.zip")

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
```

- [ ] **Step 3: Run failing settings and NAS tests**

Run:

```bash
python -m pytest tests/test_settings.py tests/test_nas.py -q
```

Expected: imports fail because modules do not exist.

- [ ] **Step 4: Implement settings**

Create `src/update_online_tool/settings.py` with `UpdateToolSettings` dataclass:

```python
@dataclass(frozen=True)
class UpdateToolSettings:
    """在线升级工具设置。

    :param nas_root: NAS 根目录。
    :param default_channel: 默认发布通道。
    :param default_minimum_version: 默认最低支持版本。
    :param package_filename: 发布包文件名。
    :param updater_executable_name: updater 可执行文件名。
    :return: None
    """

    nas_root: Path
    default_channel: str = "stable"
    default_minimum_version: str = "1.0.0"
    package_filename: str = "package.zip"
    updater_executable_name: str = "AutomationManualUpdater.exe"
```

Add `load(path: Path | None = None)` that defaults to `config/settings.json` under the current working directory, parses JSON, validates non-empty `nas.root`, and raises `UpdateError(UpdateErrorCode.SETTINGS_INVALID, "...")` for invalid settings.

- [ ] **Step 5: Implement NAS source**

Create `src/update_online_tool/nas.py` with `NasReleaseSource`:

- `manifest_path(app_id, channel)` returns `<root>/<app-id>/<channel>/latest.json`.
- `version_dir(app_id, version)` returns `<root>/<app-id>/v<version>`.
- `package_path(app_id, version, package_filename)` returns `<root>/<app-id>/v<version>/<package_filename>`.
- `resolve_package_path(package_url)` returns `<root>/<relative package url>`.
- Reject absolute paths and any path containing `..`.
- `ensure_available()` checks `root.exists()` and `root.is_dir()`, raising `NAS_SOURCE_UNAVAILABLE`.

- [ ] **Step 6: Replace settings template**

Modify `config/settings.template.json`:

```json
{
  "nas": {
    "root": "\\\\nas-server\\release-share\\UpdateOnlineTool"
  },
  "publish": {
    "default_channel": "stable",
    "default_minimum_version": "1.0.0",
    "package_filename": "package.zip"
  },
  "updater": {
    "executable_name": "AutomationManualUpdater.exe"
  }
}
```

- [ ] **Step 7: Run settings and NAS tests**

Run:

```bash
python -m pytest tests/test_settings.py tests/test_nas.py -q
```

Expected: all tests pass.

## Task 5: Downloader and Checksum

**Files:**
- Create: `src/update_online_tool/downloader.py`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write downloader tests**

```python
"""包复制和校验测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.downloader import CancellationToken, copy_package_with_verification


def test_copy_package_reports_progress_and_verifies_hash(tmp_path: Path) -> None:
    """验证包复制会报告进度并校验 sha256。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = tmp_path / "source.zip"
    target = tmp_path / "downloads" / "package.zip"
    source.write_bytes(b"release")
    progress: list[tuple[int, int]] = []

    result = copy_package_with_verification(
        source_path=source,
        target_path=target,
        expected_size=7,
        expected_sha256=hashlib.sha256(b"release").hexdigest(),
        progress=progress.append,
    )

    assert result.package_path == target
    assert result.verified is True
    assert target.read_bytes() == b"release"
    assert progress[-1] == (7, 7)


def test_copy_package_rejects_hash_mismatch(tmp_path: Path) -> None:
    """验证 sha256 不匹配会失败。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = tmp_path / "source.zip"
    source.write_bytes(b"release")

    with pytest.raises(UpdateError) as error:
        copy_package_with_verification(
            source_path=source,
            target_path=tmp_path / "package.zip",
            expected_size=7,
            expected_sha256="0" * 64,
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_HASH_MISMATCH


def test_copy_package_honors_cancellation(tmp_path: Path) -> None:
    """验证取消令牌会中止复制。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = tmp_path / "source.zip"
    source.write_bytes(b"x" * 4096)
    token = CancellationToken()
    calls = 0

    def progress(current: int, total: int) -> None:
        """首次进度回调后取消。

        :param current: 已复制字节数。
        :param total: 总字节数。
        :return: None
        """
        nonlocal calls
        calls += 1
        token.cancel()

    with pytest.raises(UpdateError) as error:
        copy_package_with_verification(
            source_path=source,
            target_path=tmp_path / "package.zip",
            expected_size=4096,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            progress=progress,
            cancellation_token=token,
            chunk_size=1024,
        )

    assert calls == 1
    assert error.value.code is UpdateErrorCode.OPERATION_CANCELLED
```

- [ ] **Step 2: Run failing downloader tests**

Run:

```bash
python -m pytest tests/test_downloader.py -q
```

Expected: import fails because `downloader.py` does not exist.

- [ ] **Step 3: Implement downloader**

Create `src/update_online_tool/downloader.py` with:

- `CancellationToken` class with `cancel()` and `cancelled` property.
- `PreparedPackage` dataclass containing `package_path`, `sha256`, and `verified`.
- `copy_package_with_verification(...)`.

Implementation details:

- Check source exists before opening; missing source raises `PACKAGE_NOT_FOUND`.
- Create target parent directory.
- Stream from source to `target_path.with_suffix(target_path.suffix + ".tmp")`.
- Update sha256 while writing.
- Report progress as `(copied_bytes, expected_size)`.
- Replace temp path atomically at end.
- Validate actual size and sha256.
- Delete temp file on failure when it exists.

- [ ] **Step 4: Run downloader tests**

Run:

```bash
python -m pytest tests/test_downloader.py -q
```

Expected: 3 passed.

## Task 6: Update Service

**Files:**
- Create: `src/update_online_tool/service.py`
- Modify: `src/update_online_tool/__init__.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: Write service tests**

```python
"""UpdateService 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from update_online_tool.service import UpdateService
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.versioning import UpdateDecision


def _write_manifest(root: Path, *, version: str, content: bytes = b"release") -> None:
    """写入 NAS 模拟 manifest 和 package。

    :param root: NAS 根目录。
    :param version: 版本号。
    :param content: 包内容。
    :return: None
    """
    package = root / "automation-manual-studio" / f"v{version}" / "package.zip"
    latest = root / "automation-manual-studio" / "stable" / "latest.json"
    package.parent.mkdir(parents=True)
    latest.parent.mkdir(parents=True)
    package.write_bytes(content)
    latest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": version,
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-08T00:00:00+00:00",
                "notes": "release",
                "package": {
                    "url": f"automation-manual-studio/v{version}/package.zip",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )


def test_service_check_returns_optional_update(tmp_path: Path) -> None:
    """验证 check 返回可选升级。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.6")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    result = service.check(
        app_id="automation-manual-studio",
        current_version="1.0.5",
        channel="stable",
    )

    assert result.decision is UpdateDecision.OPTIONAL_UPDATE
    assert result.manifest.version == "1.0.6"


def test_service_prepare_copies_package(tmp_path: Path) -> None:
    """验证 prepare 从 NAS 复制升级包。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.6")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))
    check = service.check(app_id="automation-manual-studio", current_version="1.0.5")

    prepared = service.prepare(check.manifest, tmp_path / "downloads")

    assert prepared.verified is True
    assert prepared.package_path.read_bytes() == b"release"
```

- [ ] **Step 2: Run failing service tests**

Run:

```bash
python -m pytest tests/test_service.py -q
```

Expected: import fails because `service.py` does not exist.

- [ ] **Step 3: Implement service facade**

Create `src/update_online_tool/service.py` with:

- `CheckUpdateResult` dataclass:
  - `decision: UpdateDecision`
  - `manifest: UpdateManifest`
  - `package_size: int`
  - `notes: str`
- `UpdateService` class:
  - `__init__(settings: UpdateToolSettings)`
  - `from_settings(path: Path | None = None)`
  - `check(app_id, current_version, channel="stable", skipped_version=None)`
  - `prepare(manifest, download_dir, progress=None, cancellation_token=None)`
  - `launch(package_path, manifest, install_root, old_pid, restart_executable)`

`check()` reads `<nas_root>/<app_id>/<channel>/latest.json`, parses it with `UpdateManifest`, validates `manifest.app_id` and `manifest.channel`, then calls `decide_update()`.

`prepare()` resolves `manifest.package.url` through `NasReleaseSource`, then calls `copy_package_with_verification()`.

`launch()` delegates to `StandaloneUpdaterLauncher` from Task 7.

- [ ] **Step 4: Export service**

Modify `src/update_online_tool/__init__.py`:

```python
from update_online_tool.service import CheckUpdateResult, UpdateService
```

Add names to `__all__`.

- [ ] **Step 5: Run service tests**

Run:

```bash
python -m pytest tests/test_service.py -q
```

Expected: 2 passed.

## Task 7: Standalone Updater Launcher

**Files:**
- Create: `src/update_online_tool/launcher.py`
- Test: `tests/test_launcher.py`

- [ ] **Step 1: Write launcher tests**

```python
"""updater 启动器测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.launcher import StandaloneUpdaterLauncher


def test_launcher_writes_pending_manifest_and_starts_process(tmp_path: Path) -> None:
    """验证 launcher 写入 pending manifest 并调用进程启动函数。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    updater = tmp_path / "AutomationManualUpdater.exe"
    updater.write_text("fake", encoding="utf-8")
    calls: list[list[str]] = []

    def popen(args: list[str], cwd: str, close_fds: bool):  # noqa: ANN001
        """捕获 Popen 参数。

        :param args: 命令参数。
        :param cwd: 工作目录。
        :param close_fds: 是否关闭文件描述符。
        :return: 假进程。
        """
        calls.append(args)

        class Process:
            """假进程。"""

            pid = 123

        return Process()

    result = StandaloneUpdaterLauncher(updater, popen=popen).launch(
        pending_payload={"package_path": "package.zip"},
        pending_manifest_path=tmp_path / "pending-update.json",
    )

    assert result.started is True
    assert result.updater_pid == 123
    assert json.loads((tmp_path / "pending-update.json").read_text(encoding="utf-8"))["package_path"] == "package.zip"
    assert calls == [[str(updater), "--pending", str(tmp_path / "pending-update.json")]]


def test_launcher_rejects_missing_updater(tmp_path: Path) -> None:
    """验证 updater exe 缺失时返回结构化错误。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    with pytest.raises(UpdateError) as error:
        StandaloneUpdaterLauncher(tmp_path / "missing.exe").launch(
            pending_payload={},
            pending_manifest_path=tmp_path / "pending.json",
        )

    assert error.value.code is UpdateErrorCode.UPDATER_NOT_FOUND
```

- [ ] **Step 2: Run failing launcher tests**

Run:

```bash
python -m pytest tests/test_launcher.py -q
```

Expected: import fails because `launcher.py` does not exist.

- [ ] **Step 3: Implement launcher**

Create `src/update_online_tool/launcher.py` with:

- `LaunchResult` dataclass containing `started`, `updater_pid`, and `pending_manifest_path`.
- `StandaloneUpdaterLauncher` accepting `updater_executable: Path` and optional `popen`.
- `launch(pending_payload, pending_manifest_path)` writes JSON and runs `[updater_executable, "--pending", pending_manifest_path]`.
- Missing executable raises `UpdateErrorCode.UPDATER_NOT_FOUND`.
- `OSError` during launch raises `UpdateErrorCode.UPDATER_LAUNCH_FAILED`.

- [ ] **Step 4: Run launcher tests**

Run:

```bash
python -m pytest tests/test_launcher.py -q
```

Expected: 2 passed.

## Task 8: CLI Publish, Verify, and Check

**Files:**
- Create: `src/update_online_tool/cli.py`
- Test: `tests/test_cli.py`
- Modify: `_scripts/publish.py`
- Modify: `_scripts/verify_manifests.py`

- [ ] **Step 1: Write CLI tests**

```python
"""CLI 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from update_online_tool.cli import main


def _settings(path: Path, nas_root: Path) -> None:
    """写入测试 settings。

    :param path: settings 路径。
    :param nas_root: NAS 根目录。
    :return: None
    """
    path.write_text(
        json.dumps(
            {
                "nas": {"root": str(nas_root)},
                "publish": {
                    "default_channel": "stable",
                    "default_minimum_version": "1.0.0",
                    "package_filename": "package.zip",
                },
                "updater": {"executable_name": "AutomationManualUpdater.exe"},
            }
        ),
        encoding="utf-8",
    )


def test_cli_publish_writes_package_and_latest_json(tmp_path: Path) -> None:
    """验证 publish 写入 NAS 包和 manifest。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)

    exit_code = main(
        [
            "publish",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--version",
            "1.0.6",
            "--package",
            str(package),
        ]
    )

    latest = nas_root / "automation-manual-studio" / "stable" / "latest.json"
    copied = nas_root / "automation-manual-studio" / "v1.0.6" / "package.zip"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert copied.read_bytes() == b"release"
    assert payload["package"]["sha256"] == hashlib.sha256(b"release").hexdigest()


def test_cli_verify_accepts_published_release(tmp_path: Path) -> None:
    """验证 verify 接受 publish 生成的发布内容。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)
    assert main(["publish", "--settings", str(settings_path), "--app", "automation-manual-studio", "--version", "1.0.6", "--package", str(package)]) == 0

    assert main(["verify", "--settings", str(settings_path), "--app", "automation-manual-studio"]) == 0
```

- [ ] **Step 2: Run failing CLI tests**

Run:

```bash
python -m pytest tests/test_cli.py -q
```

Expected: import fails because `cli.py` does not exist.

- [ ] **Step 3: Implement CLI**

Create `src/update_online_tool/cli.py` with `main(argv: list[str] | None = None) -> int`.

Commands:

- `publish --settings PATH --app APP --version VERSION --package PATH --channel stable --notes "" --min-supported-version "" --mandatory`
- `verify --settings PATH --app APP --channel stable`
- `check --settings PATH --app APP --current-version VERSION --channel stable`

`publish` behavior:

- Load settings.
- Copy source zip to `<nas-root>/<app>/v<version>/<package_filename>`.
- Compute size and sha256.
- Write manifest to version dir and channel dir.
- Use relative package URL `<app>/v<version>/<package_filename>`.

`verify` behavior:

- Load `<nas-root>/<app>/<channel>/latest.json`.
- Parse manifest.
- Resolve package path through `NasReleaseSource`.
- Check package exists, size matches, and sha256 matches.

`check` behavior:

- Call `UpdateService.check()`.
- Print decision and version.
- Return `0` when the manifest is valid, including no-update.

- [ ] **Step 4: Keep legacy script entry points working**

Modify `_scripts/publish.py` so its `main()` delegates to `update_online_tool.cli.main(["publish", ...])` or keep the current implementation and add a deprecation note in its docstring. Prefer delegation if argument compatibility can be preserved without losing existing behavior.

Modify `_scripts/verify_manifests.py` so its `main()` delegates to `update_online_tool.cli.main(["verify", ...])` or keep the current implementation and add a deprecation note in its docstring. Prefer delegation after tests prove compatibility.

- [ ] **Step 5: Run CLI tests**

Run:

```bash
python -m pytest tests/test_cli.py -q
```

Expected: 2 passed.

## Task 9: Documentation and Existing Tests

**Files:**
- Modify: `README.md`
- Modify: `tests/test_verify_manifests.py`
- Modify: `tests/test_verify_ifw_repository.py`

- [ ] **Step 1: Update README**

Replace the multi-platform sections with first-version NAS-only usage:

```markdown
# UpdateOnlineTool

NAS-based online update SDK and CLI for PyQt tools.

## First-version scope

- PyQt tools
- NAS release root
- `latest.json`
- zip package
- standalone updater executable

Qt IFW, GitHub, Gitee, DevOps, Electron, and Rust are not part of the first-version implementation.

## Install

```bash
pip install -e D:\tealiving\peoject\UpdateOnlineTool
```

## Configure

Create `config/settings.json` from `config/settings.template.json` and point `nas.root` to a Windows UNC path or a macOS mounted volume.

## Publish

```bash
uot publish --app automation-manual-studio --version 1.0.6 --package dist/app.zip
```

## Verify

```bash
uot verify --app automation-manual-studio
```

## SDK usage

```python
from update_online_tool import UpdateService

service = UpdateService.from_settings()
result = service.check(app_id="automation-manual-studio", current_version="1.0.5")
```
```

- [ ] **Step 2: Freeze or remove IFW tests**

Since IFW is explicitly out of scope, do not keep failing IFW tests in the active first-version test suite.

Choose one implementation path:

- Remove `tests/test_verify_ifw_repository.py` if `_scripts/verify_ifw_repository.py` is removed.
- Keep the file only if the script remains as legacy documentation and mark it outside the default test path.

Preferred first-version outcome: remove IFW verification script and test.

- [ ] **Step 3: Update legacy manifest tests**

Modify `tests/test_verify_manifests.py` so the expected contract no longer includes IFW `installer_kind` or `repository_url`. Keep tests for relative package URLs, package size, and sha256.

- [ ] **Step 4: Run the full UpdateOnlineTool test suite**

Run:

```bash
python -m pytest -q
```

Expected: all tests pass.

## Task 10: PyQt Project Integration Handoff

**Files:**
- Create: `docs/pyqt-integration.md`

- [ ] **Step 1: Document GUI project boundary**

Create `docs/pyqt-integration.md`:

```markdown
# PyQt Integration Guide

The GUI project owns UI only:

- update button
- update dialog
- progress bar
- cancel button
- QThread worker
- user-facing messages

`update_online_tool` owns backend update behavior:

- read NAS manifest
- decide update availability
- copy package
- verify size and sha256
- write pending manifest
- launch standalone updater

GUI worker example:

```python
from update_online_tool import UpdateService

class PrepareUpdateWorker:
    def __init__(self, service: UpdateService, manifest, download_dir):
        self._service = service
        self._manifest = manifest
        self._download_dir = download_dir

    def run(self):
        return self._service.prepare(
            self._manifest,
            self._download_dir,
            progress=self._emit_progress,
        )

    def _emit_progress(self, copied_bytes: int, total_bytes: int) -> None:
        percent = int(copied_bytes * 100 / total_bytes) if total_bytes else 0
        self.progress.emit(percent)
```
```

- [ ] **Step 2: Run documentation smoke checks**

Run:

```bash
python -m pytest -q
```

Expected: tests still pass after docs are added.

## Verification Checklist

- [ ] `python -m pytest tests/test_package_import.py -q`
- [ ] `python -m pytest tests/test_manifest.py tests/test_versioning.py -q`
- [ ] `python -m pytest tests/test_settings.py tests/test_nas.py -q`
- [ ] `python -m pytest tests/test_downloader.py -q`
- [ ] `python -m pytest tests/test_service.py tests/test_launcher.py -q`
- [ ] `python -m pytest tests/test_cli.py -q`
- [ ] `python -m pytest -q`
- [ ] `pip install -e .`
- [ ] `uot --help`
- [ ] `uot publish --settings <temp-settings> --app demo --version 1.0.0 --package <zip>`
- [ ] `uot verify --settings <temp-settings> --app demo`

## Self-Review Notes

Spec coverage:

- NAS-only configuration is covered in Task 4.
- Manifest contract and removal of IFW fields are covered in Task 2 and Task 9.
- SDK check, prepare, and launch are covered in Tasks 6 and 7.
- CLI publish, verify, and check are covered in Task 8.
- PyQt GUI boundary is covered in Task 10.

Implementation risks:

- Existing `_scripts` tests import private script modules directly. The implementation should either preserve those imports or update tests and README together.
- Existing deleted release artifacts in the working tree are unrelated to this plan and should not be restored or deleted unless the user requests cleanup.
- Actual NAS authentication is intentionally outside the package. Tests should use local temporary directories.

