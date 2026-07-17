# ADR-0002: Release 使用 UOT 完整性契约

## 状态

已采纳。

## 决策

新 release 在根目录写入 `uot-release.json`，记录应用、版本、平台、入口与必需
资源。UOT 在安装、切换和回滚前验证该契约；宿主可通过 bridge 的
`release_required_paths` 为历史 release 声明 settings、bridge 等必需资源。

## 后果

缺资源、入口错误或版本不一致的 release 不得进入 `current.json`。契约是可选的
向后兼容文件；旧包不会因缺少契约立即失效，但仍受宿主必需资源校验。
