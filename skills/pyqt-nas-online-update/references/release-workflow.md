# PyQt + NAS + UOT 发布流程

## 1. 变量清单

执行前先确认这些变量，缺失时从项目文档、spec、版本文件、配置文件或用户输入中获取：

- `project_root`：PyQt 项目根目录。
- `python_exe`：项目虚拟环境里的 Python。
- `uot_exe`：同一环境中的 `uot` CLI。
- `app_id`：发布到 NAS 的应用标识。
- `product_name`：安装目录和用户入口使用的产品名。
- `app_exe`：GUI 稳定入口 exe 名称。
- `updater_exe`：updater exe 名称。
- `pyinstaller_spec`：PyInstaller spec 文件。
- `current_version`：当前源码版本。
- `target_version`：发布目标版本。
- `nas_root`：NAS 根目录或挂载路径。
- `settings_file`：通常是 `config/settings.json`。
- `endpoint_file`：通常是 `update-endpoint.json`。

## 2. 初始化配置

优先生成项目本地配置，不要修改 pip 安装后的 UOT 包目录。

```powershell
uot init --app <app_id> --nas-root <nas_root> --force
```

预期生成：

```text
update-endpoint.json
config/settings.json
```

传入 `--nas-root` 时，UOT 应检查：

- NAS 根目录存在。
- 当前系统凭证可读。
- 当前系统凭证可写入、读取并删除临时探测文件。

如果只是离线生成配置，可加：

```powershell
uot init --app <app_id> --nas-root <nas_root> --force --skip-nas-check
```

## 3. 构建 PyInstaller

从项目根目录执行：

```powershell
<python_exe> -m PyInstaller --noconfirm --clean <pyinstaller_spec>
```

如果项目有自定义 launcher，应保持 GUI 和 launcher 内部构建名分离，避免 release 目录里的 GUI exe 被 launcher 覆盖。

## 4. 装配安装目录和升级目录

优先使用 UOT CLI 或项目中的薄包装脚本。薄包装脚本只应传递项目参数，不应重新实现通用装配逻辑。

通用形态：

```powershell
uot assemble-pyinstaller `
  --dist-dir dist `
  --version <target_version> `
  --settings-file <settings_file>
```

如果项目保留兼容包装脚本：

```powershell
<python_exe> tools\assemble_enterprise_release.py `
  --dist-dir dist `
  --version <target_version> `
  --update-dir `
  --settings-file <settings_file>
```

预期产物：

```text
dist\<product_name>_install_v<target_version>\
dist\<product_name>_update_v<target_version>\
```

用户快捷方式和手工启动应指向安装根目录里的稳定入口：

```text
dist\<product_name>_install_v<target_version>\<app_exe>
```

## 5. 打包升级包

只压缩升级目录内容，不压缩安装目录。

```powershell
Compress-Archive `
  -Path dist\<product_name>_update_v<target_version>\* `
  -DestinationPath dist\<product_name>_<target_version>.zip `
  -Force
```

## 6. 发布到 NAS

```powershell
uot publish `
  --settings <settings_file> `
  --app <app_id> `
  --version <target_version> `
  --package dist\<product_name>_<target_version>.zip `
  --notes "发布 <target_version>" `
  --min-supported-version <minimum_supported_version>
```

校验：

```powershell
uot verify --settings <settings_file> --app <app_id>
```

NAS 目录通常应包含：

```text
<nas_root>\
└── <app_id>\
    ├── stable\
    │   └── latest.json
    └── v<target_version>\
        ├── latest.json
        └── package.zip
```

## 7. 本地升级验证

从旧版本安装根启动：

```powershell
dist\<product_name>_install_v<current_version>\<app_exe>
```

验证项：

- GUI 能发现 `<target_version>`。
- 升级完成后 GUI 可重新进入新版本。
- 安装根 `current.json` 的 `version` 已切到 `<target_version>`。
- 安装根 `update-result.json` 的 `success` 为 `true`。
- `logs/update.log` 有安装成功记录。
- `logs/launcher.log` 有版本切换或重启记录。

## 8. 提交前检查

- 源码版本只来自项目统一版本文件。
- 项目配置位于 `config/settings.json` 和 `update-endpoint.json`。
- UOT 依赖包目录没有被写入项目私有配置。
- 打包脚本没有重新实现 UOT 已提供的通用装配、发布、校验能力。
- 验证命令和结果应记录到交付说明或最终回复中。
