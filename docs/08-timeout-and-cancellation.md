# 08 · 响应等待、总时限与Ctrl+C

## 本次现象与定位

运行 `ask --provider deepseek --retrieval hybrid` 时曾出现长时间等待，堆栈停在DeepSeek响应体读取，手动中断后返回KeyboardInterrupt。排查重点是HTTP读取的超时范围。

代码中确实存在一个等待边界问题：原来的urllib `timeout`限制阻塞I/O，不是从连接到完整响应的总时限。DeepSeek官方说明，非流式请求在等待期间可能持续发送空行保活，因此收到保活数据的连接可能一直维持，不能把“45秒socket timeout”解释为“整个请求45秒内结束”。具体某次请求是否排队，仍需服务侧信息，不能凭堆栈确定。

依据：[DeepSeek保活机制](https://api-docs.deepseek.com/quick_start/rate_limit/)、[Python urllib timeout说明](https://docs.python.org/3.12/library/urllib.request.html)，核对日期2026-10-29。

## 修正

- 模型和DashScope HTTP共用总时限读取器，限制连接、响应头和完整响应体的调用方等待时间；连续空行不重置期限。
- 默认模型请求45秒总时限，可用 `--timeout`修改。DashScope每次HTTP请求由 `.env` 的 `DASHSCOPE_TIMEOUT`控制，默认15秒。它们均是单次请求期限，不是整个多轮Agent任务的总时限。
- 超时返回明确model_error或向量工具错误/降级，不自动重试网络请求，也不把超时记为完成。
- 终端向stderr输出模型等待、工具执行、阶段用时和结束状态；stdout保持完整JSON，`--quiet`关闭进度。
- Ctrl+C在模型等待或工具执行中产生 `cancelled`结果、completed=false，保留已完成步骤与中断位置；CLI退出码130，不输出整段Python堆栈。设置 `--output`时也会保存取消记录。
- 批量评估被取消后停止后续用例，报告包含cancelled、requested_total和本次执行数，不能被视为完整通过。

总时限由守护工作线程和单调时钟限制调用方等待；超时或取消后丢弃迟到结果，工作线程在下一I/O边界关闭本地响应。DNS或底层I/O无法立即中止时，线程不阻挡CLI退出。取消作用于本地等待，服务端推理是否继续由模型服务决定。

## 推荐排查命令

```powershell
# 显示当前阶段；每次模型请求最多等待20秒，向量失败也不隐藏
python run.py ask "报销期限多久？" --provider deepseek --retrieval hybrid --require-vector --timeout 20 --output examples/local/run.json

# 只检查检索/向量链路，生成端使用规则Mock；配置向量Key后仍会联网
python run.py ask "报销期限多久？" --provider mock --retrieval hybrid --require-vector

# 输出仍为JSON；只关闭stderr进度
python run.py ask "报销期限多久？" --provider deepseek --retrieval hybrid --quiet
```

如果再次出现model_error，可据进度判断卡在生成模型，而不是反复改动向量配置；需要更长等待时可以显式提高 --timeout。若是EMBEDDING_UNAVAILABLE，则检查DashScope地域、权限、额度或网络。Key不进入错误信息、Trace或Git。

## 验证

新增9项回归检查，覆盖持续chunked空行仍按总时限返回、慢连接与迟到结果、非有限超时参数、模型/工具执行中取消、取消后保存JSON、批量评估停止，以及stderr进度与--quiet。保活测试使用本机假服务，不能证明真实服务的排队原因。

修正后以DeepSeek + DashScope/RRF严格模式复测“报销期限多久”，约13秒返回冲突与追问，未降级。原始记录：[manual-conflict.json](../examples/deepseek-hybrid/manual-conflict.json)，完整用例结果见 [report.json](../examples/deepseek-hybrid/report.json)。
