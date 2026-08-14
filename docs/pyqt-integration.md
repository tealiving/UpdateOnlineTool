# PyQt 集成指南

GUI 项目只负责界面：

- 更新按钮。
- 更新对话框。
- 进度条。
- 取消按钮。
- QThread worker。
- 用户可见提示。

`update_online_tool` 负责后端在线升级行为：

- 读取 NAS manifest。
- 判断是否有可用更新。
- 复制升级包。
- 校验包体大小和 SHA-256。
- 写入 pending manifest。
- 由稳定 Bootstrap 与 Update Agent 完成进程交接；旧独立 updater 仅保留兼容路径。
- 装配 PyInstaller GUI release 与稳定 launcher。

GUI 项目应在线程中调用 SDK 方法。SDK 本身不导入 PyQt。

```python
from update_online_tool import UpdateService


class PrepareUpdateWorker:
    """PyQt worker example.

    :param service: 在线升级服务。
    :param manifest: 检查更新返回的 manifest。
    :param download_dir: 本地下载目录。
    :return: None
    """

    def __init__(self, service: UpdateService, manifest, download_dir):
        """保存 worker 参数。

        :param service: 在线升级服务。
        :param manifest: 检查更新返回的 manifest。
        :param download_dir: 本地下载目录。
        :return: None
        """
        self._service = service
        self._manifest = manifest
        self._download_dir = download_dir

    def run(self):
        """在线程中准备升级包。

        :return: 已准备升级包。
        """
        return self._service.prepare(
            self._manifest,
            self._download_dir,
            progress=self._emit_progress,
        )

    def _emit_progress(self, copied_bytes: int, total_bytes: int) -> None:
        """把 SDK 字节进度转换为 GUI 进度。

        :param copied_bytes: 已复制字节数。
        :param total_bytes: 总字节数。
        :return: None
        """
        percent = int(copied_bytes * 100 / total_bytes) if total_bytes else 0
        self.progress.emit(percent)
```

建议 GUI 边界：

- `检查更新` 按钮在轻量 worker 中调用 `UpdateService.check()`。
- `立即更新` 按钮在可取消 worker 中调用 `UpdateService.prepare()`。
- `prepare()` 完成后，GUI 调用启动 updater 的 SDK/API，然后退出应用。
- GUI 展示 `UpdateError.code.value` 和 `UpdateError.message`，不要解析异常文本。

## 新项目：Agent + Bootstrap 接入

新 PyQt 项目应使用 [Agent + Bootstrap 迁移指南](pyqt-agent-migration.md)。该流程在
Agent ready 后保存业务状态、确认 handoff 并退出；新版本只能由稳定 Bootstrap 启动。
它不要求 GUI 自行修改 `current.json`、等待 PID 或启动版本化 exe。

## 已有 PyQt updater 的 legacy 接入方式

如果 PyQt 工具已经拥有独立 updater 可执行文件，updater 仍保留在工具项目中；`update_online_tool.pyqt_runtime` 只负责写交接文件和启动进程。

```python
from update_online_tool.pyqt_runtime import (
    PyQtPendingUpdateRequest,
    launch_existing_pending,
    write_pyqt_pending_manifest,
)
```

交接流程：

1. GUI 通过 `UpdateService.check()` 或项目适配器检查 NAS manifest。
2. GUI 通过 `UpdateService.prepare()` 或项目适配器准备升级包。
3. GUI 使用 `write_pyqt_pending_manifest()` 写入 `pending-update.json`。
4. GUI 使用 `launch_existing_pending()` 启动工具项目自己的 updater。
5. GUI 退出。
6. 工具项目的 updater 等待旧 PID，按 UOT 标准目录安装文件，切换 `current.json`，再由稳定 launcher 打开新 GUI。

`launch_existing_pending()` 启动的命令形态：

```text
<updater_executable> apply --pending <pending-update.json> --restart
```

该 helper 只负责按后台窗口合同启动 updater；安装、切换和重启仍由 updater runtime 执行。

## 通用项目示例

某个工具项目可以使用以下 endpoint 值：

```json
{
  "manifest_url": "uot-nas://my-tool/stable",
  "package_url_prefix": "uot-nas://nas",
  "auth_provider": "update_online_tool"
}
```

接入方项目可以使用 UOT 装配命令把默认 `settings.json` 复制到 PyInstaller 运行时目录：

```text
_internal/config/settings.json
```

```powershell
uot assemble-pyinstaller --version 1.0.6 --product-name MyTool --settings config\settings.json --force
```

应用适配器建议按以下顺序解析 settings：

1. 显式传入适配器的 settings 路径。
2. 项目自定义环境变量，例如 `MY_TOOL_UPDATE_SETTINGS_FILE`。
3. SDK 通用环境变量 `UPDATE_ONLINE_TOOL_SETTINGS_FILE`。
4. 用户级配置 `%APPDATA%\my-tool\update-online-tool\settings.json`。
5. 打包后的 `_internal/config/settings.json`。
6. 开发兜底 `config/settings.json`。

这样可以让源码目录、launcher 安装根目录和版本化 release 目录复用同一套 SDK 配置。项目自定义环境变量属于接入方项目边界，不应写死在 `update_online_tool` SDK 中。

## 排障

- `SETTINGS_INVALID`：接入方应用没有打包或没有指向 `settings.json`。
- `MANIFEST_NOT_FOUND`：NAS 根目录可访问，但 `<app-id>/<channel>/latest.json` 缺失。
- `PACKAGE_HASH_MISMATCH`：NAS 上的 package 与 manifest 不一致；重新发布并运行 `uot verify`。
- updater 已启动但 GUI 没有重新打开：检查接入方工具自己的 updater 和 launcher 日志，而不是 SDK。
