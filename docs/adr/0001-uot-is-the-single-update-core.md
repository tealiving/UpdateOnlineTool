# ADR-0001: UOT 是唯一更新内核

## 状态

已采纳。

## 决策

UOT 独占 NAS 协议、manifest 签名、包校验、安装事务、`current.json`、回滚和诊断。
PyQt、Electron 与 Tauri 仅通过 SDK facade 或 `uot-bridge` 接入。Electron Renderer
和 Tauri WebView 不得访问 NAS、拼更新命令或修改安装根状态。

## 后果

宿主只保留 UI、IPC、保存业务数据和退出协调。不得并行启用 Tauri Updater、
Electron autoUpdater 或其他会自行下载、切换和重启 release 的运行时。
