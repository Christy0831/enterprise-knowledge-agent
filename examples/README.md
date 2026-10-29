# 实际运行记录索引

## 运行与证据类型

```powershell
python run.py evaluate --provider mock
python run.py evaluate --provider deepseek
python scripts/check.py
```

- `mock/`：规则Mock实际执行产生的11组输入、输出和Trace。`evidence_kind=mock_flow_only`，不证明真实模型决策能力。
- `deepseek/`：在本机配置Key后生成的真实模型运行文件；API失败同样记录。不能只看文件存在，须看结果、模型响应ID、观察后的新决策和人工语义审阅。
- `deepseek/`为BM25基线；`deepseek-hybrid/`为真实DeepSeek + DashScope/RRF严格混合记录，11/11通过。另有manual-conflict.json保存本次等待问题修正后的同问题复测，约13秒结束，未降级。
- `hybrid/`：4组融合/降级流程记录。前两组为人工指定向量，后两组明确缺Key；均不证明真实DashScope语义效果。可用`python scripts/demo_hybrid.py`复现。
- `test-results.json`、`test-results.txt`：自动化测试实际结果。本机HTTP测试是假服务，不是真实模型效果。
- `development/`：开发中保留的失败，反映修正依据。
- `worklog.json`：实际时间检查点和未完成项。
- `manual-review.md`：开发侧对真实答案、原文和观察后决策的人工复核；不虚构提交者已亲自完成审阅。
- `local/`：可选临时输出，Git忽略。
- `ui/`：React布局截图、前端测试记录、真实DeepSeek SSE与真实浏览器验证；开发截图和受控分片测试明确区分，详见 [09](../docs/09-react-ui-and-streaming.md)。

## 验收文件

| 文件 | 输入要点 | 检查点 |
| --- | --- | --- |
| `01_clear_answer.json` | 单制度快照问报销期限 | D01、30天、精确引用 |
| `02_conflict.json` | 含D01/D02问报销期限 | 读取双方、补查、冲突与追问 |
| `03_out_of_scope.json` | 火星土壤氦含量 | 无依据不答、资料缺口 |
| `04_paraphrase.json` | 垫的钱最迟何时报账 | 同义检索、结果一致 |
| `05_follow_up.json` | 带报销历史问材料 | 重新读取D03，不把历史当证据 |
| `06_transient_failure.json` | 首次检索不可用 | 实际错误后重新决策 |
| `07_persistent_failure.json` | 原文读取持续不可用 | 明确工具失败，无成功幻觉 |
| `08_missing_context.json` | 无历史“那要多久” | 请求补充问题对象 |
| `09_changed_document.json` | 原文改为45天 | 结论、日期、hash变化 |
| `10_step_limit.json` | 最多一步 | 未完成上限状态 |
| `11_product_answer.json` | 字幕功能问题 | D04，事实可追溯 |

文件位于对应provider目录，每组含 `input`、`fixture`、`result`、`trace`、`evaluation`；顶层有provider/model/evidence_kind、运行时间、知识快照SHA256、预算和实际步数。`report.json`汇总真实执行结果。

Trace的 `next_action` 来自实际后续动作，不是提前写好的轨迹。`decision_round`相同表示同一次模型响应；`next_action_source=same_model_response`时，不应将两次调用解读为观察后再决策。模型reason仅要求简短动作依据，内部思维过程不写入文件。

## 开发失败

[initial_report.json](development/initial_report.json) 为首次10/11结果；[initial_out_of_scope_failure.json](development/initial_out_of_scope_failure.json) 保存无关文档误答。修正与限制见 [评估文档](../docs/03-evaluation.md)。这些文件有意保留失败，不属于最终通过记录。

`development/deepseek-first-run/`至`deepseek-fifth-run/`保留真实联调过程。第一轮未补查冲突，第二轮有一次模型协议错误；第三、四轮虽自动检查通过，仍经人工发现效力建议和引用遗漏并继续修正；第五轮相似问题过度追问。最终结果以`deepseek/report.json`和复核记录为准。
