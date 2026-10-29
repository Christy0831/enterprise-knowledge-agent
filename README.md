# 企业知识问答 Agent · 题目 3

帮助员工查询企业制度及产品资料。回答附文档 ID、版本日期和原文引用；资料冲突时查证并追问，资料不足或工具失败时明确返回未完成。

采用 **Python CLI + BM25/可选DashScope向量RRF混合检索 + 模型工具调用循环**。生成模型优先使用DeepSeek官网API；向量模型按用户选择使用DashScope `text-embedding-v4`。Python后端使用标准库，无第三方运行依赖；可选React界面的依赖由frontend/package-lock.json锁定。

> BM25和真实混合验收均已有11/11记录。Python测试74项、前端解析测试4项、浏览器用例8项通过；另有真实DeepSeek SSE与浏览器流式验证。小规模通过不代表通用质量保证。

## 评审入口

1. [需求与成功标准](docs/01-requirements.md)
2. [方案、动态决策与技术取舍](docs/02-solution.md)
3. [测试矩阵、实际结果、失败与限制](docs/03-evaluation.md)
4. [AI 辅助内容及人工复核边界](docs/04-ai-usage.md)
5. [实际用时与复盘](docs/05-retrospective.md)
6. [实际输入、输出、Trace 索引](examples/README.md) · [测试结果](examples/test-results.json)
7. [GitHub 配置与提交步骤](docs/06-setup-and-delivery.md) · [提交信息](SUBMISSION.md)
8. [真实模型答案与路径复核](examples/manual-review.md)
9. [DashScope配置、RRF原理与题目范围说明](docs/07-hybrid-retrieval.md)
10. [等待、超时与Ctrl+C排查](docs/08-timeout-and-cancellation.md)
11. [React界面、真实流式输出与验证](docs/09-react-ui-and-streaming.md)

## 启动React界面

```powershell
python run.py serve --build
```

打开 **http://127.0.0.1:8765**。页面支持问答与上下文追问、实时执行进度、模型流式草稿、引用原文、停止请求和运行记录下载。首次构建需要Node.js 22.12+或24及npm；本开发机已准备官方便携Node 24，命令会自动查找，无需修改全局PATH。已有构建时运行 `python run.py serve`即可。

密钥继续保留在本机.env，由Python后端读取，不进入浏览器。草稿明确标为待核验，finish校验通过后再展示正式状态与引用；Mock不模拟Token流。构建、开发、测试和流式边界见 [界面说明](docs/09-react-ui-and-streaming.md)。

## 环境与安装

CLI与Python后端要求 **Python 3.11+**；React构建另需Node。已在Windows / Python 3.12.4、Node 24上验证。以下命令在仓库根目录执行。

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

## 可选向量混合检索

本机.env已预留 `DASHSCOPE_API_KEY`，后续在该字段填入向量API Key即可启用。正式模型名为 `text-embedding-v4`，默认1024维。Key与URL须属于同一地域；控制台如提供业务空间专属地址，填写完整的 `DASHSCOPE_EMBEDDING_URL`。详见 [配置说明](docs/07-hybrid-retrieval.md)。

```powershell
python run.py ask "垫的钱最迟啥时候能报账？" --provider deepseek --retrieval hybrid
python run.py evaluate --provider deepseek --retrieval hybrid --require-vector
python scripts/demo_hybrid.py
```

两路独立排名后按 `Σ1/(60+rank)` 融合。Trace记录BM25/余弦原分、各路排名、RRF分及降级状态。向量失败默认明确降级为BM25，`--require-vector`禁止降级；评估不会把降级算作hybrid通过。文档向量缓存位于被Git忽略的 `.cache/embeddings/`，知识内容、版本或模型配置变化后重建。

混合验收默认写入 `examples/deepseek-hybrid/`，保留BM25历史记录。本机真实向量Key已配置并已实际调用；默认余弦阈值0.5仍需在独立问题集校准，RRF分数不是答案置信度。

真实模式默认向stderr显示当前阶段和耗时，stdout保持JSON。`--timeout 20`将每次模型HTTP等待限制为20秒总时限，连续空行保活不会延长期限；`DASHSCOPE_TIMEOUT`独立控制每次向量HTTP请求。`--quiet`可关闭进度，Ctrl+C返回cancelled及退出码130。详见[等待排查](docs/08-timeout-and-cancellation.md)。

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

状态还包括 `needs_clarification`、`insufficient_evidence`、`tool_error`、`step_limit`、`model_error`、`cancelled`。退出码：正常答复/追问/资料不足为0；工具失败、模型失败、上限或输入错误为2；批量评估有不通过用例时为1；用户取消为130，保留已有Trace。

## 项目结构

```text
src/knowledge_agent/   模型接入、工具循环、检索、校验与CLI
frontend/             React界面、Vite构建与浏览器测试
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

默认BM25基线使用中文二元字符/英文词项；可选DashScope向量/RRF已进行小规模真实调用，未完成独立检索质量基准。引用校验能验证“原文存在且本轮读过”，不能自动保证语义支持。权限系统、持久对话服务、UI、线上部署均非题目必需项；现按后续需求增加本地React界面，但没有账号服务、持久会话或线上部署。所有资料为合成数据。
