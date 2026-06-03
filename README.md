# UpdateOnlineTool

企业级跨框架应用更新包统一管理仓库。

## 目录规范

```
<app-id>/
├── stable/latest.json    ← 正式通道 manifest（manifest_url 指向此文件）
├── beta/latest.json      ← 测试通道 manifest（可选）
├── v1.0.4/               ← 历史版本归档
│   └── *.zip
└── v1.0.5/
    └── *.zip
```

## Manifest v2 格式

参见 `manifest-schema.json`，核心字段：

| 字段 | 说明 |
|---|---|
| `app_id` | 项目唯一标识，与子目录名一致 |
| `channel` | `stable` / `beta` / `nightly` |
| `version` | 语义化版本号 |
| `mandatory` | 是否强制升级 |
| `min_supported_version` | 最低支持的当前版本 |
| `published_at` | 发布时间 |
| `notes` | 发布说明 |
| `package.url` | 推荐相对路径；也支持 `https://` / `file://` |
| `package.size` | 文件字节数 |
| `package.sha256` | SHA-256 校验值 |

推荐让 `package.url` 写相对路径，例如：

```json
"package": {
  "url": "automation-manual-studio/v1.0.5/package.zip",
  "size": 113846490,
  "sha256": "..."
}
```

客户端通过 `update-endpoint.json` 的 `package_url_prefix` 拼出实际下载地址，因此同一份 manifest 可以同时用于 GitHub raw、内网 HTTPS 和内网文件路径。

## 新项目接入

1. 创建 `<app-id>/` 子目录和 `stable/` 通道目录
2. 将发布包放入 `vX.Y.Z/package.zip`
3. 生成 `latest.json`（可使用 `_scripts/publish.py`）
4. 客户端 `update-endpoint.json` 的 `manifest_url` 指向 `latest.json`

## 客户端源配置

外网 GitHub raw：

```json
{
  "channel": "stable",
  "manifest_sources": [
    {
      "name": "github",
      "manifest_url": "https://raw.githubusercontent.com/tealiving/UpdateOnlineTool/main/automation-manual-studio/stable/latest.json",
      "package_url_prefix": "https://raw.githubusercontent.com/tealiving/UpdateOnlineTool/main",
      "auth_provider": "anonymous",
      "priority": 20
    }
  ]
}
```

内网文件源：

```json
{
  "channel": "stable",
  "manifest_sources": [
    {
      "name": "intranet-file",
      "manifest_url": "file:///D:/UpdateOnlineTool/automation-manual-studio/stable/latest.json",
      "package_url_prefix": "file:///D:/UpdateOnlineTool",
      "auth_provider": "anonymous",
      "priority": 10
    }
  ]
}
```

客户端安装建议使用用户可写目录，不放入 `Program Files`，以便内网无管理员权限时仍可安装和升级。

## 兼容框架

- **PyQt** — 当前 `http_manifest_client.py` 已原生支持
- **Rust (Tauri)** — 读取同一 JSON，自行实现 updater 逻辑
- **Electron** — 同上，可用 `electron-updater` 适配

## 发布脚本

```bash
# 发布新版本
python _scripts/publish.py --app automation-manual-studio --version 1.0.5 --channel stable --package ./dist/AutomationManualStudio_1.0.5.zip

# 发布前校验
python _scripts/verify_manifests.py --app automation-manual-studio
```

## Qt IFW 发布通道

Qt Installer Framework 仓库用于新安装器、卸载器、组件选择和 MaintenanceTool 在线维护。旧 zip manifest 保留给迁移期旧客户端。

外网 stable 仓库：

```text
https://raw.githubusercontent.com/tealiving/UpdateOnlineTool/main/automation-manual-studio/stable/ifw-repository
```

内网 stable 仓库示例：

```text
file:///D:/tealiving/releases/automation-manual-studio/stable/ifw-repository
```

从主仓库构建并复制 IFW 仓库与安装器：

```powershell
rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File _scripts\build_ifw_release.ps1 `
  -MainRepo D:\tealiving\peoject\AutoMationManual\.worktrees\terminal-definition-aggregate-mode-on-layout `
  -IfwRoot C:\Qt\QtIFW-4.11.0
```

发布前校验 IFW 仓库：

```powershell
rtk proxy python _scripts\verify_ifw_repository.py automation-manual-studio\stable\ifw-repository
```

企业静默安装示例：

```powershell
AutomationManualStudio-internal-offline-setup.exe --root "$env:LOCALAPPDATA\Programs\AutomationManualStudio" install --accept-licenses --confirm-command
```

维护工具检查和更新：

```powershell
AutomationManualStudioMaintenanceTool.exe check-updates
AutomationManualStudioMaintenanceTool.exe update --confirm-command
```
