# ADR-0003: 默认 UOT Release，原生安装包使用 Delivery Adapter

## 状态

已采纳，`native_installer` 尚未实现。

## 决策

默认交付是 `uot_release`：Agent 安装 release、原子切换 `current.json`，再由稳定
Bootstrap 启动。未来 MSI、NSIS、PKG、deb 等原生安装包只能作为 UOT 的类型化
`native_installer` Delivery Adapter；不得把安装器路径、参数或 shell 命令交给 GUI
自由拼接。

## 后果

UOT 仍负责 manifest、签名、下载、哈希、操作状态与旧进程交接。平台 adapter 只负责
受控安装器启动、权限策略、取消和安装后版本验证；Tauri/Electron 内置 Updater 不会
重新成为第二个更新内核。
