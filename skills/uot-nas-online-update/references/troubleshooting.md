# UOT NAS 桌面应用常见问题排查

> 新的 Bootstrap/Agent 模式优先检查 `operations/<operation-id>.status.json`、
> `update-status.json`、`update-result.json` 和 stable Bootstrap 日志。本文的
> `uot-updater`、launcher 与 PyInstaller 条目仅适用于 legacy 兼容路径。

## 目录

- [NAS 无法访问](#nas-无法访问)
- [`uot init` 没有生成项目配置](#uot-init-没有生成项目配置)
- [GUI 检查不到新版本](#gui-检查不到新版本)
- [升级包校验失败](#升级包校验失败)
- [启动安装根没有 GUI](#启动安装根没有-gui)
- [升级后没有切换版本](#升级后没有切换版本)
- [用户要修改 NAS 路径](#用户要修改-nas-路径)
- [不应放回 PyQt 项目的通用代码](#不应放回-pyqt-项目的通用代码)

## NAS 无法访问

检查：

- `config/settings.json` 中的 NAS 根路径是否正确。
- 如果配置了 `nas.roots`，确认至少一个候选路径在当前网络下可访问；UOT 会按顺序使用第一个可访问路径。
- 当前系统用户是否能在文件管理器或 shell 中读取该路径。
- 当前系统用户是否能写入、读取并删除临时文件。
- Windows 使用 UNC 路径或已登录 SMB 会话；macOS 通常使用 `/Volumes/...` 挂载卷。
- `nas.root`/`nas.roots` 支持普通 UNC、中文、空格和 `file://` URI；JSON 中 UNC 反斜杠必须写成 `\\`。
- UNC、`file://` 或挂载路径只应出现在 `nas.root`/`nas.roots` settings；manifest `package.url` 必须是 `/` 风格相对路径，不能写 UNC、盘符、`file://` 或反斜杠路径。
- 如果是真实 SMB 路径，确认当前用户已有 SMB 会话、凭据管理器凭证或钥匙串凭证。

UOT 不应保存 NAS 用户名和密码。Windows 使用系统凭证或当前 SMB 会话；macOS 使用已挂载卷或钥匙串。

## `uot init` 没有生成项目配置

检查命令是否在项目根目录执行，或显式传入了输出路径。项目默认应生成：

```text
update-endpoint.json
config/settings.json
```

不要把配置写到 pip 安装后的 UOT 包目录。

## GUI 检查不到新版本

检查：

- GUI 是否读取了正确的 `update-endpoint.json`。
- `settings_file` 是否指向项目本地 `config/settings.json`。
- `uot check --settings <settings_file> --app <app_id> --current-version <current_version>` 是否能看到目标版本。
- NAS `<app_id>/<channel>/latest.json` 或 `<app_id>/<channel>/<platform>/latest.json` 是否指向目标版本。
- 当前版本号是否低于 NAS 最新版本。

## 升级包校验失败

检查：

- `uot verify --settings <settings_file> --app <app_id>` 是否通过。
- NAS `<app_id>/<channel>/v<version>/` 版本目录中的 `package.zip` 是否完整；平台发布时检查 `<app_id>/<channel>/v<version>/<platform>/package.zip`。
- `latest.json` 中的 size 和 SHA-256 是否与包一致。
- 发布时是否重新压缩过包但没有重新 publish。

## 启动安装根没有 GUI

常见原因是 PyInstaller 产物中 GUI 和 launcher 名称冲突，导致 release 目录里的 GUI 入口实际上是 launcher。

检查：

- release 目录中的 `<app_exe>` 或无 `.exe` 入口文件大小是否异常。
- release 目录是否生成了 launcher 日志。
- spec 中 GUI 构建名和 launcher 构建名是否分离。
- 非 Windows 装配是否传入了 `uot assemble-pyinstaller --platform macos` 或 `--platform linux`。

修复方式：

- 重新构建 PyInstaller。
- 重新执行 UOT 装配。
- 不要只手工移动 exe 或手写 `current.json`。

## 升级后没有切换版本

检查安装根：

```text
current.json
update-result.json
logs/update.log
logs/launcher.log
releases/<target_version>/<app_exe>
```

判断：

- `update-result.json.success = false`：优先看 updater 日志。
- `current.json.version` 未变化：检查 updater 是否成功写入安装根。
- `current.json.version` 已变化但 GUI 仍旧：检查 launcher 是否读取安装根而不是 release 根。
- `current.json.entry.platform` 与目标平台不一致：重新用正确的 `--platform` 装配。

## 用户要修改 NAS 路径

开发和打包默认值改项目内：

```text
config/settings.json
```

安装后的用户级覆盖可以使用项目约定的用户配置路径。不要修改 UOT 依赖包源码。

如果内网和外网 NAS 都可能使用，优先在 settings 中配置：

```json
{
  "nas": {
    "root": "/mnt/internal-nas/SmartIngest",
    "roots": [
      "/mnt/internal-nas/SmartIngest",
      "/Volumes/SmartIngestNAS"
    ]
  }
}
```

读取操作会自动选择第一个可访问路径；发布操作仍写主 `nas.root`，避免误把正式包发布到临时 fallback 路径。

## 不应放回 PyQt 项目的通用代码

这些能力应由 UOT 承担：

- 通用 manifest 生成。
- 通用 NAS 下载或复制。
- 通用 SHA-256 校验。
- 通用 PyInstaller release/launcher 装配。
- 通用 settings 解析。
- 通用 NAS 凭证策略。
