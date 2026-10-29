# 题目3 · 企业知识问答 Agent 提交报告

## 项目与评审入口

题号3，项目为“企业知识问答 Agent”。目标是帮助员工查询制度和产品资料，输出有原文依据的回答；对资料冲突、资料不足和工具故障，明确说明缺口与任务状态。

公开仓库：[Christy0831/enterprise-knowledge-agent](https://github.com/Christy0831/enterprise-knowledge-agent)。最终commit SHA由打包脚本从已提交Git快照读取，写入提交框文字和ZIP内 `提交说明.txt`，请以这两处完整40位SHA为准。

评审可先读 [README](../README.md)，再查看本报告的验收索引和 [实际评估](03-evaluation.md)。项目使用合成制度与产品资料，不含真实企业知识或API密钥。

## 实现思路与技术取舍

核心采用Python标准库实现模型工具循环，真实生成模型为DeepSeek官网API，工具包括 `search_docs`、`read_chunk` 和 `finish`。模型选择动作与参数，执行器运行工具并将实际结果反馈模型；模型再决定补充检索、读取、追问或结束。执行器负责工具参数、预算、引用与状态校验，不用固定分支假冒真实模型决策。

检索提供BM25基线，以及DashScope `text-embedding-v4`（默认1024维）向量检索。两路结果独立排名，以RRF `Σ1/(60+rank)` 融合；保留各路原分、排名、融合分与降级标记。向量失败时明确降级，`--require-vector`可禁止降级。小资料集采用本地向量缓存和余弦计算，避免引入向量数据库运维；这不代表已经证明混合方案优于BM25。

检索与读取分开：搜索预览不是正式引用，只有本轮成功读取的原文才能成为结构化引文。D01“30天”与D02“15天”冲突时，读取双方并补查适用范围/替代关系；未找到裁决依据则列出冲突并追问，不凭版本较新就选择15天。引用校验能保证片段存在、版本匹配且本轮读过，不能完全替代语义审核。

附加React/Vite本地界面，支持追问、切换检索、查看原文、停止与下载记录。后端将真实DeepSeek SSE参数增量中的回答文本传到浏览器；校验前明确标为草稿，校验失败清除，校验通过后展示正式答案及引用。密钥只由Python读取，不放入前端。界面是后续扩展，线上部署不在本次交付范围。

## 按题目逐项对应

| 题目要求 | 实现与材料 |
| --- | --- |
| 题号、GitHub URL、最终SHA | [SUBMISSION](../SUBMISSION.md)，打包生成的提交框文字与ZIP提交说明 |
| 可复制运行入口，不能只交截图 | `run.py`、`src/`、`frontend/`，下方运行命令及README |
| 模型选择→工具→观察→再次决策 | `src/knowledge_agent/agent.py`、`models.py`；下方正常/冲突/故障路径 |
| 检索与片段读取 | `knowledge.py`、`retrieval.py`、`embeddings.py`，含文档ID/版本的 `data/` |
| 回答、文档ID、原文、缺口、状态 | 实际记录中的 `result.answer/citations/gaps/conflicts/status/completed` |
| 至少3组输入、输出、Trace | [真实混合验收目录](../examples/deepseek-hybrid/)，下表覆盖全部6类必验场景 |
| 至少两条不同观察后路径 | 正常直接读取后回答；冲突读取双方后补查追问；故障反馈模型后继续/停止 |
| 步数或调用上限 | 默认最多12次工具动作；[限1步记录](../examples/deepseek-hybrid/10_step_limit.json) 返回 `step_limit` |
| 替换文档检查变化 | [原30天](../examples/deepseek-hybrid/01_clear_answer.json) 对照 [替换45天](../examples/deepseek-hybrid/09_changed_document.json) |
| 无模型时离线入口与边界 | `--provider mock`，只验证流程，不证明真实模型决策 |
| 指定五类文档 | [需求](01-requirements.md)、[方案](02-solution.md)、[评估](03-evaluation.md)、[AI使用](04-ai-usage.md)、[复盘](05-retrospective.md) |
| 依赖、环境变量和测试 | `pyproject.toml`、`frontend/package.json` 与锁文件、`.env.example`、`tests/`、CI |
| 实际用时与未完成部分 | [worklog.json](../examples/worklog.json)、下方范围说明；不虚构个人独立工时 |

## 全部必验场景与实际记录

以下均指已有的真实DeepSeek + DashScope/RRF记录，非手写决策日志。每个JSON含输入、知识快照指纹、完整工具结果、简短决策依据、下一动作、最终输出与断言结果；无模型内部思维过程。

| 场景 | 实际结果 | 输入/输出/Trace |
| --- | --- | --- |
| 明确答案 | 无冲突快照回答30天，引用D01原文，`answered` | [01](../examples/deepseek-hybrid/01_clear_answer.json) |
| 文档冲突 | 列出30/15天和双方引用，补查后仍缺适用口径，`needs_clarification` | [02](../examples/deepseek-hybrid/02_conflict.json) |
| 资料外问题 | 两次检索无相关资料，不编造数值，`insufficient_evidence` | [03](../examples/deepseek-hybrid/03_out_of_scope.json) |
| 改写后的相似问题 | “垫的钱…报账”命中报销制度并回答30天，`answered` | [04](../examples/deepseek-hybrid/04_paraphrase.json) |
| 上下文追问 | 结合报销历史，将“那需要哪些材料”关联D03并重新读取，`answered` | [05](../examples/deepseek-hybrid/05_follow_up.json) |
| 工具临时失败 | 首次检索返回注入的UNAVAILABLE，下一轮模型再次检索，恢复后读取作答 | [06](../examples/deepseek-hybrid/06_transient_failure.json) |
| 工具持续失败 | 原文连续读取失败，`tool_error`，无正式引用，不虚报完成 | [07](../examples/deepseek-hybrid/07_persistent_failure.json) |

另外包含 [缺少历史](../examples/deepseek-hybrid/08_missing_context.json)、[替换文档45天](../examples/deepseek-hybrid/09_changed_document.json)、[执行上限](../examples/deepseek-hybrid/10_step_limit.json) 和 [产品问题](../examples/deepseek-hybrid/11_product_answer.json)，合计11个用例。

两条核心真实路径可直接对照：

1. 正常：`search_docs → read_chunk → finish`。搜索得到可用制度，模型读取后按D01回答。
2. 冲突：`search_docs → read_chunk → read_chunk → search_docs → finish`。观察到冲突，读取双方，另查裁决依据；未查到则追问。

临时故障路径为 `search_docs(失败) → search_docs → read_chunk → finish`。重试动作来自收到错误后的新一轮模型响应，故障由可控注入产生，不声称是真实外部服务宕机。详见 [开发侧原文与路径复核](../examples/manual-review.md)。

## 实际验证结果与失败改进

- Python自动化测试：**74项通过**，原始 [JSON](../examples/test-results.json) 与 [日志](../examples/test-results.txt)。API合同测试使用本机假服务，与真实模型质量验证区分。
- 真实DeepSeek/BM25：**11/11**，见 [报告](../examples/deepseek/report.json)。
- 真实DeepSeek + DashScope/RRF严格混合检索：**11/11**，见 [报告](../examples/deepseek-hybrid/report.json)。未将降级当成混合通过。
- 前端解析测试：**4项**；浏览器：**8项**，见 [前端执行结果](../examples/ui/frontend-checks.json) 与 [浏览器报告](../examples/ui/browser-tests.json)。
- 真实流式冒烟：收到182个回答增量，首段约9.4秒、总约11.2秒，拼接文本与最终校验答案一致；见 [实际事件](../examples/ui/live-stream.json)。这是单次记录，不是延迟均值。真实Edge页面先显示草稿，最终显示两条引用，见 [记录](../examples/ui/live-browser.json)。
- CI包含Windows/Linux、Python3.11/3.12四组后端检查及一组前端构建/浏览器检查；最终提交的状态请从提交说明中的commit检查链接查看。

保留了开发失败：初次离线资料外误召回、真实冲突未补查、无依据优先级建议、正文引用与结构化引用不一致、相似问法过度追问，以及手机抽屉无法明确关闭。针对实际输出修正提示/执行校验/页面交互并复测，未通过放宽预期掩盖问题。失败材料位于 [examples/development/](../examples/development/) 与 [手机修正前截图](../examples/ui/development/mobile-settings-before-fix.png)，分析见 [评估文档](03-evaluation.md)。

## 可复制运行

最小评审入口只需要Python3.11+，无需安装第三方Python包、模型Key或Node：

```powershell
python run.py ask "报销期限多久？" --provider mock
python run.py ask "报销期限多久？" --provider mock --corpus data/fixtures/single_policy.json
python run.py evaluate --provider mock
python scripts/check.py
```

真实模型/向量：参考 `.env.example` 在本机配置 `DEEPSEEK_API_KEY` 与 `DASHSCOPE_API_KEY`，然后：

```powershell
python run.py ask "报销期限多久？" --provider deepseek --retrieval hybrid
python run.py evaluate --provider deepseek --retrieval hybrid --require-vector
```

本地React界面另需Node.js22.12+或24与npm：

```powershell
python run.py serve --build
```

打开 http://127.0.0.1:8765。已有构建时 `python run.py serve` 即可。其他机器安装Node后可构建；本机便携Node并未作为附件或跨机器依赖。完整配置见 [README](../README.md) 与 [UI/流式说明](09-react-ui-and-streaming.md)。

## AI辅助、用时与未完成边界

Codex参与需求拆解、代码、测试、调试、文档和开发侧复核；用户选择第三题、DeepSeek、DashScope、GitHub仓库及React扩展，并在本机配置密钥。没有用户独立手写代码或逐段审阅的证据，不作此类声明。详见 [AI使用](04-ai-usage.md)；提交者个人复核、修改和学习经历应据实补充。

计时文件分别记录核心实现、混合检索、等待/取消修正和UI扩展的检查点；已记录阶段约 **103.60分钟**，不包含阶段间空档、后续发布/CI等待和本次材料整理，不等于个人独立工时，也不补写成“3小时”。本次整理另计于worklog。

未做独立大规模检索质量基准、生产并发压测、完整提示注入防护、权限系统、持久会话或线上部署。小规模合成用例通过不代表通用正确率；引用存在也不代表语义必然充分支持答案。当前交付聚焦题目核心链路及用户追加的检索/UI扩展。
