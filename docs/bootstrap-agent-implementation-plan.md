# Bootstrap 与 Update Agent 实施任务流

本计划将 UOT 从“宿主启动 updater 并自行退出”收口为稳定 Bootstrap 和独立
Update Agent。NAS、manifest、签名、包校验、安装、切换、回滚仍由 UOT Core
负责；Electron、Tauri、PyQt 仅负责用户确认、保存工作和受控调用。

## 执行队列

| 编号 | 任务 | 验收条件 | 状态 |
| --- | --- | --- | --- |
| P0-01 | 固定 Agent request、状态和操作 ID 契约 | 可持久化、无密钥、可诊断 | 已完成 |
| P1-01 | 实现 Bootstrap/Agent 的最小 Python 参考运行时 | Agent 先 ready，再等待旧 PID，最终启动 Bootstrap | 已完成 |
| P1-02 | 增加 `uot-agent`、`uot-bootstrap` 窄 CLI | 可由打包产物独立调用 | 已完成 |
| P2-01 | bridge 改为“prepare → Agent ready”并统一安装、切换、回滚 | 三条路径均经 ready/handoff 并重启 Bootstrap | 已完成 |
| P2-02 | 通用 release 装配接入稳定 Bootstrap/Agent | release 包不能覆盖稳定入口 | 已完成 |
| P3-01 | macOS Electron、Windows、Linux 真实进程交接验收 | 新版本只经 Bootstrap 启动 | 进行中 |
| P3-02 | Agent 故障注入与诊断证据闭环 | runtime 失败不重启 Bootstrap；doctor 可归档 request/handoff/status | 已完成 |
| P3-03 | Rust 原生 Bootstrap | 保持 `current.json` 与 JSON CLI 契约；macOS Electron 真机交接 | 已完成 |
| P4-01 | 一次性迁移完成后删除旧兼容 | 无 legacy NAS、`_launcher`、迁移包运行时代码 | 待开始 |

## 固定数据流

`prepare` 下载并验签后写入 `pending-update.json`。宿主创建带唯一
`operation_id` 的 request，并启动 Agent。Agent 写入 `ready` 状态后，宿主才
能退出；Agent 等待旧 PID、执行 UOT Core 安装事务、写结果，最后启动
Bootstrap。Bootstrap 读取 `current.json` 并启动 active release。

P1 使用 Python 参考实现验证 Interface 和状态机；生产阶段以同一 JSON
Interface 替换为 Rust 原生二进制。该替换不得改变 NAS schema、manifest、
`current.json` 或宿主调用顺序。

稳定运行时模式通过 `ReleaseAssemblyConfig.bootstrap_agent_mode` 或
`uot assemble-pyinstaller --bootstrap-agent-mode --agent-bundle <path>` 启用。
Bootstrap 和 Agent 只复制到安装根；`package.zip` 仅包含 versioned release，
因此新包不能覆盖正在运行的 Agent 或稳定入口。

Python 参考产物可通过 `uot write-agent-spec --output-dir <dir>` 与
`uot write-bootstrap-spec --output-dir <dir>` 生成 PyInstaller spec。生产切换为
Rust 原生二进制时，只替换这些 bundle，不改变 bridge 配置或 Agent request。

Rust 原生 Bootstrap 已位于 `native/uot-bootstrap/`。它仅接受
`launch --install-root <root>`，读取并校验 `current.json` 的相对
`release_dir`/`executable`，其中 release 必须是 `releases/<version>`，解析后的
release 与入口不得经符号链接逃出 active release；在 macOS 用 `open -n` 启动
`.app`，其他平台直接启动可执行文件。它不访问 NAS、不安装包、不改状态；原生
Agent 必须完整实现或安全调用同一 UOT Core 事务后才能替换 Python Agent。

## 当前验收记录

macOS Electron 完整包基线已通过：旧主进程退出后，Agent 完成 `1.0.0 → 1.0.2`
安装并由 Bootstrap 启动 `releases/1.0.2`。该次事务总耗时约 20.2 秒，其中旧
进程等待约 1.8 秒、校验约 0.1 秒、完整 Electron 包解压约 18.2 秒、
`current.json` 切换约 17 ms。Windows、Linux、签名/公证和 Rust 原生二进制
仍是 P3 后续验收项。

Rust Bootstrap 已在外挂盘的 macOS Electron 环境完成第二次交接：`1.0.2 →
1.0.3`。operation request 的 `bootstrap_command` 指向安装根 `uot-bootstrap`
arm64 Mach-O，Agent status 记录 `bootstrap_pid`，`current.json` 已切到
`1.0.3`，实际 Electron 主进程来自 `releases/1.0.3`。事务总耗时约 17.9 秒，
其中旧 PID 等待约 0.6 秒、校验约 0.2 秒、解压约 16.8 秒、切换约 29 ms。

本次外接盘尝试通过 `electron-builder --mac dir --arm64` 重建测试包时，在
electron-builder 26.15.3 的 ASAR header 读取阶段出现
`chromium-pickle-js` offset 错误；这是构建环境/ASAR 输入问题，未进入 UOT
Agent、Bootstrap 或安装事务。正式流程应在 APFS 构建机或 CI 生成并签名/公证
完整 `.app`，再将封装产物复制到 NAS 或外接盘，不能把本次复用已验证 release
的测试包作为生产构建方案。

## 清理门槛

P4 之前不得删除现有兼容代码。完成一次受控 `0.2.x → 1.0` 迁移、跨平台
故障注入和回滚验收后，删除 legacy 全局 NAS 布局扫描、`_launcher` sidecar、
长期迁移命令、install-root 自动向上纠正和生产 HMAC 验签路径。
