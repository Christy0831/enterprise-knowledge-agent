# 02 · 调用流程、动态决策与取舍

## 架构

```mermaid
flowchart TD
    U[问题和可选对话历史] --> M[真实模型选择工具及参数]
    M --> V[参数及预算校验]
    V --> S[search_docs 本地BM25]
    V --> R[read_chunk 原文读取]
    S --> O[实际结果或工具错误]
    R --> O
    O --> M
    V --> F[finish 引用与状态校验]
    F -->|未通过：反馈错误| M
    F -->|通过| A[结构化回答或未完成状态]
    V -->|预算耗尽| L[step_limit 保留Trace]
```

主代码：`agent.py` 执行器、`models.py` 模型适配、`knowledge.py` 检索和读取、`schema.py` 工具及输出约束、`prompts.py` 决策要求。

## 原生工具循环

没有引入 Agent 框架。真实模式每次将问题、工具定义和此前的实际观察发送模型，由模型返回函数名和JSON参数。执行器校验并执行，结果以 `function_call_output` 配对回传，直至模型调用 `finish` 或达到上限。

模型可选择检索、改写后再检索、读取其他原文、提交追问或停止；执行器不替模型选业务动作。校验错误同样回传给模型，因此模型可以修正引用或参数。HTTP错误或无有效工具调用属于 `model_error`，不伪造一个后续模型动作。

工具契约：

| 工具 | 参数（另含简短reason） | 返回 |
| --- | --- | --- |
| `search_docs` | `query`, `top_k`（1～8） | 候选ID、日期、标题、范围、优先级、分数与预览；匹配总数及是否截断 |
| `read_chunk` | `doc_id`, `chunk_id` | 指定原文及元数据，或明确工具错误 |
| `finish` | 状态、回答、引用、缺口、冲突 | 本地校验通过后结束；失败则返回校验错误 |

`reason` 只是可审计的一句话动作说明，不是模型内部思维过程。

## DeepSeek 与 OpenAI 的接口差异

优先使用 DeepSeek 官网的 Responses API，地址为 `https://api.deepseek.com/responses`，默认 `deepseek-flash`。API采用无服务端会话的完整历史回传；设置 `reasoning.effort=none` 关闭thinking、`temperature=0`减少输出波动，省去记录思维过程的需求。温度为0也不保证服务端严格确定性。

根据官方兼容表，DeepSeek不支持OpenAI的加密reasoning项，也不接受关闭并行工具调用的效果。因此DeepSeek适配器不发送 `include`、`store`、`parallel_tool_calls`，不假定服务端执行strict校验；本地始终校验工具参数、引用和状态。模型一次返回多个调用时执行器逐个执行、分别扣预算，并按唯一call_id回传。

同轮的多个读取不被描述成“模型观察了前一个读取后又决定读取下一个”。Trace 用 `decision_round` 和 `next_action_source` 区分同轮预先提出的调用与观察后产生的新响应。`finish` 必须独占一轮，不能与尚未观察的工具结果一同提交。

保留的OpenAI适配器使用Responses API、strict函数schema及 `parallel_tool_calls=false`；`store=false`时将返回的reasoning项留在内存历史并回传，不写入Trace。HTTP/API测试均为本机假服务或模拟返回，不能当作真实模型效果证据。

官方依据（核对日期2026-10-29）：[DeepSeek入门](https://api-docs.deepseek.com/)、[DeepSeek兼容表](https://api-docs.deepseek.com/guides/responses_api/)、[DeepSeek Responses参数](https://api-docs.deepseek.com/api/create-response/)、[OpenAI工具调用](https://developers.openai.com/api/docs/guides/function-calling)。

## 检索设计

采用BM25，中文连续字符按二元字符切分，英文/数字按词切分；索引内容为标题与原文。分词、IDF、长度归一化和排序本地计算；排序稳定，便于替换知识快照后复核。检索返回预览，但预览不自动登记为可引用证据。

当前只有6份合成资料，BM25可直接运行，不需要下载embedding模型或配置向量库。代价是无法识别所有语义同义词；真实模型需要根据问题与检索反馈改写查询。离线Mock的少量同义规则只覆盖演示用例，不能作为通用语义检索能力。

`has_more=true`说明候选受top_k截断，不等于全部相关文档已查全。未知范围/优先级为null，版本日期只表示文档版本，不隐含有效性或替代关系。

## 引用、冲突与事实边界

本轮成功读取的原文登记在内存证据表。finish硬校验：

- `answered`必须有引用，且不能同时含未解决的缺口或冲突。
- 引用文档及片段须在本轮成功读取；版本日期必须匹配；引文至少6个字符且为连续原文。
- answer中的每个[文档ID/片段ID]标记，必须在结构化citations中有对应条目，防止只给标记而遗漏原文。
- 未完成状态必须列出缺口。未解决冲突必须为 `needs_clarification`，至少引用两份不同文档。
- 提交冲突前，须在读取冲突双方后成功补充检索以查证适用范围、优先级或替代关系；否则返回校验错误，由模型修正下一步。
- 工具失败不登记证据，前一任务的证据不会带入新任务。

**本地校验不做自然语言蕴含判断。** 例如，原文30天存在，并不代表模型另写的99天会被一个逐字引用校验器识别。语义支持、相关性与冲突是否完整发现，依赖真实模型及人工复核，不夸大为绝对防幻觉。

冲突处理由模型阅读与查证决定。D01/D02均未给优先级，即使D02日期更晚，也不能选它作为确定答案。合成资料中的文字视为不可信数据，不允许改变系统规则或生成其他工具。

## 失败与资源控制

工具错误包含code/message/retryable，模型看到后自行决定是否再试。执行器不自动重试工具。Mock为演示明确编码为重试一次；这只是规则行为，不构成ReAct证明。真实API网络故障不自动重试，返回 `model_error`。

预算以工具调用为粒度；批量调用超预算时只执行允许的部分并返回 `step_limit`，不再请求下一轮模型。普通工具输出及Trace保留；不把中间读取称为完成回答。单次请求有超时，无后台无限循环。

## 配置与复现

`.env`只读取白名单配置，外部环境变量优先；不执行shell、不展开变量。网络仅向配置的API端点发起请求；禁止把Authorization转发到重定向目标。默认只允许HTTPS，本机假服务测试允许localhost HTTP。全部知识库为合成资料，无真实员工隐私。
