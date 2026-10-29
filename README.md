# 企业知识问答 Agent · 题目 3

帮助员工查询企业制度及产品资料。回答附文档 ID、版本日期和原文引用；资料冲突时查证并追问，资料不足或工具失败时明确返回未完成。

采用 **Python CLI + 本地 BM25 检索 + 模型工具调用循环**。真实模型优先使用 DeepSeek 官网 API，也保留 OpenAI Responses 接口。运行与测试均使用 Python 标准库，无第三方运行依赖。

> 已保存真实DeepSeek与离线Mock的验收记录，最新完整通过轮次均为11/11；自动化测试36项通过。真实模型记录见 `examples/deepseek/`，Mock仅用于验证离线流程。开发中失败轮次和人工复核发现的问题均保留，不将一次通过等同于模型始终正确。

## 评审入口

1. [需求与成功标准](docs/01-requirements.md)
2. [方案、动态决策与技术取舍](docs/02-solution.md)
3. [测试矩阵、实际结果、失败与限制](docs/03-evaluation.md)
4. [AI 辅助内容及人工复核边界](docs/04-ai-usage.md)
5. [实际用时与复盘](docs/05-retrospective.md)
6. [实际输入、输出、Trace 索引](examples/README.md) · [测试结果](examples/test-results.json)
7. [GitHub 配置与提交步骤](docs/06-setup-and-delivery.md) · [提交信息](SUBMISSION.md)
8. [真实模型答案与路径复核](examples/manual-review.md)

## 环境与安装

要求 Python **3.11+**。本地已在 Windows / Python 3.12.4 验证。以下命令在仓库根目录执行。

```powershell
python --version
python run.py --help
```

无需 `pip install` 即可运行。需要可安装的命令入口时，可选执行：

```powershell
python -m pip install -e .
knowledge-agent --help
```

推荐直接使用 `run.py`，评审时无需联网安装。数据和文档位于源码仓库内，不将 wheel 视为独立数据分发包。

## 一分钟离线运行

```powershell
# 默认知识库含30天/15天冲突，应列出双方并追问
python run.py ask "报销期限多久？" --provider mock

# 无冲突知识快照，应引用D01回答30天
python run.py ask "报销期限多久？" --provider mock --corpus data/fixtures/single_policy.json

# 重跑所有验收场景，生成实际记录到examples/mock/
python run.py evaluate --provider mock

# 重跑自动化测试，结果写入examples/test-results.json和.txt
python scripts/check.py
```

Mock 不访问网络，无需环境变量。它用规则模拟下一步动作，仅用于验证执行器、检索工具、引用校验与失败路径。

## 使用 DeepSeek 官网 API

1. 在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 创建 API Key，并确认账户可调用模型。
2. 首次执行 `Copy-Item .env.example .env`。如 `.env` 已存在，直接编辑，避免覆盖已有配置。
3. 在本机 `.env` 填入以下内容，保留真实密钥在本机：

```dotenv
DEEPSEEK_API_KEY=在本机填写你的真实Key
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

```powershell
python run.py ask "报销期限多久？" --provider deepseek --output examples/deepseek/conflict-manual.json
python run.py evaluate --provider deepseek
```

真实模式通过 HTTPS 调用 `https://api.deepseek.com/responses`；模型决定工具及参数，执行器将实际工具结果回传模型。默认关闭 thinking，只要求简短的动作依据，不保存内部思维过程。模型名与接口依据 [DeepSeek 官方入门文档](https://api-docs.deepseek.com/) 和 [Responses 兼容说明](https://api-docs.deepseek.com/guides/responses_api/)，核对日期为 2026-10-29。

真实调用会使用你的 API 额度。`evaluate` 包含 11 个用例，每例默认最多 12 步，其中一个用例限 1 步；模型网络失败也会记录为失败，不能算验收通过。不同模型的动作数和表述允许不同，以实际报告和人工检查为准。

CLI 自动读取仓库根目录 `.env`，系统/终端中已有的环境变量优先。只支持 `KEY=value`，允许整行注释和外层引号，不执行 shell、不展开变量、不支持行尾注释。`.env` 已忽略，不把密钥放进代码、Trace 或 GitHub。HTTP 错误不回显服务端响应体。

可选 OpenAI 后端：设置 `OPENAI_API_KEY`、`OPENAI_MODEL`，运行 `--provider openai`；默认地址 `https://api.openai.com/v1`。不需要同时配置两个服务。

## 上下文、故障与上限

```powershell
python run.py ask "那需要准备哪些材料？" --history examples/history.json --provider mock
python run.py ask "垫的钱最迟啥时候能报账？" --corpus data/fixtures/single_policy.json --provider mock
python run.py ask "报销期限多久？" --provider mock --fail-tool search_docs --fail-count 1
python run.py ask "报销期限多久？" --provider mock --fail-tool read_chunk --fail-count -1
python run.py ask "报销期限多久？" --provider mock --max-steps 1
```

将 `mock` 换成 `deepseek` 可验证真实模型对相同故障和检索结果的决策。`--fail-count -1` 表示持续故障，正整数表示故障次数。

每次 `ask` 输出完整 JSON，可用 `--output` 另存。`history` 为 `role=user|assistant`、`content` 字符串组成的数组；历史仅用于理解上下文，本轮引用仍须重新读取。

| 字段 | 意义 |
| --- | --- |
| `result.answer` | 回答或未完成原因，使用 `[doc_id/chunk_id]` 指向引用 |
| `result.citations` | 文档 ID、片段 ID、版本日期、逐字原文 |
| `result.gaps` / `conflicts` | 资料缺口、待确认事项和冲突双方 |
| `result.status` / `completed` | `answered` 才是已完成，其余状态明确标记未完成 |
| `trace` | 实际动作、参数、工具结果与摘要、后续动作、简短依据 |
| `decision_round` | 一次模型请求；同轮多个调用不表示模型已观察前一个调用 |
| `corpus_sha256` | 本次知识快照指纹，方便比对替换文档后的结果 |

状态还包括 `needs_clarification`、`insufficient_evidence`、`tool_error`、`step_limit`、`model_error`。退出码：正常答复/追问/资料不足为 0；工具失败、模型失败、上限或输入错误为 2；批量评估有不通过用例时为 1。

## 项目结构

```text
src/knowledge_agent/   模型接入、工具循环、检索、校验与CLI
data/knowledge.json   含故意冲突的合成制度和产品资料
data/fixtures/        无冲突快照、45天替换快照
data/cases.json       11组验收输入与预期
tests/                标准库unittest测试
examples/mock/        当前实际离线输入、输出、Trace、报告
examples/deepseek/    当前实际DeepSeek输入、输出、Trace、报告
examples/development/ 开发中发现并保留的失败记录
docs/                 需求、方案、评估、AI使用与复盘
scripts/check.py      测试及结果归档
.github/workflows/    离线检查CI配置（远端执行情况另行核实）
```

## 已知限制

检索是中文二元字符/英文词项 BM25，不是向量语义检索；泛化同义表达依赖真实模型改写。引用校验能验证“原文存在且本轮读过”，不能自动保证“原文在语义上支持答案”。冲突发现与相关性判断依赖真实模型，需用真实记录和人工复核证明。无权限系统、持久对话服务、UI 或线上部署；所有资料为合成数据。
