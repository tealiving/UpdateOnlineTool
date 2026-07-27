# Release 路径与中文兼容治理任务流

## 目标与架构边界

本任务解决下载后解压的路径过长、跨平台非法名称、中文/Unicode 冲突和受控
目录逃逸。所有策略收口在 UOT Core；GUI、PyQt/Electron/Tauri 适配器、Bridge
和宿主项目只消费结构化结果，不解析 ZIP、不拼 NAS 或 `current.json` 路径。

## 根因归纳

| 根因 | 原行为 | 风险 |
| --- | --- | --- |
| 发布标识直接参与 `Path / value` | app、channel、version 可包含 `..` | 包与 manifest 可写出 NAS 根 |
| manifest version 只有非空检查 | runtime 直接生成 `releases/<version>` | 安装/切换可逃出 releases |
| Python Bootstrap 只做字符串拼接 | 存在即启动 | 可启动安装根之外入口 |
| ZIP 只检查绝对路径和 `..` | 不检查 Windows 名称、长度和冲突 | 解压失败或静默覆盖 |
| dry-run 与 extract 各自遍历 | 规则可能漂移 | 预检通过、真实安装失败 |
| 本机文件系统代替目标平台规则 | macOS 上看似正常 | Windows/归一化文件系统失败 |

## 执行队列

| 编号 | 任务 | 验收条件 | 状态 |
| --- | --- | --- | --- |
| RP-00 | 建立发布标识红测 | 拒绝前无 NAS 文件残留 | 已完成 |
| RP-01 | 建立统一 release identity 合同 | NFC、中文允许、非法段拒绝 | 已完成 |
| RP-02 | 接入 manifest、NAS、安装、切换 | SDK 入口不能绕过 CLI | 已完成 |
| RP-03 | 对齐 Python/Rust Bootstrap | 仅允许受控 release 与入口 | 已完成 |
| RP-04 | 实现 `ReleasePackagePlan` | dry-run 与解压复用同一计划 | 已完成 |
| RP-05 | 增加长度与冲突错误码 | UI 可区分布局非法和路径过长 | 已完成 |
| RP-06 | 接入 `package-release` 原子产包 | 非法布局不替换已有包 | 已完成 |
| RP-07 | 平台矩阵 harness | 三平台 + 中文/冲突/长度 | 已完成（单元层） |
| RP-08 | 接入外部 `publish`/`verify`/`prepare` 包预检 | 源 ZIP、NAS/缓存临时副本提升前使用相同计划 | 已完成 |
| RP-09 | Agent/Bridge 端到端故障注入 | status、rollback、无临时残留 | 已完成 |
| RP-10 | Windows long-path 开/关真机矩阵 | 记录实际安装根预算与行为 | 清单已建立，真机待执行 |

## Harness 设计

当前不需要修改 Bridge/Agent 架构。harness 增加独立 package-plan 平台矩阵，
并保留 runtime 公共缝测试：

1. 词法层：路径段、相对路径、版本和 Unicode 规范化。
2. plan 层：三个目标平台、大小写/NFC-NFD 冲突、保留名、文件/目录冲突、
   组件长度和完整路径长度。
3. 事务层：install 与 dry-run 返回相同错误，不创建 release、不切换
   `current.json`、不留下 staging/backup。
4. 进程层：Python/Rust Bootstrap 拒绝路径和 symlink 逃逸。
5. 真机层：按
   [三平台真机验收清单](release-path-real-device-checklist.md)执行 Windows
   long-path 开/关、macOS 默认归一化卷和 Linux case-sensitive 文件系统验证。

## 规则文档评估

- 原有“UOT 是唯一更新核心”“GUI 不解析 NAS/current.json”“新接入使用
  Agent + Bootstrap”符合本方案，无需改变架构方向。
- 原规则只描述 ZIP 路径穿越，未覆盖标识、可移植名称、Unicode 冲突与长度预算，
  因此 ADR-0005、技术架构、企业差距、AGENTS 和打包 skill 需要同步更新。
- 企业架构中“P0 暂无”与复现结果不一致，必须改为已修复记录，避免审计误判。

## 冗余与历史代码治理

本阶段已删除 runtime 内 4 个重复 ZIP 规则函数、共 62 行：
`_verify_zip_plan`、`_safe_zip_member_parts`、`_normalized_zip_member_name` 和
`_extract_zip_symlink`，统一由 `ReleasePackagePlan` 承担。

审查修复阶段又删除 runtime 内独立的 `_verify_package`，将 manifest hash/size
验证并入 `ReleasePackagePlan` 的同一打开文件合同；CLI、通用装配和
PyInstaller 装配的三份平台别名表也已收敛到 `release_identity.py`。当前仍有
6 处“入口是否可启动”相关实现，但它们分属 ZIP 计划、release 文件系统和
Bootstrap 三个信任边界，本阶段不做跨语言强行合并；Python 文件系统侧后续可
收敛为一个公共 helper。

P4 旧兼容候选的生产代码静态上界仍为 874 行：

| 模块 | 行数 | 静态引用文件数 | 当前职责/阻断项 | 本阶段可删 |
| --- | ---: | ---: | --- | ---: |
| `pyqt_runtime.py` | 106 | 3 | 文档化 legacy PyQt 自定义 updater API 与测试仍直接引用 | 0 |
| `launcher.py` | 263 | 10 | `desktop.py`、`service.py` 和公共导出仍调用 | 0 |
| `updater_cli.py` | 179 | 15 | `uot-updater` console entry、PyInstaller spec 和 legacy 测试仍依赖 | 0 |
| `migration_package.py` | 326 | 7 | CLI、公共导出和旧安装根迁移仍依赖 | 0 |
| **合计** | **874** | **35 次文件级命中** | 候选上界，不等于死代码 | **0** |

另有 legacy NAS fallback、`_launcher`/`updater` sidecar 和 install-root 自动
向上纠正属于函数级候选。当前静态扫描没有证明它们是不可达代码；路径修复不能
借机删除仍受 console entry、公共 API、迁移命令或宿主兼容合同保护的实现。

只有同时满足以下门禁才能删除：

1. 受控旧版本到新版本迁移完成；
2. 三平台 Agent + Bootstrap 安装/切换/回滚验收完成；
3. 公共 facade 与宿主不再引用 standalone updater/PyQt runtime；
4. legacy NAS 使用量为零并有回滚方案；
5. 对应测试、文档、console entry point 和公共导出同步移除。

因此当前不得为了“去重”提前删除兼容代码；先统计调用关系和迁移证据，再按
模块提交独立删除变更。
