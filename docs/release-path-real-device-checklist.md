# Release 路径三平台真机验收清单

## 目的与通过标准

本清单验证 `ReleasePackagePlan` 的平台预算和实际文件系统行为一致。三平台必须
使用同一提交、同一组 ZIP fixture，并从公开入口执行
`publish → verify → prepare → Agent handoff → Bootstrap`。GUI、Bridge、Agent
和宿主不得增加自己的路径白名单或解压逻辑。

每组验收都必须记录操作系统构建号、文件系统、安装根绝对路径、UOT 提交、
Python/Rust Bootstrap 版本、命令、退出码、结构化错误码及诊断包路径。只有以下
条件全部满足才可通过：

- 合法中文、空格和正常深度目录可发布、准备、安装并由 Bootstrap 启动；
- 保留名、尾随点/空格、ADS、大小写冲突、NFC/NFD 冲突在写 release 前拒绝；
- 超长成员返回 `PACKAGE_PATH_TOO_LONG`，不能退化为 `OSError` 或乱码异常；
- 失败时 `current.json` 不变，Bootstrap 不启动，不留下 `.update-*`、
  `.release-backup.*`、`.sidecar-backup.*` 或包复制临时文件；
- `doctor` 能收集 request、handoff、Agent status、runtime status/result。

## 固定 Fixture

| Fixture | ZIP 成员示例 | 预期 |
| --- | --- | --- |
| `zh-valid` | `MyTool.exe`、`资源/更新说明.txt` | 成功，中文内容保持一致 |
| `case-collision` | `Config.json`、`config.json` | `PACKAGE_LAYOUT_INVALID` |
| `unicode-collision` | `资源/é.txt`、`资源/e◌́.txt` | `PACKAGE_LAYOUT_INVALID` |
| `windows-reserved` | `CON.txt` | `PACKAGE_LAYOUT_INVALID` |
| `windows-ads` | `config.json:secret` | `PACKAGE_LAYOUT_INVALID` |
| `component-overflow` | 260 个 `a` 加 `.txt` | `PACKAGE_PATH_TOO_LONG` |
| `relative-overflow` | 多层中文目录使相对路径超过 1024 UTF-8 bytes | `PACKAGE_PATH_TOO_LONG` |
| `boundary-valid` | 使 staging 目标路径刚好不超过平台预算 | 成功 |
| `boundary-invalid` | 比平台预算多 1 个单位 | `PACKAGE_PATH_TOO_LONG` |

`boundary-*` 必须基于本次运行的实际 staging 路径计算，不能只按 release 最终路径
估算。Windows 的检查对象为：

```text
<install-root>\.update-<version>.<pid>.<32-hex>.tmp\<member>
```

当前可移植性合同的硬上限是 Windows 259 UTF-16 code units、macOS 1023 UTF-8
bytes、Linux 4095 UTF-8 bytes；单个组件同时不得超过 255 UTF-8 bytes 或 255
UTF-16 code units，相对路径不得超过 1024 UTF-8 bytes。

## Windows long-path 开/关矩阵

先记录系统开关，不要在未获运维授权时修改组策略或注册表：

```powershell
Get-ItemPropertyValue `
  -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
  -Name LongPathsEnabled
```

同时记录实际运行的 `uot.exe`、`uot-agent.exe` 和 Bootstrap 是否带
`longPathAware` manifest。注册表值为 `1` 但进程 manifest 未声明时，不能记为
“long path 已启用”。

| 场景 | `LongPathsEnabled` | `longPathAware` | UOT 预期 |
| --- | ---: | --- | --- |
| 关闭 | 0 | 任意 | `boundary-valid` 成功，超出 259 的 fixture 由 Core 预先拒绝 |
| 仅系统开启 | 1 | 否 | 与关闭场景相同 |
| 系统与进程均开启 | 1 | 是 | 仍执行 259 可移植基线，不因系统能力放宽发布合同 |

long-path 开关不改变 UOT 的跨机器发布合同。它只能影响合同之外的其他文件系统
操作；若三种状态返回不同 UOT 错误码，视为回归。

Windows 每种状态执行：

```powershell
python -m pytest `
  tests/test_release_package.py `
  tests/test_runtime.py `
  tests/test_agent.py `
  tests/test_bridge_cli.py -q

uot verify --settings <settings.json> --app <app-id> --platform windows
uot doctor --install-root <install-root> `
  --output <evidence-dir>\doctor.json `
  --archive <evidence-dir>\doctor.zip
```

## macOS 真机清单

- 记录 `sw_vers`、`uname -m` 和 `diskutil info <install-volume>` 的文件系统信息；
- 分别在默认大小写不敏感 APFS 与可用的 case-sensitive 卷执行 fixture；
- 验证合法中文名称内容一致，NFC/NFD 等价成员不能发生静默覆盖；
- 执行一次真实旧进程退出、Agent handoff、`current.json` 切换和稳定 Bootstrap
  拉起；
- 归档 `doctor.json`、`doctor.zip` 和测试输出。

## Linux 真机清单

- 记录发行版、内核、挂载点和 `findmnt -no FSTYPE,OPTIONS <install-root>`；
- 在 case-sensitive 文件系统执行完整 fixture；
- 即使底层允许 `Config.json` 与 `config.json` 共存，UOT 仍必须返回
  `PACKAGE_LAYOUT_INVALID`；
- 验证中文 locale 为 UTF-8，并执行真实 Agent/Bootstrap 交接；
- 归档 `doctor.json`、`doctor.zip` 和测试输出。

## 结果记录

| 平台/状态 | 文件系统 | 安装根 UTF-8/UTF-16 长度 | 合法中文 | 冲突拒绝 | 长度边界 | Agent/Bootstrap | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Windows / long-path 关闭 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待执行 |
| Windows / long-path 开启、manifest 关闭 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待执行 |
| Windows / long-path 开启、manifest 开启 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待执行 |
| macOS 26.2 arm64 / 默认卷 | APFS，大小写不敏感，NFD/NFC 别名 | pytest `tmp_path` 动态实测 | 通过 | 通过 | 通过 | 真实进程 handoff 自动化通过 | 2026-07-27：路径/runtime/Agent/Bridge 71 passed | 自动化基线通过，正式 release 待验 |
| macOS / case-sensitive 卷 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待执行 |
| Linux / case-sensitive | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待执行 |

真机证据未填满前，RP-10 只能标记为“清单已建立”，不能宣称三平台验收完成。

本次 macOS 基线运行于 macOS 26.2（Build 25C56）、arm64、内部
`Macintosh HD - Data` APFS 可写卷。临时探针确认该卷大小写不敏感，NFD 名称
可命中 NFC 文件；随后执行 `test_release_package.py`、`test_runtime.py`、
`test_agent.py` 和 `test_bridge_cli.py`，共 71 项通过。该结果覆盖真实子进程
handoff 自动化，但不替代正式签名 release、真实 NAS 和独立 case-sensitive 卷
验收。
