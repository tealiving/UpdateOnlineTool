# UOT 多桌面运行时架构

UOT 的更新内核保持 framework-agnostic：NAS 发布根、manifest、签名、包
校验、`releases/<version>`、`current.json`、稳定 Bootstrap、Update Agent、回滚和诊断均不
依赖 PyQt、Electron 或 Tauri。框架差异只能存在于构建产物识别、GUI 调用和
稳定启动入口三个适配层。

## 稳定契约

远端继续使用 schema v2 manifest 和现有 NAS 布局：

```text
<nas>/<app>/<channel>/latest.json
<nas>/<app>/<channel>/versions.json
<nas>/<app>/<channel>/v<version>/package.zip
```

`platform` 仅表示 `windows`、`macos` 或 `linux`；不要把 Electron/Tauri
写入平台值。运行框架属于本地装配配置，不改变远端协议。

每个新 release 应在 release 根携带 `uot-release.json`。它绑定 `app_id`、
`version`、`platform`、入口和必需资源路径；UOT 会在安装、切换和回滚前校验。
历史 release 可以没有该文件，但宿主仍必须通过 bridge 的
`release_required_paths` 声明 settings、bridge 等不可缺少的资源。

稳定 Bootstrap/Agent 模式下，更新 zip 的根目录只能是可直接安装到
`releases/<version>` 的 release：

```text
package.zip
├── <entry path>       # .exe、.app 或 Linux 可执行文件
└── <runtime resources>
```

Bootstrap 和 Agent 固定在安装根，不能由 release 包提升或覆盖。旧
`_launcher/`、`updater/` sidecar 仅是迁移期 legacy 装配模式，不属于新的多运行时
契约。

工具项目不得自行读取 NAS、修改 `current.json`、构造 updater 命令或替换
release 文件。

## 通用装配层

`release_assembly.py` 定义 `ReleaseAssemblyConfig` 和
`assemble_release_layout()`。它只要求：

- release 目录和其入口路径；
- launcher 目录和其入口路径；
- 目标平台、版本和应用标识；
- 可选 Agent bundle 与 release 资源文件。

它生成标准安装根、升级目录和 `current.json`。启用
`bootstrap_agent_mode` 时，`launcher_dir` 作为 Bootstrap 复制到安装根，
`agent_bundle` 复制到 `install_root/agent/`，而升级目录不再包含
`_launcher/` 或 `updater/`。现有
`assemble-pyinstaller` 先校验 PyInstaller `_internal` 结构，再委托该通用层，
因此既保持旧 CLI 兼容，也为 Electron/Tauri 复用同一安装/回滚 runtime。

## 运行时适配层

| 适配器 | 构建输入 | UOT 调用方 | 当前状态 |
| --- | --- | --- | --- |
| PyInstaller | onedir 或 macOS `.app` | Python `DesktopUpdateClient` | 已支持 |
| Electron | `electron-builder --dir` 的解包目录 | Node Main Process + `uot-bridge` | 已有参考 bridge 流程；打包与签名由应用负责 |
| Tauri | 经验证的可运行 staging 目录 | Rust command | 已完成 macOS NAS+Agent+Bootstrap 基线；Windows/Linux 资源布局待验收 |

Electron 与 Tauri 不直接复用各自的 HTTP updater。它们应调用同一个
`uot-bridge`：`check`、`prepare`、`agent-start`、`agent-switch`、
`agent-rollback`、`agent-handoff`、`status` 和 `result`。bridge 使用 UOT Core，
避免在 Python、Node、Rust 中重复实现 NAS、验签和安装。

## JSON Bridge 契约

`uot-bridge` 是本地子进程，不是服务或 MCP。每次调用只输出一行 JSON，成功
输出包含 `ok: true`，失败输出到 stderr 并包含 UOT 结构化错误码。它只创建、
启动和确认交接，不取代 Bootstrap 或 Update Agent。

宿主项目提供一个不含密钥的配置文件：

```json
{
  "app_id": "my-app",
  "install_root": "/Applications/MyApp",
  "settings_path": "/Applications/MyApp/releases/1.2.0/MyApp.app/Contents/Resources/uot/settings.json",
  "platform": "macos",
  "channel": "stable",
  "signature_key": "/Applications/MyApp/releases/1.2.0/MyApp.app/Contents/Resources/uot-signing.pub",
  "agent_executable": "/Applications/MyApp/agent/uot-agent/uot-agent",
  "bootstrap_command": ["/Applications/MyApp/uot-bootstrap", "launch", "--install-root", "/Applications/MyApp"],
  "agent_ready_timeout": 30,
  "release_required_paths": [
    "MyApp.app/Contents/Resources/uot/settings.json",
    "MyApp.app/Contents/Resources/uot/uot-bridge/uot-bridge"
  ]
}
```

典型调用顺序：

```text
uot-bridge check --config uot-bridge.json
uot-bridge prepare --config uot-bridge.json --version 1.2.0 --old-pid <pid>
uot-bridge agent-start --config uot-bridge.json --old-pid <pid>
# Agent 已写入 ready；宿主保存数据后：
uot-bridge agent-handoff --config uot-bridge.json --request <request-path>
<宿主退出；Agent 等待 PID、安装、切换并启动 Bootstrap>
```

`prepare` 不启动 Agent；它只复制并校验 package、写入 `pending-update.json`。
`agent-start` 会写入带唯一 `operation_id` 的 request，并等待 Agent 写入 `ready`
状态。只有在宿主完成保存后，才能调用 `agent-handoff`；Agent 随后等待旧 PID、
执行 UOT Core 事务，并只重启稳定 Bootstrap。Renderer/WebView 不得直接执行
bridge，Electron 必须经过 Main Process IPC，Tauri 必须经过受控 Rust command。

`operation_id` 只能使用最长 128 字符的字母、数字、下划线和连字符，且 request
必须恰好位于 `<install-root>/operations/<operation-id>.request.json`。Agent 与
handoff 都会重复校验该路径，不接受宿主传入的任意文件路径。

本地版本切换使用 `agent-switch --version <version>`，回滚使用
`agent-rollback`；它们与远端安装复用同一 ready/handoff 和 Bootstrap 重启
Interface，而不是让宿主直接修改 `current.json`。

`uot-agent` 仍是用于验证 JSON Interface 与状态机的 Python 参考实现。稳定
`uot-bootstrap` 已有 Rust 原生实现，源代码和构建说明位于
`native/uot-bootstrap/`；它保持 `launch --install-root` 和 JSON 输出契约，只读取
`current.json` 并启动 active release。其 `release_dir` 限制为
`releases/<version>`，解析后的 release 和入口必须留在该目录内，以拒绝路径或
符号链接逃逸。生产 Rust Agent 必须保持 request、status、NAS schema 和宿主调用
顺序不变，并完整实现或受控调用同一 UOT Core 安装事务；不得仅以“启动 Python
脚本”的包装器冒充原生事务。

## 分阶段交付

1. 通用 release 装配层：保持 PyInstaller 兼容，并用模拟 Electron release 验证。已完成。
2. Agent request、ready/handoff、安装/切换/回滚与 JSON bridge。已完成 Python 参考实现。
3. 稳定 `uot-bootstrap`：读取 `current.json` 并启动当前 release。已完成 Python 参考实现。
4. Electron 参考流程：启动后后台检查、用户确认、两阶段进程交接、安装与回滚。进行中。
5. Electron macOS/Linux：由应用补齐代码签名、公证、权限和 bundle 验证。
6. Rust 原生 Bootstrap：已完成 macOS arm64 构建与真实 Electron 交接；Windows/
   Linux 编译和签名验收待完成。
7. Rust 原生 Agent：按 [ADR-0004](adr/0004-defer-unified-rust-agent.md) 的量化门槛
   决定是否立项；一旦实施必须保持 JSON Interface，并完整复用或实现 UOT Core 事务，
   不以包装器替代。
8. Tauri：macOS 基线已完成；补齐 Windows/Linux 资源布局、签名和真实 NAS 验收。

## macOS Electron 验证基线

完整 Electron 包已通过一次真实进程交接：旧主进程经 `agent-start` 进入 ready、
宿主写入 handoff 并退出，Agent 安装并切换 `current.json`，随后 Bootstrap 启动
新 release。一次约 342 MB 全量包的事务耗时约 20.2 秒，其中解压约 18.2 秒，
`current.json` 切换约 17 ms。这说明后续优化应优先针对包体、解压策略和增量
交付，而不是重新引入 Electron 主进程自管理重启。

Rust `uot-bootstrap` 已在同一外挂盘环境完成 `1.0.2 → 1.0.3` 真实交接：
Python Agent 的 request 直接启动安装根中的 arm64 Rust Mach-O，Agent status 写入
Bootstrap PID，新 Electron 主进程由 `releases/1.0.3` 启动。该次运行时总耗时约
17.9 秒，解压约 16.8 秒，说明原生 Bootstrap 没有成为延迟来源。

## 构建与签名边界

原生 Bootstrap 需要与稳定安装根一起签名、分发和公证，不能放进 release zip。
Electron/Tauri 的完整 `.app` 也应先在 APFS 构建机或 CI 上生成并完成签名/公证，
然后复制到 NAS。外接盘重建时出现的 `electron-builder` ASAR header 错误属于
构建输入/文件系统问题，不能归因于 UOT 更新事务，也不能用“复用旧 release”
替代正式发布流程。

## 启动时检查策略

应用 ready 后在后台执行检查，不阻塞主窗口。NAS 不可用时记录状态并继续启动；
可选更新需用户确认，只有 `mandatory` 或不满足 `min_supported_version` 时才阻止
继续进入业务。安装必须由主进程/Rust command 协调保存工作和退出，随后由
`uot-agent` 完成等待、切换，并重启 Bootstrap。
