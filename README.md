# UpdateOnlineTool

基于 NAS 的桌面工具在线升级 SDK 和 CLI。

## 首版范围

支持：

- Python 桌面工具项目。
- PyQt 工具项目作为首个已落地的运行时集成目标。
- NAS 发布根目录。
- 操作系统托管的 SMB 凭证。
- `latest.json`。
- zip 升级包。
- 独立 updater 可执行文件。

首版暂不支持：

- Qt Installer Framework。
- GitHub、Gitee、DevOps 或 HTTP 更新源。
- 在 settings 中保存 API token、deploy key 或账户凭证。
- Electron、Rust、Tauri 或跨框架一等适配器。
- 内置 GUI 组件。

SDK 是纯 Python 后端，不导入 PyQt。它是“已支持 PyQt 对接”，不是“只能用于 PyQt”。非 Python 前端可以通过 Python sidecar 或 `uot` CLI 间接使用，但 Electron/Rust/Tauri 的一等适配器当前还未提供。

## 安装

```bash
pip install -e D:\tealiving\peoject\UpdateOnlineTool
```

## 配置

从 `config/settings.template.json` 创建 `config/settings.json`。

Windows NAS 示例：

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
    "executable_name": "MyToolUpdater.exe"
  }
}
```

macOS NAS 示例：

```json
{
  "nas": {
    "root": "/Volumes/release-share/UpdateOnlineTool"
  }
}
```

本工具不保存 NAS 用户名或密码。Windows 使用凭据管理器或当前 SMB 会话；macOS 使用钥匙串或已挂载的 SMB 卷。

本地 Windows 验证可以把普通目录当作 NAS 根目录：

```json
{
  "nas": {
    "root": "D:\\Nas"
  }
}
```

SDK 解析 settings 的优先级：

1. 显式传入的 settings 路径。
2. 通用环境变量 `UPDATE_ONLINE_TOOL_SETTINGS_FILE`。
3. 用户级配置：
   - Windows：`%APPDATA%\<app-id>\update-online-tool\settings.json`
   - macOS：`~/Library/Application Support/<app-id>/update-online-tool/settings.json`
   - Linux/其他：`~/.config/<app-id>/update-online-tool/settings.json`
4. 打包内置配置，例如 PyInstaller 的 `_internal/config/settings.json`。
5. 开发兜底 `config/settings.json`。

用户修改 NAS 路径时，推荐修改用户级配置或通过 GUI 设置页写入用户级配置，不要修改 SDK 安装目录。

## NAS 目录结构

```text
<nas-root>/
└── <app-id>/
    ├── stable/
    │   └── latest.json
    └── v<version>/
        └── package.zip
```

`package.url` 是相对 NAS 根目录的路径：

```json
{
  "package": {
    "url": "my-tool/v1.0.6/package.zip",
    "size": 123456,
    "sha256": "..."
  }
}
```

## 初始化项目配置

接入方工具项目可以用 `uot init` 生成自己的 `update-endpoint.json`：

```bash
uot init --app my-tool --output update-endpoint.json
```

默认生成 NAS SDK endpoint：

```json
{
  "channel": "stable",
  "installer_mode": "custom_updater",
  "manifest_sources": [
    {
      "name": "local-nas",
      "manifest_url": "uot-nas://my-tool/stable",
      "package_url_prefix": "uot-nas://nas",
      "auth_provider": "update_online_tool",
      "priority": 10
    }
  ]
}
```

## 发布

```bash
uot publish --settings config/settings.json --app my-tool --version 1.0.6 --package dist/app.zip
```

## 校验

```bash
uot verify --settings config/settings.json --app my-tool
```

## 检查更新

```bash
uot check --settings config/settings.json --app my-tool --current-version 1.0.5
```

## SDK 使用

```python
from update_online_tool import UpdateService

service = UpdateService.from_settings()
result = service.check(
    app_id="my-tool",
    current_version="1.0.5",
)
```

GUI 项目负责界面、QThread 包装、进度展示和用户提示。`update_online_tool` 负责 manifest 解析、版本决策、NAS 包复制、校验、pending manifest 写入和 updater 进程启动。

完整工具项目对接流程见 [docs/integration-guide.md](docs/integration-guide.md)。PyQt worker 和 pending manifest 细节见 [docs/pyqt-integration.md](docs/pyqt-integration.md)。

## PyQt 运行时契约

如果 PyQt 工具已经有自己的 updater 可执行文件，可以使用 `update_online_tool.pyqt_runtime`：

```python
from update_online_tool.pyqt_runtime import (
    PyQtPendingUpdateRequest,
    launch_existing_pending,
    write_pyqt_pending_manifest,
)
```

`write_pyqt_pending_manifest()` 会写入兼容 PyQt updater 的扁平 `pending-update.json`：

```json
{
  "app_id": "my-tool",
  "expected_sha256": "...",
  "from_version": "1.0.5",
  "install_root": "D:\\Tools\\MyTool",
  "old_pid": 12345,
  "package_path": "D:\\Tools\\MyTool\\updates\\package.zip",
  "restart_executable": "MyTool.exe",
  "to_version": "1.0.6"
}
```

`launch_existing_pending()` 只负责使用 `--pending <path>` 启动 updater。它不负责解压包、切换 `current.json`、重启 GUI 或清理旧 release；这些操作属于工具项目自己的独立 updater 和 launcher。

## 边界

`update_online_tool` 是共享升级后端：

- 读取 settings 和 NAS manifest。
- 发布并校验 `latest.json` 与 `package.zip`。
- 从 NAS 复制升级包并进行进度回调和 SHA-256 校验。
- 提供 PyQt pending manifest 辅助能力。

接入方工具项目仍然负责：

- 打包自己的 PyInstaller release。
- 提供 `MyToolUpdater.exe` 或等价 updater。
- 决定安装根目录结构和 `current.json` 语义。
- 管理所有 GUI 文案、弹窗、QThread worker、重试和取消交互。
