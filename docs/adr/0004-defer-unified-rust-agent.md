# ADR-0004: 暂缓统一 Rust Agent，以 JSON 契约实现跨语言兼容

## 状态

已采纳；未来按量化门槛重新评估。

## 背景

当前 `uot-agent` 是 PyInstaller onedir 的 Python 参考实现，稳定 Bootstrap 已采用
Rust。Electron、Tauri 和 PyQt 通过 facade 或 `uot-bridge` 使用同一份 request、status、
handoff、manifest、release contract 与 `current.json` 契约。

已验证的 macOS Electron 全包事务约为 17.9 至 20.2 秒，其中约 16.8 至 18.2 秒是
release 解压；现有证据没有显示 Python Agent 是主要延迟来源。

## 决策

不为每个宿主语言分别实现更新内核，也不立即用 Rust 重写 Agent。继续以打包的 Python
Agent 作为默认实现，Rust Bootstrap 作为稳定启动入口；跨语言兼容由 JSON bridge 契约而
非语言绑定实现。

只有满足至少一项门槛才立项统一 Rust Agent：

- 已签名的目标平台包中，Agent ready 的 p95 超过 10 秒且排除首包解压、NAS 复制和宿主保存时间；
- 至少两个已验收平台显示 Agent 自身耗时超过整次事务的 15%；
- 企业安全、部署或审计政策明确禁止随包分发 Python runtime。

## 后果

未来 Rust Agent 必须一次性服务 Electron、Tauri、PyQt 与其他宿主，并保持现有 JSON
schema、错误码、锁、签名校验、原子 `current.json` 切换、回滚和诊断行为。不能仅通过
Rust 进程再启动 Python 来宣称“原生 Agent”，也不能让某个宿主重新实现 NAS 或安装事务。

在门槛满足前，优化重点放在包体、解压、签名、公证和目标平台验收。
