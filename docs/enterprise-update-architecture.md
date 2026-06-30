# Enterprise Update Architecture Review

本文档梳理 UOT 当前版本更新、远端版本安装、本地版本切换和回滚链路，并标注企业级可用状态与后续增强项。

## 架构边界

UOT 应拥有完整在线更新能力：NAS 配置解析、发布目录约定、manifest 与签名、版本索引、包复制与校验、pending 交接、标准 updater 启动、安装、切换、回滚、进程等待、重启、状态文件和诊断。

工具仓库只应提供：

- `app_id`、`platform`、`channel`、`install_root`、settings 路径。
- GUI 展示、确认弹窗、下载进度展示、后台线程调度。
- 调用 `DesktopUpdateClient.check()`、`list_remote_versions()`、`install_remote_version()`、`switch_installed_version()`、`rollback()`、`read_status()`、`read_result()`。

工具仓库不应解析 `current.json`、拼 updater 命令、寻找 updater exe、构造版本 `latest.json` 路径、修改 `releases/` 或处理 sidecar。

## 发布模型

UOT 当前采用单机 CLI 发布模型：发布人员在发布机上运行 `uot publish`，直接把包、manifest、`latest.json` 和 `versions.json` 写入 NAS 发布根。当前没有发布服务、服务端部署、后台 worker、队列或中心化控制面。

`publish.lock` 是 NAS 文件锁，用于防止同一发布根下重复执行 `uot publish` 时互相覆盖；它不是分布式发布系统，也不承担服务调度职责。若单机发布进程异常退出后留下 `publish.lock`，发布人员确认没有正在运行的 `uot publish` 后可以人工删除该锁文件再重试。

## 执行链路

### 1. 配置解析

`UpdateToolSettings` 从显式 settings、环境变量、用户级配置、打包内置配置、开发目录依次解析。`nas.root` 和 `nas.roots` 使用 `Path` 保存，支持普通路径、UNC 和 `file://` URI。读操作使用第一个可访问 NAS root，发布仍写主 `nas.root`。

状态：符合。注意 manifest 中 `package.url` 必须是 forward-slash 相对路径，不能写 UNC、盘符或 `file://`。

### 2. 打包装配

`assemble-pyinstaller` 生成标准安装根：

- 稳定入口在安装根，例如 `MyTool.exe`。
- GUI release 在 `releases/<version>/`。
- `current.json` 由 UOT 写入安装根。
- 标准 updater sidecar 位于 `updater/`。
- update zip 内容包含 GUI release、`_launcher/` 和 `updater/` sidecar。

状态：符合。工具仓库可调用 UOT 装配或封一层薄 wrapper，但不应手工复制 root updater 或 `_launcher/updater`。

### 3. 发布与版本索引

`uot publish` 写入：

- `<app>/<channel>/latest.json`
- `<app>/<channel>/versions.json`
- `<app>/<channel>/v<version>/.../latest.json`
- `<app>/<channel>/v<version>/.../package.zip`

`versions.json.manifest_url` 是历史版本选择器的权威路径。`list-remote` 和 `show-version` 优先使用索引，并兼容旧版 `<app>/v<version>` 布局。

状态：符合单机发布模型。发布端使用 `publish.lock` 防止同一 app/channel/platform 重复发布写入，并通过同目录临时文件 `replace` 原子提升 package、manifest、channel `latest.json` 和 `versions.json`。

建议：后续可增加发布审计日志和失败恢复报告，便于定位 NAS 中断或权限异常；不需要引入发布服务部署。

### 4. 检查更新与历史版本发现

`DesktopUpdateClient.check()` 读取当前安装根版本后调用 `UpdateService.check()`。`list_remote_versions()` 读取历史版本。配置了 `signature_key` 时，桌面 facade 会在发现阶段校验 manifest 签名，避免 GUI 展示被篡改的版本号或说明。

状态：符合。桌面 facade 与 CLI `check` / `list-remote` / `show-version` / `prepare-version` 均支持签名校验；配置 `--signature-key` 时会在展示或准备包前拒绝被篡改的 manifest。

### 5. 准备包

`prepare()` 通过 manifest 的相对 `package.url` 从 NAS 复制到本地下载目录，复制前后校验 size 和 SHA-256，支持进度回调和取消令牌。`prepare-version` 输出 `package_path` 和 `manifest_path`；调用方必须使用输出值，不应自行拼路径。

状态：符合。下载目录按 app/channel/platform/version 隔离。

### 6. Pending 交接与 updater 启动

`DesktopUpdateClient.install_remote_version()` 写 `pending-update.json`，并启动安装根 `updater/` 下的标准 updater。pending 中包含 package、manifest、install_root、old_pid、force、restart、wait_timeout 和 signature_key。

`StandaloneUpdaterLauncher` 只负责写 pending 并启动 `uot-updater apply`。版本切换和回滚不写 pending，直接启动 `uot-updater switch-installed` 或 `rollback`。

状态：符合。工具仓库不需要拼 updater 参数。

### 7. 安装远端版本

runtime 安装流程：

1. 可选等待旧 GUI 退出。
2. 获取 `update.lock`。
3. 验签 pending manifest。
4. 校验包 size 和 SHA-256。
5. 安全解压 zip，拒绝路径穿越和不安全 symlink。
6. 校验 release 入口。
7. 写入 `releases/<version>`。
8. 提升 `_launcher/` 和 `updater/` sidecar。
9. 切换 `current.json`。
10. 可选重启当前入口。
11. 写 `update-result.json` 和 `update-status.json`。

状态：符合。已覆盖 sidecar 回滚和 `--force` 覆盖失败恢复；current 切换前失败不会留下半安装 release。`update-result.json` 和 `update-status.json` 使用同目录临时文件原子替换，避免 GUI 轮询读到半截 JSON。

### 8. 本地版本切换

`switch-installed` 只切换已经存在于 `releases/<version>` 的版本。它校验 release 入口，持有 `update.lock`，原子写 `current.json`，记录 `previous_version`，支持等待旧进程和重启。

状态：符合。它不下载远端包；远端指定版本应先走 `install_remote_version()` 或 `prepare-version + install-prepared`。

### 9. 回滚

`rollback` 从 `current.json.previous_version` 切回上一版本，使用同一套锁、等待和重启流程。

状态：符合。回滚依赖 previous release 仍存在；如果后续增加 release 清理策略，必须保留 current 与 previous，或清理前写入可恢复策略。

### 10. 进程接管与进度

runtime 支持 `--wait-pid`、`--wait-timeout` 和 `--restart`。旧 GUI 退出后，内存回调自然中断；实时状态通过 `update-status.json` 暴露，下一次 GUI 启动可读取 `update-result.json`。

状态：基础符合。若需要安装期间持续可视化进度，应增加 updater 自有窗口或独立托盘/监控进程，而不是依赖已退出的 GUI。

### 11. 诊断与可观测性

现有状态文件包含 phase、message、version、previous_version、elapsed_ms 和 phase_durations_ms。`doctor` 可收集安装根诊断包。

状态：部分符合。建议补充单机发布审计日志、失败时保留最近 N 次结果、NAS 权限探测详情和 updater stdout/stderr 日志。stale lock 恢复应以显式诊断/清理命令为主，不应隐式删除其他进程可能正在使用的锁。

## 当前企业级差距

P0：暂无已知会直接破坏安装根的阻断问题。

P1：

- `update.lock` 无 stale lock 判定和恢复命令，只能人工或 doctor 辅助处理。

P2：

- 发布端已有 `publish.lock`，单机发布场景下可人工确认后删除 stale lock；后续可补显式 `clear-publish-lock` 辅助命令。
- `rollout_percent` 当前只是 manifest 元数据，未做确定性灰度命中计算。
- 没有 release 保留/清理策略。
- 没有 updater 自带可视化进度窗口。
- same-version cross-channel 本地仍共用 `releases/<version>`，需要强制递增版本或显式 `--force`。
- 没有对 updater exe/launcher exe 做平台代码签名校验；manifest 只能保护包内容。

## 结论

当前 UOT 的运行端更新、版本切换和回滚链路已经接近企业级可用，核心边界也正确：工具库只调用 SDK/facade，不处理版本与 exe 细节。

距离更完整的企业通用在线包，主要差距在客户端 stale `update.lock` 恢复、可视化进度、release 清理策略、灰度命中策略和运维审计。发布侧按单机 CLI 模型继续演进即可，不需要服务部署；这些增强也不应回退到工具仓库实现。
