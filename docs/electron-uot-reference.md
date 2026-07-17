# Electron + UOT 受控接入参考

本参考适用于 Electron Main Process。它不使用 `autoUpdater`，也不让 Renderer
访问 NAS、调用 `uot-bridge`、修改 `current.json` 或自行重启版本化应用。

## 文件职责

将 [示例控制器](../examples/electron-uot-host/uot-host.mjs) 复制到 Electron 主进程层，
由主进程定位已打包的 `uot-bridge` 与其无密钥配置文件。preload 只暴露
`check`、`install`、`switchInstalled`、`rollback`、`status` 等固定 IPC；不要向
Renderer 暴露 bridge 路径、任意参数或 `child_process`。

```text
Renderer → preload 固定 IPC → Main Process controller → uot-bridge
                                                     → Agent → Bootstrap
```

## 安装顺序

`controller.install(version)` 严格按以下顺序执行：

1. `prepare --version <version> --old-pid <Electron PID>` 下载并校验 package；
2. `agent-start` 等待 Agent 写入 ready；
3. `beforeHandoff` 保存业务状态；
4. `agent-handoff --request <request-path>`；
5. `app.exit(0)` 结束旧主进程。

只有 Agent ready 和 handoff 都成功后才能退出。失败时控制器抛出 UOT 结构化错误，
Electron 继续运行，供 UI 展示重试或诊断入口。

## 验证

```bash
node --test examples/electron-uot-host/uot-host.test.mjs
```

该测试验证无 shell 子进程、交接顺序和失败不退出。实际发布仍需在目标平台完成
`vN → vN+1` NAS 进程交接、签名/公证和回滚验收。
