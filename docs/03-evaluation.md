# 03 · 测试预期、实际结果、失败与限制

## 当前验证结果

在Windows、Python 3.12.4上实际执行：

```powershell
python run.py evaluate --provider mock
python run.py evaluate --provider deepseek
python scripts/check.py
```

BM25离线验收 **11/11通过**，DeepSeek/BM25基线 **11/11通过**，Python自动化测试 **74项通过，0失败、0错误**。原始记录见 [离线报告](../examples/mock/report.json)、[真实基线报告](../examples/deepseek/report.json)、[测试JSON](../examples/test-results.json) 和 [测试日志](../examples/test-results.txt)。

DeepSeek + DashScope/RRF严格混合验收 **11/11通过**，见 [混合报告](../examples/deepseek-hybrid/report.json)。测试覆盖融合、缓存、降级、总时限、取消及SSE；另有 [4组离线检索记录](../examples/hybrid/) 展示融合与失败路径。检索设计见 [07](07-hybrid-retrieval.md)，等待问题修正见 [08](08-timeout-and-cancellation.md)。

API协议测试使用本机服务和模拟返回；实际DeepSeek记录包含响应ID、Token用量和工具反馈，覆盖正常、冲突、无结果与故障路径。回答、原文与决策路径的核对见 [manual-review.md](../examples/manual-review.md)。

## 场景矩阵与实际输出

前端事件解析4项、浏览器8项通过，另有模型SSE和Edge页面运行记录，详见 [09](09-react-ui-and-streaming.md)。

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
| 产品问答 | 引用D04说明字幕功能，回答范围限于资料中的事实 | [11](../examples/mock/11_product_answer.json) |

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

失败记录：[初次报告](../examples/development/initial_report.json)、[完整Trace](../examples/development/initial_out_of_scope_failure.json)。去掉未知问题中的泛化扩展词，同义改写限定为演示词组，离线复测11/11通过。真实模型的相关性另用实际API用例检查。

真实第一轮10/11：冲突结果正确列出双方并追问，但没有补充检索查证，未通过动作要求。保留 [第一轮报告](../examples/development/deepseek-first-run/report.json)。修正为更明确的提示，并在finish提交冲突时校验是否已在读取双方后补查；新增回归测试。

真实第二轮10/11：无历史追问用例出现一次模型协议错误，程序返回model_error。后续复跑通过，但偶发错误的根因尚未确定。记录见 [第二轮报告](../examples/development/deepseek-second-run/report.json) 和 [失败用例](../examples/development/deepseek-second-run/08_missing_context.json)。

第三轮自动检查11/11通过，复核时发现45天回答附带了资料未声明的“专门制度优先”建议。调整提示中的效力判断约束后重新验收。记录见 [第三轮报告](../examples/development/deepseek-third-run/report.json)。

第四轮11/11自动检查通过，但人工发现明确答案包含[D03/C01]标记而结构化引用列表遗漏D03原文。保留 [第四轮报告](../examples/development/deepseek-fourth-run/report.json)，增加“answer中的引用标记必须在citations提供版本和原文”的硬校验及回归测试，随后重新真实验证。

第五轮10/11：相似问题被过度澄清，模型因“垫付/报账”推测额外业务分类，未返回预期的answered状态。调整单文档来源限定与同义问法的提示，并设置temperature=0，随后复测通过。记录见 [第五轮报告](../examples/development/deepseek-fifth-run/report.json)。

## 真实模型验收步骤

1. 在本机.env配置DeepSeek密钥，运行 `python run.py evaluate --provider deepseek`。
2. 查看 `examples/deepseek/report.json` 的通过数、失败检查和对应Trace。
3. 至少人工检查正常和冲突两条路径；确认观察发生在新一轮决策前，`response_id`与工具结果实际存在。批量读取的同轮调用不算观察后的新决策。
4. 人工逐项核对回答是否受原文支持、是否遗漏冲突、追问是否适当、资料外是否拒答、上下文是否只用于意图理解。
5. 更换知识快照重复标准问题，核对30→45天及对应引用、版本、hash确实改变。

自动报告用于检查状态、引用和动作特征，回答语义按上述步骤复核。发现失败后记录原因，修改并重跑相关场景。

## 已知限制与未完成项

- 当前验收覆盖小规模合成场景；曾出现一次协议错误，长期稳定性待进一步测试。
- 默认BM25与DashScope向量/RRF都可能误召回；已通过小规模真实混合验收，阈值0.5尚未在独立问题集校准。知识库很小，未测大规模召回率、成本和并发；运行用时记录不等同于延迟基准。
- 引用校验不验证自然语言蕴含；prompt也不能保证抵抗所有资料中的恶意指令。
- 未实现企业访问控制、数据更新服务、跨会话存储、部署或生产监控。
- GitHub Actions包含四组Windows/Linux后端检查与一组前端检查，状态见最终commit的检查页面。
- 最终SHA与固定版本链接由打包脚本填入提交说明。
