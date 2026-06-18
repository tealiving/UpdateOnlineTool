# 对接指南

本文档说明其他工具项目如何安装 `update-online-tool`，并把它作为共享在线升级后端使用。

## 1. 本包提供什么能力

`update-online-tool` 提供后端在线升级能力：

- 读取 `config/settings.json`。
- 读取 NAS 上的 `latest.json`。
- 比较当前版本和远端版本。
- 从 NAS 复制升级包到本地更新目录。
- 校验包体大小和 SHA-256。
- 写入 pending update manifest。
- 启动独立 updater 可执行文件。
- 通过 `uot` CLI 发布、校验和检查 release。

它不提供：

- GUI 组件或弹窗。
- 内置 PyQt `QThread`。
- 安装器界面。
- 适用于所有桌面外壳的通用文件替换引擎。
- GitHub、DevOps、HTTP、Electron、Rust 或 Tauri 的首版一等适配器。

## 2. 前端兼容边界

SDK 本身是纯 Python，不导入 PyQt。接入方项目只要把耗时任务放到 UI 线程之外，就可以从任意 Python 前端调用：

- PyQt / PySide：首个已支持的集成目标。
- Tkinter、wxPython、CLI 工具或服务进程：可以直接使用 SDK，但线程调度和 UI 行为由接入方负责。
- Electron、Rust、Tauri 或其他非 Python 外壳：当前还不是一等支持目标。可以通过 Python sidecar 或 `uot` CLI 调用，但前端桥接和 updater 进程需要接入方自己实现。

因此，首版定位是 **已支持 PyQt 对接**，不是 **只能用于 PyQt**。

## 3. 在工具项目中安装

本地开发时：

```powershell
python -m pip install -e D:\tealiving\peoject\UpdateOnlineTool\.worktrees\pyqt-nas-online-updater-20260608
```

内部正式包发布后，安装 wheel 或源码包：

```powershell
python -m pip install update-online-tool
```

客户机升级时不得执行 `pip install`。接入方工具应把该依赖冻结进 PyInstaller 或等价桌面发布包中。

## 4. 初始化项目配置和 NAS 配置

推荐接入方在安装 `update-online-tool` 后执行 `uot init`，由 CLI 自动生成项目配置：

```powershell
uot init --nas-root D:\Nas
```

该命令会写入：

- `update-endpoint.json`：放在工具项目内，随源码和打包产物分发，用于声明当前工具使用 NAS + `update-online-tool` + 自定义 updater。
- `config/settings.json`：放在工具项目内，保存 NAS 根路径等后端配置；打包时由 UOT 装配命令复制到 PyInstaller `_internal/config/settings.json`。

打包时按文件归属处理：

- 应用包应能读取 `update-endpoint.json`。如果 GUI 运行时依赖它判断更新来源，就必须随 GUI 包一起分发。
- `config/settings.json` 是构建默认配置。使用 `uot assemble-pyinstaller --settings config\settings.json` 时，UOT 会复制到运行时配置目录；不用 UOT 装配时，PyInstaller spec 需要手动复制等效文件。
- `current.json` 是安装根状态文件，由 UOT 装配生成，升级时由 updater 修改；不要把它作为源码配置放进版本化 GUI release。
- `latest.json` 是 NAS 远端 manifest，由 `uot publish` 生成；不要打进客户端应用包。
- `pending-update.json`、`update-result.json`、`update-status.json` 和 `logs/` 是运行时产物，不要预置。

传入 `--nas-root` 时，`init` 默认会先检查 NAS 目录连通性和权限，再写入配置。检查内容包括：

- 路径存在且是目录。
- 当前系统凭证可读取目录。
- 当前系统凭证可写入、读取并删除临时探测文件。

检查通过时 CLI 输出类似：

```text
NAS check ok: root=D:\Nas
NAS check ok: readable
NAS check ok: writable
```

检查失败时返回非 0 退出码，并且不会写入 `update-endpoint.json` 或项目 settings。若只是离线生成配置，可以显式跳过：

```powershell
uot init --nas-root D:\Nas --skip-nas-check
```

`--app` 可选；不传时自动读取当前工作目录名作为应用标识。`--output` 可选；不传时默认生成当前目录的 `update-endpoint.json`。因此在工具项目根目录执行时，最小命令就是 `uot init --nas-root D:\Nas`。

如果只需要生成项目内 endpoint，不想同时写入 settings，可以省略 `--nas-root`：

```powershell
uot init
```

如果需要写入用户级 settings，显式增加 `--user-settings`：

```powershell
uot init --nas-root D:\Nas --user-settings
```

如果打包脚本需要把 settings 写到指定路径，可以指定输出路径：

```powershell
uot init --app my-tool --output update-endpoint.json --nas-root D:\Nas --settings-output config\settings.json
```

手动创建共享升级后端使用的 `settings.json` 仍然支持，文件结构如下：

```json
{
  "nas": {
    "root": "D:\\Nas"
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

真实 SMB 共享示例：

```json
{
  "nas": {
    "root": "\\\\nas-server\\release-share\\UpdateOnlineTool"
  }
}
```

不要把 NAS 用户名或密码写入该文件。Windows 使用凭据管理器或当前 SMB 会话；macOS 使用钥匙串或已挂载的 SMB 卷。

SDK 解析 settings 的优先级：

1. 显式传入的 settings 路径。
2. 通用环境变量 `UPDATE_ONLINE_TOOL_SETTINGS_FILE`。
3. 用户级配置：
   - Windows：`%APPDATA%\<app-id>\update-online-tool\settings.json`
   - macOS：`~/Library/Application Support/<app-id>/update-online-tool/settings.json`
   - Linux/其他：`~/.config/<app-id>/update-online-tool/settings.json`
4. 打包内置配置，例如 PyInstaller 的 `_internal/config/settings.json`。
5. 开发兜底 `config/settings.json`。

用户或运维需要修改 NAS 路径时，可以修改项目内 `config/settings.json` 作为打包默认值；安装后如果需要按用户覆盖，应修改用户级 settings，或者让接入方 GUI 设置页写入该文件。不要要求用户修改 `pip install` 后的 SDK 包目录。

## 5. 装配 PyInstaller 发布目录

UOT 提供标准装配命令，接入方工具只需要先用 PyInstaller 构建 GUI bundle 和 launcher bundle。两个 bundle 的构建名称必须区分，避免 GUI exe 和 launcher exe 在同一个 spec 中同名覆盖。

推荐 PyInstaller 输出：

```text
dist/
├── MyTool_release_v1.0.6/
│   ├── MyTool.exe
│   └── _internal/
└── MyTool_launcher/
    ├── MyToolLauncher.exe
    └── _internal/
```

装配命令：

```powershell
uot assemble-pyinstaller `
  --version 1.0.6 `
  --product-name MyTool `
  --settings config\settings.json `
  --force
```

装配后输出：

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

安装根目录的 `MyTool.exe` 是稳定 launcher，用户快捷方式应固定指向它；GUI 版本切换由 `current.json` 指向 `releases/<version>/MyTool.exe`。升级包里的 `_launcher/MyTool.exe` 用于升级后刷新稳定入口。

## 6. 使用 CLI 发布 release

接入方项目首次接入时生成自己的 `update-endpoint.json`：

```powershell
uot init
```

默认输出 NAS SDK endpoint，适用于首版 PyQt + NAS + 自定义 updater 流程。需要生成项目内 NAS settings 时增加 `--nas-root`；需要写入用户级 settings 时同时增加 `--user-settings`。需要覆盖自动推导的应用标识时增加 `--app`。已存在文件时不会覆盖；确需覆盖时增加 `--force`。

发布端可以只使用 `uot`，不需要导入 Python 代码。

发布升级包：

```powershell
uot publish `
  --settings config\settings.json `
  --app my-tool `
  --version 1.0.6 `
  --package dist\MyTool_1.0.6.zip `
  --notes "发布 v1.0.6" `
  --min-supported-version 1.0.0
```

校验 NAS 内容：

```powershell
uot verify --settings config\settings.json --app my-tool
```

检查指定当前版本是否有更新：

```powershell
uot check --settings config\settings.json --app my-tool --current-version 1.0.5
```

发布时可附带版本治理策略：

```powershell
uot publish --settings config\settings.json --app my-tool --version 1.0.7 --package dist\package.zip --requires-confirmation --rollout-percent 25 --data-schema-version 3
```

支持的策略包括 `--allow-downgrade`、`--hidden`、`--requires-confirmation`、`--rollout-percent 0..100` 和 `--data-schema-version <int>`。`hidden` 版本默认不会出现在 `list-remote`，也不会被普通 `check` 当作可用更新；运维可以用 `list-remote --include-hidden`、`show-version` 或 `prepare-version` 显式操作。

企业发布可开启 manifest 签名：

```powershell
uot keygen --output secrets\uot-signing.key --public-output config\uot-signing.pub
uot publish --settings config\settings.json --app my-tool --version 1.0.7 --package dist\package.zip --sign-key secrets\uot-signing.key --key-id release
uot verify --settings config\settings.json --app my-tool --signature-key config\uot-signing.pub
```

`keygen` 默认生成 Ed25519 私钥，并可通过 `--public-output` 导出客户端验证用公钥。`--sign-key` 会把 `signature` 写入 `latest.json` 和版本目录 manifest。`verify --signature-key`、`install-prepared --signature-key`、`apply-update --signature-key` 会拒绝被篡改的 manifest。生产环境只应把公钥打进客户端，私钥留在发布机或 CI 密钥库。兼容场景仍可用 `keygen --algorithm hmac-sha256`。

列出和准备历史版本：

```powershell
uot list-remote --settings config\settings.json --app my-tool --platform windows
uot list-remote --settings config\settings.json --app my-tool --platform windows --include-hidden
uot show-version --settings config\settings.json --app my-tool --version 1.0.4 --platform windows
uot prepare-version --settings config\settings.json --app my-tool --version 1.0.4 --platform windows --download-dir updates
```

`prepare-version` 只复制并校验包，不直接改安装根或 `current.json`。已准备包可以交给标准 runtime 安装：

```powershell
uot install-prepared --install-root D:\Tools\MyTool --package updates\package.zip --manifest updates\latest.json
uot apply-update --pending D:\Tools\MyTool\pending-update.json
uot rollback --install-root D:\Tools\MyTool
```

最终应用中建议打包更窄的 `uot-updater` 入口，而不是完整发布端 CLI：

```powershell
uot write-updater-spec --output-dir build\updater --name MyToolUpdater
python -m PyInstaller --noconfirm build\updater\MyToolUpdater.spec
uot assemble-pyinstaller `
  --version 1.0.6 `
  --product-name MyTool `
  --settings config\settings.json `
  --updater-bundle dist\MyToolUpdater `
  --force
```

`--updater-bundle` 可以是 PyInstaller onefile 文件或 onedir 目录，装配后会进入安装根 `updater\`。完整安装包应携带 `updater\`；升级 zip 不应预置 `latest.json`、`pending-update.json`、`update-result.json` 或 `update-status.json`。

```powershell
uot-updater install --install-root D:\Tools\MyTool --package updates\package.zip --manifest updates\latest.json --signature-key config\uot-signing.pub --wait-pid 12345 --wait-timeout 60 --restart
uot-updater apply --pending D:\Tools\MyTool\pending-update.json --signature-key config\uot-signing.pub --restart
uot-updater rollback --install-root D:\Tools\MyTool
uot-updater launch-current --install-root D:\Tools\MyTool
```

上线前可先预检：

```powershell
uot install-prepared --install-root D:\Tools\MyTool --package updates\package.zip --manifest updates\latest.json --dry-run
```

`apply-update` 读取 pending manifest 中的 `package_path`、`install_root` 和 `manifest`。runtime 会校验包大小和 SHA-256，安全解压到 `releases\<version>`，切换 `current.json`，并写入 `update-result.json` 和 `update-status.json`。runtime 执行时会创建 `update.lock` 防止同一安装根并发更新；失败时也会写入失败结果和失败状态，dry-run 不写安装状态。`update-status.json` 的标准阶段包括 `waiting_old_process`、`verifying`、`extracting`、`switching`、`restarting`、`success` 和 `failed`；`percent` 是阶段级 UI 提示，不是下载字节进度。`--wait-pid` 用于等待旧 GUI 退出，超时返回 `PROCESS_TIMEOUT` 并提示用户关闭应用；`--restart` 会切换后启动当前版本并记录 `restarted_pid`。旧 GUI 退出后不能继续接收内存回调；实时进度应由 updater 自己显示窗口，或由外部进程轮询 `update-status.json`。新 GUI 启动时可读取该文件展示上次更新结果。如果项目需要完全自定义进程退出和重启，也可以只使用 `prepare-version` 和 SDK。

企业级执行链路：构建 update zip，`publish --sign-key` 写入 NAS，客户端 `check` 发现最新版本，历史版本选择器用 `list-remote/show-version/prepare-version`，runtime 通过 `install-prepared --signature-key` 或 `apply-update --signature-key` 安装，已安装版本通过 `switch-installed` 切换，失败时用 `rollback` 回到 `previous_version`。
`uot publish` 会维护通道下的 `versions.json` 索引；`list-remote` 优先读取该索引，索引不存在时回退扫描 `v<version>` 历史目录。

现场排障可收集诊断报告和诊断包：

```powershell
uot doctor --install-root D:\Tools\MyTool --output diagnostics\doctor.json --archive diagnostics\doctor.zip
```

诊断报告包含安装根关键文件状态、`current.json`、`update-result.json`、`update-status.json`、`pending-update.json` 摘要、`update.lock` 状态、已安装版本列表、日志摘要和常见问题判断。诊断 zip 不包含 `config/settings*.json` 或签名私钥。

旧版平铺安装根可迁移到新架构：

```powershell
uot write-migration-package --output-dir dist\MyTool_migration_v1.0.0 --app my-tool --version 1.0.0 --entry-name MyTool.exe --platform windows --updater-bundle dist\MyToolUpdater --settings config\settings.json --endpoint update-endpoint.json
uot verify-migration-package --package-dir dist\MyTool_migration_v1.0.0
uot migrate-install-root --install-root D:\Tools\MyTool --version 1.0.0 --entry-name MyTool.exe --app my-tool --platform windows --dry-run
uot migrate-install-root --install-root D:\Tools\MyTool --version 1.0.0 --entry-name MyTool.exe --app my-tool --platform windows
```

迁移会把旧安装根中的应用文件复制到 `releases\<version>\`，写入 `current.json`，并保留旧根目录文件不删除。运行态文件如 `update-result.json`、`update-status.json`、`pending-update.json`、`update.lock`、`logs\` 不会复制进 release。目标 release 已存在时需要 `--force`。

如果目标版本已经安装在本机安装根，可以直接切换本地 `current.json`：

```powershell
uot list-installed --install-root D:\Tools\MyTool
uot switch-installed --install-root D:\Tools\MyTool --version 1.0.4
```

`switch-installed` 只适用于 `releases\<version>\<app_exe>` 已存在的版本。它会记录 `previous_version` 供 `rollback` 使用，但不下载、不解压、不重启 GUI；接入方应在切换后自行重启或让稳定 launcher 下次启动时进入目标版本。

文件打包归属：

- `update-endpoint.json` 打进应用包。
- `current.json` 属于安装根运行状态，首包带初始版本，后续由 updater 修改。
- `config/settings*.json` 通常只用于构建和发布，不应带 NAS 敏感配置进入最终用户包。
- `latest.json` 和 `versions.json` 只放 NAS，不打进应用包。
- `pending-update.json`、`update-result.json` 和 `update-status.json` 是运行时文件，不打进发布包。

CLI 写入的 NAS 目录结构：

```text
<nas-root>/
└── my-tool/
    ├── stable/
    │   └── latest.json
    └── v1.0.6/
        ├── latest.json
        └── package.zip
```

## 7. 在 Python 工具中使用 SDK

最小检查和准备升级流程：

```python
from pathlib import Path

from update_online_tool import UpdateDecision, UpdateService


service = UpdateService.from_settings(Path("config/settings.json"))
result = service.check(
    app_id="my-tool",
    current_version="1.0.5",
    channel="stable",
)

if result.decision in {UpdateDecision.OPTIONAL_UPDATE, UpdateDecision.MANDATORY_UPDATE}:
    prepared = service.prepare(
        result.manifest,
        Path("updates"),
        progress=lambda copied, total: print(copied, total),
    )
    print(prepared.package_path)
```

`check()` 和 `prepare()` 都是后端操作。GUI 程序应在 worker 线程中调用，并把进度转换为对应前端的 signal/event。

## 8. 启动 updater

接入方工具必须提供独立 updater 可执行文件。首版 updater 可以由工具项目实现，但安装目录、launcher 归一化和 `current.json` 标准结构由 UOT 的 `assemble-pyinstaller` 生成。

如果 updater 接受 SDK 通用 pending payload，可以使用 `UpdateService.launch()`：

```python
service.launch(
    package_path=prepared.package_path,
    manifest=result.manifest,
    install_root=Path(r"D:\Tools\MyTool"),
    old_pid=12345,
    restart_executable="MyTool.exe",
)
```

SDK 通用 pending payload 结构如下：

```json
{
  "package_path": "D:\\Tools\\MyTool\\updates\\package.zip",
  "manifest": {
    "schema_version": 2,
    "app_id": "my-tool",
    "version": "1.0.6",
    "channel": "stable",
    "mandatory": false,
    "min_supported_version": "1.0.0",
    "package": {
      "url": "my-tool/v1.0.6/package.zip",
      "size": 123456,
      "sha256": "..."
    }
  },
  "install_root": "D:\\Tools\\MyTool",
  "old_pid": 12345,
  "restart_executable": "MyTool.exe"
}
```

如果 updater 需要 PyQt 扁平 pending 契约，使用 `update_online_tool.pyqt_runtime`：

```python
from update_online_tool.pyqt_runtime import (
    PyQtPendingUpdateRequest,
    launch_existing_pending,
    write_pyqt_pending_manifest,
)


pending_path = write_pyqt_pending_manifest(
    pending_path=Path(r"D:\Tools\MyTool\pending-update.json"),
    request=PyQtPendingUpdateRequest(
        package_path=prepared.package_path,
        manifest=result.manifest,
        install_root=Path(r"D:\Tools\MyTool"),
        old_pid=12345,
        restart_executable="MyTool.exe",
        from_version="1.0.5",
    ),
)

launch_existing_pending(
    updater_executable=Path(r"D:\Tools\MyTool\MyToolUpdater.exe"),
    pending_manifest_path=pending_path,
)
```

## 9. 推荐的接入方项目边界

接入方工具项目负责：

- 应用版本来源。
- 包体构建脚本。
- 前端更新按钮、弹窗、进度、取消和重试交互。
- worker 线程或任务调度。
- 独立 updater 可执行文件。
- 日志和用户可见的排障信息。

`update-online-tool` 负责：

- NAS settings。
- manifest schema。
- 版本决策。
- 包体复制和校验。
- pending manifest 辅助函数。
- CLI 发布、校验和检查命令。
- PyInstaller GUI bundle + launcher bundle 的标准装配。
- 安装根目录、`current.json`、`releases/<version>` 和 `_launcher` 目录约定。

## 10. 最小端到端流程

1. 工具项目为新版本构建 zip 升级包。
2. 发布前运行 `uot assemble-pyinstaller` 生成标准安装目录和升级目录。
3. 发布端把升级目录压缩为包体并运行 `uot publish`。
4. 发布端运行 `uot verify`。
5. 用户启动旧版本安装根目录的稳定 launcher。
6. 前端调用 SDK `check()`。
7. 用户确认后，前端调用 SDK `prepare()`。
8. 前端写入 pending manifest 并启动 updater。
9. 前端退出。
10. Updater 安装新 release，切换 `current.json`，并由稳定 launcher 打开新版本 GUI。
