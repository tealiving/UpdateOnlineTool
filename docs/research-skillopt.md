# Microsoft SkillOpt 项目分析

> 调研日期：2026-07-15。以下结论基于 Microsoft 官方仓库 `main`、PyPI 元数据和仓库内文档；未把第三方博客或二手评测当作依据。

## 项目定位与架构

SkillOpt 是“优化 agent skill 文档”的 Python 研究框架：把 Markdown skill 当作可训练状态，通过 rollout → reflect → aggregate → select → update → validation gate 的循环提出有界文本编辑；部署时只使用生成的 `best_skill.md`，不修改模型权重，也不增加推理期优化循环。核心训练器位于 `skillopt/engine/trainer.py`，环境适配器位于 `skillopt/envs/`，模型后端位于 `skillopt/model/`。训练和评估入口是 `skillopt-train`、`skillopt-eval`（或仓库中的 `scripts/train.py`、`scripts/eval_only.py`）。

仓库还包含相互独立的 **SkillOpt-Sleep（预览）**：`skillopt_sleep/` 不依赖论文训练包，夜间读取本地 Claude Code/Codex 会话，挖掘任务、离线重放、通过 held-out gate 后暂存 skill/`CLAUDE.md` 修改，人工 review 后再 adopt。默认 `mock` 后端不访问任何 provider，可用于无密钥自检。

## 安装和典型使用

### 研究训练引擎

```bash
python -m pip install skillopt                 # PyPI 运行包
# 需要仓库配置、数据物化器和示例时：
git clone https://github.com/microsoft/SkillOpt.git
cd SkillOpt
python -m pip install -e ".[searchqa]"
cp .env.example .env && set -a && source .env && set +a
python scripts/materialize_searchqa.py
python scripts/train.py --config configs/searchqa/default.yaml \
  --out_root outputs/searchqa_run
python scripts/eval_only.py --config configs/searchqa/default.yaml \
  --skill outputs/searchqa_run/best_skill.md --split valid_unseen
```

至少需要 Python 3.10 和一个模型后端（托管 API、OpenAI-compatible 本地服务或已安装的 Codex/Claude CLI）。默认依赖是 `openai`、`numpy`、`openpyxl`、`azure-*`、`httpx`、`PyYAML`；Qwen/vLLM、ALFWorld、SearchQA、Claude SDK、WebUI 等均为可选 extra。仓库没有官方 Docker/Compose/系统服务部署文件，部署形态是 Python 虚拟环境加外部模型服务或 CLI。

### SkillOpt-Sleep

```bash
python -m pip install skillopt
skillopt-sleep dry-run   # 只 harvest/mine/replay，不暂存
skillopt-sleep run       # 通过 gate 后暂存 proposal
skillopt-sleep status
skillopt-sleep adopt      # 人工检查后应用
```

Claude Code、Codex、Copilot、Devin 通过仓库 `plugins/` 中的薄封装接入；插件文件不包含在 PyPI wheel 中。真实后端会把截断的会话片段和派生任务发往所选 provider，敏感项目应先 `harvest --output tasks.json`、脱敏并标记 `reviewed: true`。可使用 `schedule` 安装定时任务，但仓库本身不提供统一守护进程。

## 资源轻量性评估

| 场景 | 资源判断 | 原因 |
|---|---|---|
| Sleep `mock` / dry-run | 轻量 | 纯本地 Python 文本处理，无模型调用；默认每晚最多 40 个任务、40 万 token 预算，可再调低。 |
| 研究引擎 + 托管 API | 中等偏轻（CPU） | 不下载模型权重，主要消耗 API 延迟/费用；部分基准（如 SearchQA）默认 rollout 64、reflection 16，批量大时需限制 workers。 |
| WebUI | 中等 | 额外安装 Gradio，并默认绑定 `0.0.0.0`；本机使用应显式 `--host 127.0.0.1`。 |
| Qwen 本地推理 | 不轻量 | `[qwen]` extra 引入 vLLM；模型权重、显存和推理吞吐由所选模型决定，项目未给出统一最低 GPU 规格。 |
| ALFWorld/DocVQA 等完整基准 | 取决于数据 | 官方文档要求额外安装或下载环境/图片/数据；数据和运行输出可能远大于代码本身。 |

PyPI `skillopt==0.2.0` 的通用 wheel 约 321 KB，源码包约 291 KB；在 macOS arm64 上按核心依赖下载的压缩 wheel 总量约 17 MB（不同 Python/平台会变化，安装后目录会更大）。因此“代码和默认运行时”相对轻，但“完整实验栈”并不一定轻：外部 API、数据集、并发任务和可选 vLLM 才是主要成本。SkillOpt-Sleep 的状态、日志和 proposal 都写入本地文件，运行次数与任务量决定磁盘增长。

## 与本项目的对接启示

SkillOpt 不是通用 exe 更新器，而是离线优化 agent skill 的实验/自动化工具。若要与本项目的 UOT 对接，适合把 `best_skill.md`、配置或插件作为待发布文本制品，经 UOT manifest/签名/回滚流程分发；不要把 SkillOpt 的训练过程放进客户端更新链路。Sleep 的“暂存 → 人工审阅 → adopt”安全边界也可借鉴到发布前审批。

## 官方来源

- [README（定位、训练循环、部署产物）](https://github.com/microsoft/SkillOpt/blob/main/README.md)
- [安装指南](https://github.com/microsoft/SkillOpt/blob/main/docs/guide/installation.md)
- [首次实验](https://github.com/microsoft/SkillOpt/blob/main/docs/guide/first-experiment.md)
- [配置与默认并发](https://github.com/microsoft/SkillOpt/blob/main/configs/_base_/default.yaml)
- [SearchQA 适配器并发默认值](https://github.com/microsoft/SkillOpt/blob/main/skillopt/envs/searchqa/adapter.py)
- [项目依赖与入口](https://github.com/microsoft/SkillOpt/blob/main/pyproject.toml)
- [训练器实现](https://github.com/microsoft/SkillOpt/blob/main/skillopt/engine/trainer.py)
- [SkillOpt-Sleep 说明与数据边界](https://github.com/microsoft/SkillOpt/blob/main/docs/sleep/README.md)
- [OpenAI-compatible 后端与隐私提示](https://github.com/microsoft/SkillOpt/blob/main/docs/sleep/openai-compatible-endpoints.md)
- [PyPI 0.2.0 元数据（wheel/sdist 大小与依赖）](https://pypi.org/pypi/skillopt/0.2.0/json)
