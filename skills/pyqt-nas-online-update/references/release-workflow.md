# PyQt + NAS + UOT 发布流程

## 1. 变量清单

执行前先确认这些变量，缺失时从项目文档、spec、版本文件、配置文件或用户输入中获取：

- `project_root`：PyQt 项目根目录。
- `python_exe`：项目虚拟环境里的 Python。
- `uot_exe`：同一环境中的 `uot` CLI。
- `app_id`：发布到 NAS 的应用标识。
- `product_name`：安装目录和用户入口使用的产品名。
- `platform`：目标平台，通常是 `windows`、`macos` 或 `linux`。
- `app_exe`：GUI 稳定入口名称；Windows 通常带 `.exe`，macOS/Linux 通常不带。
- `updater_exe`：updater 入口名称；Windows 通常带 `.exe`，macOS/Linux 通常不带。
- `pyinstaller_spec`：PyInstaller spec 文件。
- `current_version`：当前源码版本。
- `target_version`：发布目标版本。
- `nas_root`：NAS 根目录或挂载路径。
- `nas_roots`：可选，多个网络环境下按顺序尝试的 NAS 根目录列表。
- `settings_file`：通常是 `config/settings.json`。
- `endpoint_file`：通常是 `update-endpoint.json`。

## 2. 初始化配置

优先生成项目本地配置，不要修改 pip 安装后的 UOT 包目录。

```powershell
uot init --app <app_id> --nas-root <nas_root> --force
```

预期生成：

```text
update-endpoint.json
config/settings.json
```

打包归属：

- `update-endpoint.json` 是应用侧更新入口声明；如果 GUI 运行时读取它，应随应用包分发。
- `config/settings.json` 是构建默认后端配置；使用 `uot assemble-pyinstaller --settings <settings_file>` 时会复制到运行时配置目录。若不用 UOT 装配，项目 PyInstaller spec 必须手动复制到等效位置。
- `current.json` 由 UOT 装配生成在安装根，updater 升级时修改；不要作为源码配置手写到 release 目录。
- `latest.json` 由 `uot publish` 写到 NAS，是远端发布 manifest；不要打进客户端应用包。
- `pending-update.json`、`update-result.json`、`update-status.json` 和 `logs/` 是运行时文件，不要预置。

传入 `--nas-root` 时，UOT 应检查：

- NAS 根目录存在。
- 当前系统凭证可读。
- 当前系统凭证可写入、读取并删除临时探测文件。

如果只是离线生成配置，可加：

```powershell
uot init --app <app_id> --nas-root <nas_root> --force --skip-nas-check
```

## 3. 构建 PyInstaller

从项目根目录执行：

```powershell
<python_exe> -m PyInstaller --noconfirm --clean <pyinstaller_spec>
```

如果项目有自定义 launcher，应保持 GUI 和 launcher 内部构建名分离，避免 release 目录里的 GUI 入口被 launcher 覆盖。

## 4. 装配安装目录和升级目录

优先使用 UOT CLI 或项目中的薄包装脚本。薄包装脚本只应传递项目参数，不应重新实现通用装配逻辑。

通用形态：

```powershell
uot assemble-pyinstaller `
  --dist-dir dist `
  --version <target_version> `
  --product-name <product_name> `
  --platform <platform> `
  --settings <settings_file>
```

Windows 默认入口为 `<product_name>.exe`。macOS/Linux 默认入口为 `<product_name>`；如果 PyInstaller 内部产物名不同，增加 `--entry-name`、`--release-entry-name` 或 `--launcher-entry-name`。

如果项目保留兼容包装脚本：

```powershell
<python_exe> tools\assemble_enterprise_release.py `
  --dist-dir dist `
  --version <target_version> `
  --update-dir `
  --settings-file <settings_file>
```

预期产物：

```text
dist\<product_name>_install_v<target_version>\
dist\<product_name>_update_v<target_version>\
```

用户快捷方式和手工启动应指向安装根目录里的稳定入口：

```text
dist\<product_name>_install_v<target_version>\<app_exe>
```

## 5. 打包升级包

只压缩升级目录内容，不压缩安装目录。

```powershell
Compress-Archive `
  -Path dist\<product_name>_update_v<target_version>\* `
  -DestinationPath dist\<product_name>_<target_version>.zip `
  -Force
```

## 6. 发布到 NAS

```powershell
uot publish `
  --settings <settings_file> `
  --app <app_id> `
  --version <target_version> `
  --platform <platform> `
  --package dist\<product_name>_<target_version>.zip `
  --notes-file docs\release-notes\<target_version>.md `
  --min-supported-version <minimum_supported_version>
```

`--notes` 适合短说明，`--notes-file` 适合直接复用 changelog 文件。发布后的说明会写入 manifest 和 `versions.json`，GUI/SDK 读取历史版本说明时直接调用 `list-remote` 或 `show-version`。

多 NAS 配置：

```json
{
  "nas": {
    "root": "/mnt/internal-nas/SmartIngest",
    "roots": [
      "/mnt/internal-nas/SmartIngest",
      "/Volumes/SmartIngestNAS",
      "\\\\nas-server\\SmartIngest"
    ]
  }
}
```

`check`、`verify`、`list-remote`、`show-version`、`prepare-version` 等读取操作会按 `nas.roots` 顺序选择第一个可访问目录。`publish` 仍写入主 `nas.root`，不要依赖自动 fallback 发布；需要同步多个 NAS 时应切换 settings 或分别发布。

校验：

```powershell
uot verify --settings <settings_file> --app <app_id> --platform <platform>
uot check --settings <settings_file> --app <app_id> --platform <platform> --current-version <current_version>
```

历史版本选择：

```powershell
uot list-remote --settings <settings_file> --app <app_id> --platform <platform>
uot show-version --settings <settings_file> --app <app_id> --version <target_version> --platform <platform>
uot prepare-version --settings <settings_file> --app <app_id> --version <target_version> --platform <platform> --download-dir updates
```

发布策略：

```powershell
uot publish --settings <settings_file> --app <app_id> --version <target_version> --package <package.zip> --requires-confirmation --rollout-percent 25 --data-schema-version 3
uot list-remote --settings <settings_file> --app <app_id> --platform <platform> --include-hidden
```

可选策略包括 `--allow-downgrade`、`--hidden`、`--requires-confirmation`、`--rollout-percent 0..100` 和 `--data-schema-version <int>`。`hidden` 版本默认不会出现在普通 `list-remote`，也不会被普通 `check` 当作可用更新；运维可显式 `--include-hidden` 后再选择版本。

签名发布：

```powershell
uot keygen --output secrets\uot-signing.key --public-output config\uot-signing.pub
uot publish --settings <settings_file> --app <app_id> --version <target_version> --package <package.zip> --sign-key secrets\uot-signing.key --key-id release
uot verify --settings <settings_file> --app <app_id> --signature-key config\uot-signing.pub
```

`keygen` 默认生成 Ed25519 私钥，并可通过 `--public-output` 导出客户端验证用公钥。`--sign-key` 会写入 manifest `signature`；`verify --signature-key`、`install-prepared --signature-key` 和 `apply-update --signature-key` 会拒绝被篡改的 manifest。生产环境只应把公钥打进客户端，私钥留在发布机或 CI 密钥库。

`prepare-version` 只复制并校验指定版本包到 `<download-dir>/<app>/<channel>/<platform-or-any>/<version>/package.zip`，不直接修改安装根或 `current.json`。后续命令应使用 JSON 输出里的 `package_path`。如果使用 UOT 标准 runtime，可以继续执行：

```powershell
uot install-prepared --install-root <install_root> --package updates\package.zip --manifest updates\latest.json --signature-key config\uot-signing.pub --dry-run
uot install-prepared --install-root <install_root> --package updates\package.zip --manifest updates\latest.json --signature-key config\uot-signing.pub
uot apply-update --pending <install_root>\pending-update.json --signature-key config\uot-signing.pub
uot rollback --install-root <install_root>
```

最终应用内的独立 updater 可使用更窄的 `uot-updater` 入口：

```powershell
uot write-updater-spec --output-dir build\updater --name <updater_name>
python -m PyInstaller --noconfirm build\updater\<updater_name>.spec
uot assemble-pyinstaller --version <target_version> --product-name <product_name> --settings <settings_file> --updater-bundle dist\<updater_name> --force
```

`--updater-bundle` 可以指向 onefile 文件或 onedir 目录；装配后会复制到安装根 `updater/`。完整安装包应携带 `updater/`，升级 zip 不应预置远端 `latest.json` 或运行态 `pending-update.json`、`update-result.json`、`update-status.json`。

```powershell
uot-updater install --install-root <install_root> --package updates\package.zip --manifest updates\latest.json --signature-key config\uot-signing.pub --wait-pid <old_gui_pid> --wait-timeout 60 --restart
uot-updater apply --pending <install_root>\pending-update.json --signature-key config\uot-signing.pub --restart
uot-updater rollback --install-root <install_root>
uot-updater launch-current --install-root <install_root>
```

`install-prepared` 和 `apply-update` 会校验包大小与 SHA-256，安全解压到 `releases/<target_version>`，切换 `current.json`，并写入 `update-result.json` 和 `update-status.json`。runtime 会创建 `update.lock` 防止并发更新；`switch-installed` 也使用同一把锁。失败时也会写入失败结果和失败状态，dry-run 不写安装状态。`update-status.json` 的标准阶段是 `waiting_old_process`、`verifying`、`extracting`、`switching`、`restarting`、`success` 和 `failed`，`percent` 是 UI 阶段提示，不是下载字节进度；状态同时包含 `started_at`、`phase_started_at`、`phase_elapsed_ms`、`total_elapsed_ms` 等耗时字段。`--wait-pid` 等待旧 GUI 退出，超时返回 `PROCESS_TIMEOUT`；`--restart` 会切换后启动当前入口并记录 `restarted_pid`。旧 GUI 退出后不能继续接收内存回调；实时进度要由 updater 窗口或外部轮询进程读取状态文件。
`uot publish` 会维护通道 `versions.json` 版本索引，并把包写入 `<app_id>/<channel>/v<target_version>/`。`list-remote` 优先读取索引，并补充扫描通道版本目录和旧版 `<app_id>/v<version>/` 历史目录。历史版本选择器应把 `versions.json.manifest_url` 当作目标版本 manifest 的权威路径，并通过 `show-version` / `get_remote_manifest()` 获取；不要在 GUI 或项目适配器里自行拼 `<app>/<channel>/v<version>/latest.json`。同一版本号可在不同 channel 远端存放不同包，但安装根仍使用 `releases/<target_version>`；同一客户端从测试包升级到正式包时应使用递增版本号，或显式 `install-prepared --force` 覆盖同版本 release。

诊断包：

```powershell
uot doctor --install-root <install_root> --output diagnostics\doctor.json --archive diagnostics\doctor.zip
```

`doctor` 会收集安装根路径摘要、写权限探针、UNC-like 提示、关键文件状态、`current.json`、`update-result.json`、`update-status.json`、`pending-update.json` 摘要、`update.lock`、已安装版本列表和日志摘要。诊断包不包含 `config/settings*.json` 或签名私钥。

旧安装根迁移：

```powershell
uot write-migration-package --output-dir dist\<product_name>_migration_v<current_version> --app <app_id> --version <current_version> --entry-name <app_exe> --platform <platform> --updater-bundle dist\<updater_name> --settings <settings_file> --endpoint <endpoint_file>
uot verify-migration-package --package-dir dist\<product_name>_migration_v<current_version>
uot migrate-install-root --install-root <install_root> --version <current_version> --entry-name <app_exe> --app <app_id> --platform <platform> --dry-run
uot migrate-install-root --install-root <install_root> --version <current_version> --entry-name <app_exe> --app <app_id> --platform <platform>
```

迁移会把旧安装根中的应用文件复制到 `releases/<current_version>/` 并写入 `current.json`，但不会删除旧根目录文件。运行态文件如 `update-result.json`、`update-status.json`、`pending-update.json`、`update.lock`、`logs/` 不会复制进 release。

本地已安装版本切换：

```powershell
uot list-installed --install-root <install_root>
uot-updater switch-installed --install-root <install_root> --version <target_version> --wait-pid <old_gui_pid> --restart
```

`switch-installed` 只适用于 `releases/<target_version>/<app_exe>` 已存在的版本。它会在 `update.lock` 保护下原子更新安装根 `current.json` 并记录 `previous_version`；GUI 内版本选择器应优先交给 `uot-updater switch-installed --wait-pid --restart`，让本地切换和远端安装使用同一套后台进程模型。

打包边界：

- `update-endpoint.json` 应打进应用包。
- `current.json` 属于安装根运行状态，首包带初始版本，后续由 updater/runtime 修改。
- `config/settings*.json` 通常只用于构建和发布，不应带 NAS 敏感配置进入最终用户包。
- `latest.json` 和 `versions.json` 只属于 NAS 发布目录。
- `pending-update.json`、`update-result.json` 和 `update-status.json` 是运行时文件。

传入 `--platform` 时，UOT 会按平台隔离 manifest 和包体，避免 Windows/macOS/Linux 同版本包互相覆盖。NAS 目录通常应包含：

```text
<nas_root>\
└── <app_id>\
    └── stable\
        ├── <platform>\
        │   ├── latest.json
        │   └── versions.json
        └── v<target_version>\
            └── <platform>\
                ├── latest.json
                └── package.zip
```

## 7. 本地升级验证

从旧版本安装根启动：

```powershell
dist\<product_name>_install_v<current_version>\<app_exe>
```

验证项：

- GUI 能发现 `<target_version>`。
- 升级完成后 GUI 可重新进入新版本。
- 安装根 `current.json` 的 `version` 已切到 `<target_version>`。
- 安装根 `update-result.json` 的 `success` 为 `true`。
- 安装根 `update-status.json` 的 `phase` 为 `success`；失败时 GUI 能在下次启动展示 `message`。
- `logs/update.log` 有安装成功记录。
- `logs/launcher.log` 有版本切换或重启记录。

## 8. 提交前检查

- 源码版本只来自项目统一版本文件。
- 项目配置位于 `config/settings.json` 和 `update-endpoint.json`。
- UOT 依赖包目录没有被写入项目私有配置。
- 打包脚本没有重新实现 UOT 已提供的通用装配、发布、校验能力。
- 验证命令和结果应记录到交付说明或最终回复中。
