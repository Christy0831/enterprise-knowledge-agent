# 03 · 测试预期、实际结果、失败与限制

## 当前验证结果

在Windows、Python 3.12.4上实际执行：

```powershell
python run.py evaluate --provider mock
python run.py evaluate --provider deepseek
python scripts/check.py
```

离线验收 **11/11通过**，真实DeepSeek最新完整通过轮次 **11/11通过**，自动化测试 **36项通过，0失败、0错误**。原始记录见 [离线报告](../examples/mock/report.json)、[真实报告](../examples/deepseek/report.json)、[测试JSON](../examples/test-results.json) 和 [测试完整日志](../examples/test-results.txt)。时间戳和用时来自运行程序，不手写测试输出。

API协议测试使用本机假服务或模拟返回，与真实DeepSeek验收分别报告。真实记录使用用户本机配置的官网API产生，包含模型响应ID、Token用量和实际工具反馈；正常、冲突、无结果、工具失败等路径已运行。答案语义和动态路径的开发侧人工复核见 [manual-review.md](../examples/manual-review.md)。提交者仍应亲自复核，一次完整通过不证明所有未来请求都正确。

## 场景矩阵与实际输出

下表链接为离线记录；同名真实记录位于 [examples/deepseek/](../examples/deepseek/)，完整列表及判定见 [真实报告](../examples/deepseek/report.json)。所有文件均含输入、知识快照指纹、实际输出、完整工具返回、步骤与后续动作；测试预期和逐项断言结果一同保存在文件中。

| 场景 | 预期与实际离线结果 | 记录 |
| --- | --- | --- |
| 明确答案 | `answered`，30天，D01原文引用 | [01](../examples/mock/01_clear_answer.json) |
| 制度冲突 | `needs_clarification`，列出30/15天、引用双方，补查后仍无优先级则追问 | [02](../examples/mock/02_conflict.json) |
| 资料外问题 | `insufficient_evidence`，补查仍无资料，不给外部常识答案 | [03](../examples/mock/03_out_of_scope.json) |
| 相似问题改写 | “垫的钱…报账”首轮无匹配，改写报销期限后回答30天 | [04](../examples/mock/04_paraphrase.json) |
| 上下文追问 | 根据报销历史检索材料，引用D03列出发票、明细、审批单 | [05](../examples/mock/05_follow_up.json) |
| 临时工具故障 | 检索真实抛出一次UNAVAILABLE，观察错误后Mock选择再试，再读取作答 | [06](../examples/mock/06_transient_failure.json) |
| 持续工具故障 | 原文读取持续失败，`tool_error`，无伪造引用 | [07](../examples/mock/07_persistent_failure.json) |
| 缺少上下文 | 无历史的“那要多久”返回追问 | [08](../examples/mock/08_missing_context.json) |
| 文档替换 | D01改45天和新版本日期后，输出45天，不再30天 | [09](../examples/mock/09_changed_document.json) |
| 步数上限 | 只允许一步，检索后返回`step_limit`，completed=false | [10](../examples/mock/10_step_limit.json) |
| 产品问答 | 引用D04说明字幕功能，不虚构效率数值 | [11](../examples/mock/11_product_answer.json) |

## 两类不同路径

直接证据路径：01为 `search_docs → read_chunk → finish(answered)`。

冲突路径：02为 `search_docs → read_chunk → read_chunk → search_docs → finish(needs_clarification)`。第二次检索用于查证适用范围和优先级，未找到足够裁决依据。

以上步骤数为Mock记录。真实模型04可在首次检索前将“报账”改写为“报销”；真实03体现两次无结果后的停止，真实06体现错误反馈后改写检索，真实02体现读取冲突双方后补查。至少两条真实模型观察后决策路径见 [复核记录](../examples/manual-review.md)，不要求真实轨迹逐步复刻Mock。

## 自动化检查的含义

执行器测试覆盖未读/伪造引文、错误版本、失败读取、跨任务证据隔离、参数越界、未知工具、上限、错误回传及修正、缺少上下文等。

API合同测试检查实际HTTP请求路径、Authorization仅发往本机假服务、工具结果配对、reasoning项仅在内存回传、错误脱敏、DeepSeek请求兼容、批量调用单独计数和finish必须独占一轮。环境及CLI测试检查白名单.env、环境优先级、跨工作目录运行和退出码。

批量评估检查状态、关键事实片段、引用ID、逐字原文、成功读取记录、预算和必要动作特征。不要求真实模型逐步复刻Mock日志。例如，相似问题可在首次检索前直接改写，仍可通过；资料外问题可在一次有效检索后明确停止。

## 开发中发现的问题及修正

第一轮离线评估为10/11，资料外用例失败。Mock将“火星土壤氦含量”扩展为带“制度 产品”的查询，检索命中无关的报销制度，并错误提交回答。逐字引用校验仍通过，说明“引用真实”不等于“回答相关”。

保留的实际失败证据：[初次报告](../examples/development/initial_report.json)、[失败完整Trace](../examples/development/initial_out_of_scope_failure.json)。修正为不向未知问题添加泛化领域词；同义改写仅覆盖明确的演示词组。重新运行11/11通过。真实模型仍需进行相关性评估，不能因修好Mock就声称模型已解决此问题。

真实第一轮10/11：冲突结果正确列出双方并追问，但没有补充检索查证，未通过动作要求。保留 [第一轮报告](../examples/development/deepseek-first-run/report.json)。修正为更明确的提示，并在finish提交冲突时校验是否已在读取双方后补查；新增回归测试。

真实第二轮10/11：冲突已通过，但无历史追问用例出现一次模型协议错误，程序返回model_error而不是假装回答成功。保留 [第二轮报告](../examples/development/deepseek-second-run/report.json) 和 [失败用例](../examples/development/deepseek-second-run/08_missing_context.json)。后续复跑成功不代表偶发协议错误的根因已消除，当前选择明确失败，不自动掩盖。

第三轮11/11自动检查通过，但开发侧人工检查发现45天回答附带了资料未声明的“专门制度优先”建议。保留 [第三轮报告](../examples/development/deepseek-third-run/report.json)，随后进一步禁止无依据的效力推断并重新执行真实验收。这个问题说明关键事实断言和逐字引用不能替代语义审核。

第四轮11/11自动检查通过，但人工发现明确答案包含[D03/C01]标记而结构化引用列表遗漏D03原文。保留 [第四轮报告](../examples/development/deepseek-fourth-run/report.json)，增加“answer中的引用标记必须在citations提供版本和原文”的硬校验及回归测试，随后重新真实验证。

第五轮10/11：引用对应校验生效，但相似问题被过度澄清。模型在单制度无冲突快照中因“垫付/报账”而猜测额外业务分类，未返回与标准问法一致的answered状态。保留 [第五轮报告](../examples/development/deepseek-fifth-run/report.json)。修正为明确单文档来源限定与同义问法的关系，不自动因scope未知扩大缺口；DeepSeek温度设为0以减少输出波动。没有放宽验收状态来让失败通过。

## 真实模型验收步骤

1. 在本机.env配置DeepSeek密钥，运行 `python run.py evaluate --provider deepseek`。
2. 查看 `examples/deepseek/report.json` 的通过数及失败检查。API失败、调用上限与协议错误必须保留实际失败，不改写为通过。
3. 至少人工检查正常和冲突两条路径；确认观察发生在新一轮决策前，`response_id`与工具结果实际存在。批量读取的同轮调用不算观察后的新决策。
4. 人工逐项核对回答是否受原文支持、是否遗漏冲突、追问是否适当、资料外是否拒答、上下文是否只用于意图理解。
5. 更换知识快照重复标准问题，核对30→45天及对应引用、版本、hash确实改变。

真实模型自动报告并不代替人工语义审核。如果有失败，修正原因、重跑相关场景，并保留原失败说明；不要只选成功截图。

## 已知限制与未完成项

- 真实模型已实际验证，但仅覆盖小规模合成场景；此前发生过一次协议错误，不能将最新通过率解释为长期稳定性保证。
- BM25无embedding，中文二元字符可能误召回；语义判断依赖模型。知识库很小，未测大规模召回率、延迟、成本和并发。
- 引用校验不验证自然语言蕴含；prompt也不能保证抵抗所有资料中的恶意指令。
- 未实现企业访问控制、数据更新服务、跨会话存储、部署或生产监控。
- CI配置已提供；本机通过不代表GitHub Actions已经运行。
- GitHub URL已由用户提供并可通过Git连接；最终SHA在实际提交推送后附于交付消息，见提交文件。
