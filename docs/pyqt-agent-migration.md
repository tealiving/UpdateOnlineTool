# PyQt 迁移到 Agent + Bootstrap

本指南面向已有 PyQt 应用。PyQt 继续只负责窗口、确认、QThread、保存业务状态和
退出；NAS、包校验、安装、版本切换与回滚继续由 UOT 执行。新接入不再启动
`uot-updater` sidecar，而是使用稳定 Bootstrap 与 Update Agent。

## 安装根要求

首次安装根必须包含：

```text
<install-root>/
├── MyToolBootstrap.exe       # 稳定 Bootstrap，不随升级包覆盖
├── agent/uot-agent/uot-agent.exe
├── current.json
└── releases/<version>/MyTool.exe
```

升级 zip 只包含新的 `releases/<version>` 内容；Bootstrap、Agent、`current.json` 和
`operations/` 都属于安装根运行时状态。

## PyQt 交接代码

下载和 Agent ready 可放在 QThread；`confirm_handoff()` 之前必须在主线程或受控保存
回调中完成业务数据落盘。成功 handoff 后立刻调用 `QCoreApplication.quit()`，不要再
等待 UI 回调或自行启动新 exe。

```python
import os
from pathlib import Path

from update_online_tool import DesktopUpdateClient
from update_online_tool.agent import UpdateAgentLauncher, create_apply_request


def prepare_agent_handoff(client: DesktopUpdateClient, version: str) -> tuple[UpdateAgentLauncher, Path]:
    """准备更新并等待 Agent ready。

    :param client: 已按 DesktopUpdateConfig 创建的客户端。
    :param version: 用户确认安装的远端版本。
    :return: Agent launcher 和 request 路径。
    """
    root = client.install_root()
    prepared = client.prepare_remote_version(
        version,
        old_pid=os.getpid(),
        restart=False,
    )
    launcher = UpdateAgentLauncher(root / "agent" / "uot-agent" / "uot-agent.exe")
    request = create_apply_request(
        install_root=root,
        pending_path=prepared.pending_manifest_path,
        old_pid=os.getpid(),
        bootstrap_command=(
            str(root / "MyToolBootstrap.exe"),
            "launch",
            "--install-root",
            str(root),
        ),
    )
    started = launcher.start(request)
    return launcher, started.request_path
```

调用方随后执行：保存业务数据 → `launcher.confirm_handoff(request_path)` →
`QCoreApplication.quit()`。若 ready、保存或 handoff 任一步失败，应用必须继续运行并
展示 `UpdateError.code.value` 与 `UpdateError.message`；不要退出。

## 验收

在干净安装根验证 `vN → vN+1`，并确认：Agent status 为 `success`、
`current.json.version` 已切换、Bootstrap 启动新 release。再验证本地版本切换、回滚和
`uot doctor --install-root <root> --archive diagnostics.zip` 的 operation 证据。
