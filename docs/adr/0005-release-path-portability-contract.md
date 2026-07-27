# ADR-0005：Release 路径可移植性合同

## 状态

已采纳。

## 背景

旧 runtime 只拒绝 ZIP 绝对路径和 `..`，dry-run 与真实解压分别遍历中央目录。
这不足以覆盖 Windows 保留名、尾随空格/点、ADS 冒号、单组件或完整路径过长、
大小写冲突以及 Unicode NFC/NFD 等价冲突。不同文件系统可能在解压时失败，也
可能静默覆盖先前成员。

发布标识和 `current.json` 路径此前也由各入口自行拼接。CLI 虽然是主要入口，
但 SDK、Agent、Bootstrap 和本地切换仍可能绕过 CLI。

## 决策

1. `release_identity.py` 是 app、channel、platform、version、包文件名和受控
   相对路径的唯一词法合同。合同使用 NFC 规范化，允许合法中文，拒绝路径分隔
   符、控制字符、Windows 非法字符/保留名和非规范版本。
2. NAS、manifest、runtime、本地切换以及 Python/Rust Bootstrap 都在自己的
   信任边界重新验证；GUI、Bridge 和宿主不得复制这些规则。
3. `ReleasePackagePlan` 在创建解压目录前一次读取 ZIP 中央目录，生成不可变
   成员计划。dry-run 和真实解压复用同一计划，不再各自解释成员名。
4. 冲突键使用 `NFC + casefold`。大小写等价、NFC/NFD 等价、重复成员和
   文件/目录结构冲突统一返回 `PACKAGE_LAYOUT_INVALID`。
5. 单组件按 UTF-8 字节与 UTF-16 code unit 双重检查；完整相对路径和目标平台
   解压路径按明确预算检查，超限返回 `PACKAGE_PATH_TOO_LONG`。
6. `package-release` 生成临时 ZIP，计划校验通过后才原子替换目标包；
   `publish` 同时预检源包并在 NAS 临时副本提升前复验。安装时，manifest
   大小/hash、ZIP 计划和解压必须绑定同一个打开文件；解压完成后再次校验该
   文件，禁止“hash 通过后替换包”的 TOCTOU。
7. 旧 manifest 未声明 `platform` 时，完整目标路径预算使用实际运行宿主，而非
   默认为最宽松平台；symlink 目标正文同样受 1024-byte 可移植相对路径预算。
8. 新 manifest 和 `uot-release.json` 的 `platform` 只能是 `windows`、`macos`
   或 `linux`。历史 `current.json` 可读取既有平台别名，但切换或回滚后必须
   写回规范值；历史 release 可以没有 `uot-release.json`，安全路径与入口校验
   不得因此跳过。
9. ZIP 计划拒绝空包，并限制成员数量、单成员/总解压大小和压缩比。普通入口
   必须是文件，`.app` 必须包含 `Contents/MacOS/` 下的文件，dry-run 与真实
   安装使用同一入口类型合同。
10. ZIP 成员和 symlink 目标统一使用 NFC；内置打包器必须保留 symlink，不得
    静默解引用。
11. `publish` 的每个文件仍使用同目录临时文件原子替换；同一进程内任一步失败
    时恢复 package、版本 manifest、通道 latest 和版本索引的旧快照。进程崩溃
    后遗留的 `.backup` 是恢复证据，不把 SMB 多文件写入宣称为分布式事务。

## 后果

- 中文名称本身不再被当作异常；规范等价名称并存时 fail-closed。
- 非法 Unicode surrogate 转换为结构化 UOT 错误，不得泄漏原生编码异常。
- 旧包中依赖 Windows 保留名、尾随点/空格或大小写覆盖的布局会被明确拒绝。
- 路径预算是兼容性基线，不表示所有目标机器都启用了 Windows long-path。
- 发布、验证和 Agent 的进一步接入只能调用该计划，不得新增第二套 ZIP 规则。
- 旧 PyQt/updater 与迁移代码在迁移门禁完成前继续保留，路径修复不改变总体
  `Bridge → Agent → UOT Core → Bootstrap` 架构。
