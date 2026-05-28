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

## Manifest 格式

参见 `manifest-schema.json`，核心字段：

| 字段 | 说明 |
|---|---|
| `app_id` | 项目唯一标识，与子目录名一致 |
| `channel` | `stable` / `beta` / `nightly` |
| `version` | 语义化版本号 |
| `package.url` | 支持 `file://` / `https://` / `http://` |
| `package.sha256` | SHA-256 校验值 |

## 新项目接入

1. 创建 `<app-id>/` 子目录和 `stable/` 通道目录
2. 将发布包放入 `vX.Y.Z/` 目录
3. 生成 `latest.json`（可使用 `_scripts/publish.py`）
4. 客户端 `update-endpoint.json` 的 `manifest_url` 指向 `latest.json`

## 兼容框架

- **PyQt** — 当前 `http_manifest_client.py` 已原生支持
- **Rust (Tauri)** — 读取同一 JSON，自行实现 updater 逻辑
- **Electron** — 同上，可用 `electron-updater` 适配

## 发布脚本

```bash
# 发布新版本
python _scripts/publish.py --app automation-manual-studio --version 1.0.5 --channel stable --package ./dist/AutomationManualStudio_1.0.5.zip
```
