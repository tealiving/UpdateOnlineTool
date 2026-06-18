# UpdateOnlineTool

基于 NAS 的桌面工具在线升级 SDK 和 CLI。


https://github.com/user-attachments/assets/18b6dd1b-5cea-409e-9ca0-1f556fee3ff2

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

推荐在接入方工具项目中执行 `uot init` 自动生成配置，不要求用户手动创建 JSON：

```bash
uot init --nas-root D:\Nas
```

该命令会生成两类文件：

- 项目内 `update-endpoint.json`：随工具源码和打包产物一起分发，用于告诉工具走 NAS + 自定义 updater 流程。
- 项目内 `config/settings.json`：用于保存 NAS 根路径、默认发布通道和 updater 名称；PyInstaller 打包时可由 UOT 装配命令自动复制到 `_internal/config/settings.json`。

传入 `--nas-root` 时，`init` 会在写配置前自动检查 NAS 根目录：

- 目标路径是否存在且是目录。
- 当前系统凭证是否可读取该目录。
- 当前系统凭证是否可写入、读取并删除临时探测文件。

检查结果会直接打印到 CLI 输出，例如：

```text
NAS check ok: root=D:\Nas
NAS check ok: readable
NAS check ok: writable
```

如果当前机器暂时无法连接 NAS，但只需要离线生成配置，可以显式跳过检查：

```bash
uot init --nas-root D:\Nas --skip-nas-check
```

`--app` 可选；不传时自动使用当前工作目录名作为应用标识。`--output` 也可选，默认写入当前目录的 `update-endpoint.json`。

## 应用打包文件清单

接入方应用仓库应保留并打包两类配置：

- `update-endpoint.json`：应用自己的更新入口声明。标准接入时应随源码提交，并随 GUI 包一起分发，供应用判断使用 NAS + UOT + 自定义 updater 流程。
- `config/settings.json`：构建默认后端配置，包含 NAS 根路径、默认 channel、包文件名和 updater 名称。使用 `uot assemble-pyinstaller --settings config/settings.json` 时，UOT 会把它复制到运行时配置位置：Windows/Linux onedir 为 `_internal/config/settings.json`，macOS `.app` 为 `Contents/Resources/config/settings.json`。如果不用 UOT 装配，接入方自己的 PyInstaller spec 必须完成同等复制。

不要把下面这些文件当作应用源码配置手工打包：

- `current.json`：由 `uot assemble-pyinstaller` 写到安装根目录，表示当前激活 release；升级时由 updater 修改。初始完整安装包应包含装配生成的安装根，因此会自然包含它；版本化 GUI release 目录和升级 zip 不应手写这个文件。
- `latest.json`：由 `uot publish` 写到 NAS 的 channel/version 目录，是远端发布 manifest，不应放进客户端应用包。
- `pending-update.json`、`update-result.json`、`update-status.json`、`logs/`：运行时生成，用于 updater 交接、进度状态、结果和排障，不应随应用包预置。

需要写入用户级 settings 时，显式增加 `--user-settings`：

```bash
uot init --nas-root D:\Nas --user-settings
```

Windows 用户级路径：

```text
%APPDATA%\<app-id>\update-online-tool\settings.json
```

只想生成项目内 endpoint 时，可以不传 `--nas-root`：

```bash
uot init
```

需要把 settings 写到指定路径时，可以增加 `--settings-output`：

```bash
uot init --app my-tool --output update-endpoint.json --nas-root D:\Nas --settings-output config\settings.json
```

手动配置仍然支持，适合运维或打包脚本准备内置默认配置。
Windows 可从 `config/settings.template.json` 开始，macOS 可从 `config/settings.macos.template.json` 开始。

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
  },
  "updater": {
    "executable_name": "MyToolUpdater"
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

## PyInstaller 装配

UOT 提供标准 PyInstaller 目录装配命令，接入方不需要自己处理 launcher 名称、`current.json` 或 release 目录结构。

前提是 PyInstaller 已经分别构建两个 onedir 目录。Windows 默认入口带 `.exe`：

```text
dist/
├── MyTool_release_v1.0.6/
│   ├── MyTool.exe
│   └── _internal/
└── MyTool_launcher/
    ├── MyToolLauncher.exe
    └── _internal/
```

执行装配：

```bash
uot assemble-pyinstaller --version 1.0.6 --product-name MyTool --settings config/settings.json --force
```

输出目录：

```text
dist/
├── MyTool_install_v1.0.6/
│   ├── MyTool.exe
│   ├── current.json
│   └── releases/
│       └── 1.0.6/
│           ├── MyTool.exe
│           └── _internal/config/settings.json
└── MyTool_update_v1.0.6/
    ├── MyTool.exe
    ├── _internal/config/settings.json
    └── _launcher/
        └── MyTool.exe
```

用户快捷方式和后续启动入口始终指向安装根目录的 `MyTool.exe`。该文件是稳定 launcher；实际 GUI 位于 `releases/<version>/MyTool.exe`。升级包中的 `_launcher/MyTool.exe` 用于升级后刷新安装根目录的稳定入口。

macOS onedir 产物使用无 `.exe` 入口，并显式指定平台：

```text
dist/
├── MyTool_release_v1.0.6/
│   ├── MyToolGui
│   └── _internal/
└── MyTool_launcher/
    ├── MyToolLauncher
    └── _internal/
```

```bash
uot assemble-pyinstaller --version 1.0.6 --product-name MyTool --platform macos --settings config/settings.json --force
```

macOS 输出中的稳定入口为 `MyTool_install_v1.0.6/MyTool`，release GUI 位于 `releases/1.0.6/MyTool`。如果项目内部构建名不同，使用 `--entry-name`、`--release-entry-name` 或 `--launcher-entry-name` 显式覆盖。

macOS `.app` bundle 也可以作为入口装配，适合需要应用包目录结构的本地或内部分发形态：

```text
dist/
├── MyTool_release_v1.0.6/
│   └── MyToolGui.app/
│       └── Contents/MacOS/MyToolGui
└── MyTool_launcher/
    └── MyToolLauncher.app/
        └── Contents/MacOS/MyToolLauncher
```

```bash
uot assemble-pyinstaller \
  --version 1.0.6 \
  --product-name MyTool \
  --platform macos \
  --entry-name MyTool.app \
  --release-entry-name MyToolGui.app \
  --launcher-entry-name MyToolLauncher.app \
  --settings config/settings.json \
  --force
```

`.app` 模式下 `current.json.entry.kind` 会写为 `app_bundle`，settings 会复制到 `MyTool.app/Contents/Resources/config/settings.json`。

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

多平台并行发布时，推荐显式传 `--platform`，避免同一个版本号的 Windows/macOS/Linux 包互相覆盖：

```bash
uot publish \
  --settings config/settings.json \
  --app my-tool \
  --version 1.0.6 \
  --platform macos \
  --package dist/MyTool_macos_1.0.6.zip

uot verify --settings config/settings.json --app my-tool --platform macos
uot check --settings config/settings.json --app my-tool --platform macos --current-version 1.0.5
```

发布时可附带版本治理策略：

```bash
uot publish \
  --settings config/settings.json \
  --app my-tool \
  --version 1.0.7 \
  --package dist/MyTool_1.0.7.zip \
  --requires-confirmation \
  --rollout-percent 25 \
  --data-schema-version 3
```

可选策略包括 `--allow-downgrade`、`--hidden`、`--requires-confirmation`、`--rollout-percent 0..100` 和 `--data-schema-version <int>`。`hidden` 版本默认不会出现在 `list-remote`，也不会被普通 `check` 当作可用更新；运维可用 `list-remote --include-hidden`、`show-version` 或 `prepare-version` 显式操作。

企业发布可开启 manifest 签名：

```bash
uot keygen --output secrets/uot-signing.key --public-output config/uot-signing.pub
uot publish \
  --settings config/settings.json \
  --app my-tool \
  --version 1.0.7 \
  --package dist/MyTool_1.0.7.zip \
  --sign-key secrets/uot-signing.key \
  --key-id release

uot verify --settings config/settings.json --app my-tool --signature-key config/uot-signing.pub
```

`keygen` 默认生成 Ed25519 私钥，并可通过 `--public-output` 导出客户端验证用公钥。`--sign-key` 会把 `signature` 写入 `latest.json` 和版本目录 manifest。`verify --signature-key`、`install-prepared --signature-key`、`apply-update --signature-key` 会拒绝被篡改的 manifest。生产环境只应把公钥打进客户端，私钥留在发布机或 CI 密钥库。兼容场景仍可用 `keygen --algorithm hmac-sha256`。

需要让 GUI 提供“选择历史版本”时，可以先列出 NAS 上已发布版本，再准备指定版本包：

```bash
uot list-remote --settings config/settings.json --app my-tool --platform macos
uot list-remote --settings config/settings.json --app my-tool --platform macos --include-hidden
uot show-version --settings config/settings.json --app my-tool --version 1.0.4 --platform macos
uot prepare-version --settings config/settings.json --app my-tool --version 1.0.4 --platform macos --download-dir updates/
```

`uot publish` 会维护通道下的 `versions.json` 索引；`list-remote` 优先读取索引，索引不存在时回退扫描 `v<version>` 目录并输出 JSON。`prepare-version` 会复制并校验目标版本的包，但不直接修改安装根或 `current.json`。

已准备好的 zip 包可以交给标准 updater runtime 安装。runtime 会校验包大小和 SHA-256，安全解压到 `releases/<version>`，切换 `current.json`，并写入 `update-result.json`：

```bash
uot install-prepared \
  --install-root /Applications/MyTool \
  --package updates/package.zip \
  --manifest updates/latest.json

uot apply-update --pending /Applications/MyTool/pending-update.json
uot rollback --install-root /Applications/MyTool
```

面向最终应用打包时，也可以使用更窄的 updater 入口 `uot-updater`。它只包含安装、应用 pending、回滚和启动当前版本，适合作为独立 updater exe 打包：

```bash
uot write-updater-spec --output-dir build/updater --name MyToolUpdater
python -m PyInstaller --noconfirm build/updater/MyToolUpdater.spec
uot assemble-pyinstaller \
  --version 1.0.6 \
  --product-name MyTool \
  --settings config/settings.json \
  --updater-bundle dist/MyToolUpdater \
  --force
```

`--updater-bundle` 可以指向 PyInstaller onefile 文件或 onedir 目录，UOT 会复制到安装根 `updater/` 下。完整安装包应包含这个 `updater/` 目录；升级 zip 不需要预置远端 `latest.json` 或运行态 `pending-update.json`。

```bash
uot-updater install \
  --install-root /Applications/MyTool \
  --package updates/package.zip \
  --manifest updates/latest.json \
  --signature-key config/uot-signing.pub \
  --wait-pid 12345 \
  --wait-timeout 60 \
  --restart

uot-updater apply --pending /Applications/MyTool/pending-update.json --signature-key config/uot-signing.pub --restart
uot-updater rollback --install-root /Applications/MyTool
uot-updater launch-current --install-root /Applications/MyTool
```

安装前可用 `--dry-run` 做预检：

```bash
uot install-prepared \
  --install-root /Applications/MyTool \
  --package updates/package.zip \
  --manifest updates/latest.json \
  --dry-run
```

`apply-update` 读取 `pending-update.json` 中的 `package_path`、`install_root` 和 `manifest`。runtime 会创建 `update.lock` 防止同一安装根并发更新；成功或失败都会写入 `update-result.json`，并持续刷新 `update-status.json`，但 dry-run 不写安装状态。标准状态阶段包括 `waiting_old_process`、`verifying`、`extracting`、`switching`、`restarting`、`success` 和 `failed`，`percent` 是面向 UI 的阶段进度提示，不代表逐字节下载进度。`--wait-pid` 用于等待旧 GUI 退出，超时返回 `PROCESS_TIMEOUT` 并提示用户关闭应用；`--restart` 会在切换 `current.json` 后启动当前版本并记录 `restarted_pid`。如果旧 GUI 已退出，实时进度必须由 updater 自己显示窗口，或由外部监控进程轮询 `update-status.json`；新 GUI 启动后可读取该文件展示上次更新结果。如果项目已有自定义 updater，也可以只使用 `prepare-version` 和 SDK，自行控制进程退出、安装和重启。

如果目标版本已经存在于安装根的 `releases/<version>`，可以直接列出并切换本地版本：

```bash
uot list-installed --install-root /Applications/MyTool
uot switch-installed --install-root /Applications/MyTool --version 1.0.4
```

`switch-installed` 会校验 `releases/<version>/<entry>` 存在，并原子更新安装根 `current.json`，同时记录 `previous_version` 供 `rollback` 使用。它不下载包、不解压包，也不重启 GUI；接入方可在切换后自行重启或让 launcher 下次启动时进入目标版本。

现场排障可收集诊断报告和诊断包：

```bash
uot doctor \
  --install-root /Applications/MyTool \
  --output diagnostics/doctor.json \
  --archive diagnostics/doctor.zip
```

诊断报告包含安装根关键文件状态、`current.json`、`update-result.json`、`update-status.json`、`pending-update.json` 摘要、`update.lock` 状态、已安装版本列表、日志摘要和常见问题判断。诊断 zip 只包含报告、运行态 JSON、锁文件和日志，不包含 `config/settings*.json` 或签名私钥。

企业级执行链路：

1. `assemble-pyinstaller` 或接入方构建脚本生成安装根和 update zip 内容。
2. `publish --platform ... --sign-key ...` 把包、版本 manifest、通道 `latest.json`、`versions.json` 写入 NAS。
3. 客户端 `check` 只看当前通道 latest，自动更新不会使用 hidden 版本。
4. GUI/CLI 需要历史版本时用 `list-remote`、`show-version`、`prepare-version` 选择并下载指定版本。
5. `install-prepared --signature-key --dry-run` 预检包和 manifest。
6. 开发环境可用 `uot install-prepared` 或 `uot apply-update`；最终应用内建议调用打包后的 `uot-updater install/apply`。
7. `switch-installed` 只切换本地已安装版本；`rollback` 回到 `previous_version`。
8. 更新失败时用 `uot doctor --archive ...` 收集现场诊断包。

旧版平铺安装根迁移到新架构：

```bash
uot write-migration-package \
  --output-dir dist/MyTool_migration_v1.0.0 \
  --app my-tool \
  --version 1.0.0 \
  --entry-name MyTool.exe \
  --platform windows \
  --updater-bundle dist/MyToolUpdater \
  --settings config/settings.json \
  --endpoint update-endpoint.json

uot verify-migration-package --package-dir dist/MyTool_migration_v1.0.0

uot migrate-install-root \
  --install-root /Applications/MyTool \
  --version 1.0.0 \
  --entry-name MyTool.exe \
  --app my-tool \
  --platform windows \
  --dry-run

uot migrate-install-root \
  --install-root /Applications/MyTool \
  --version 1.0.0 \
  --entry-name MyTool.exe \
  --app my-tool \
  --platform windows
```

迁移会把旧安装根中的应用文件复制到 `releases/<version>/`，写入 `current.json`，并保留旧根目录文件不删除。运行态文件如 `update-result.json`、`update-status.json`、`pending-update.json`、`update.lock`、`logs/` 不会复制进 release。目标 release 已存在时需要 `--force`。

打包文件归属建议：

- `update-endpoint.json`：打进应用包，供运行时发现更新源。
- `current.json`：安装根运行状态；首包需要带初始版本，后续由 updater/runtime 修改。
- `config/settings*.json`：构建、发布和 NAS 配置；通常不要打进最终用户包，除非已脱敏且 updater 运行时确实需要。
- `latest.json`、`versions.json`：NAS 远程发布状态，不打进应用包。
- `pending-update.json`、`update-result.json`、`update-status.json`：运行时临时状态，不打进发布包。

平台隔离后的目录结构：

```text
<nas-root>/
└── <app-id>/
    ├── stable/
    │   └── macos/
    │       ├── latest.json
    │       └── versions.json
    └── v<version>/
        └── macos/
            ├── latest.json
            └── package.zip
```

对应 `package.url`：

```json
{
  "package": {
    "url": "my-tool/v1.0.6/macos/package.zip",
    "size": 123456,
    "sha256": "..."
  },
  "platform": "macos"
}
```

## 初始化项目配置

接入方工具项目可以用 `uot init` 生成自己的 `update-endpoint.json`：

```bash
uot init
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

`launch_existing_pending()` 只负责使用 `--pending <path>` 启动 updater。它不负责解压包、切换 `current.json`、重启 GUI 或清理旧 release；这些是 updater 的安装执行职责。标准目录结构、launcher 入口归一化和 `current.json` 初始内容由 `uot assemble-pyinstaller` 生成。

## 边界

`update_online_tool` 是共享升级后端：

- 读取 settings 和 NAS manifest。
- 发布并校验 `latest.json` 与 `package.zip`。
- 从 NAS 复制升级包并进行进度回调和 SHA-256 校验。
- 提供 PyQt pending manifest 辅助能力。
- 装配 PyInstaller GUI release 与稳定 launcher。
- 生成标准安装根目录、`current.json`、`releases/<version>` 和 `_launcher` 目录约定。

接入方工具项目仍然负责：

- 打包自己的 PyInstaller release。
- 提供 `MyToolUpdater.exe` 或等价 updater。
- 执行 updater 内的等待旧进程、解压、替换和重启动作。
- 管理所有 GUI 文案、弹窗、QThread worker、重试和取消交互。
