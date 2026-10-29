# 提交信息

- 题号：**3 · 企业知识问答 Agent**。
- GitHub账号：`Christy0831`。
- GitHub URL：[Christy0831/enterprise-knowledge-agent](https://github.com/Christy0831/enterprise-knowledge-agent)。用户创建的公开仓库，Git已验证可连接。
- 最终commit SHA：待实际提交并推送后，通过 `git rev-parse HEAD` 获取，在最终提交消息中附完整SHA。
- 评审入口：[README.md](README.md)。

## 当前状态

- 已完成：CLI、真实模型工具循环接口、本地检索和读取、引用/状态校验、冲突及错误反馈、调用上限、合成数据、验收用例、测试及指定文档。
- 已验证：BM25离线验收11/11；自动化测试54项通过。详见 [测试与限制](docs/03-evaluation.md)。
- 真实模型：已使用DeepSeek官网API运行，最新完整通过轮次11/11；原始记录在examples/deepseek/，失败历史与人工复核发现的问题均保留。
- 后续扩展：已实现DashScope text-embedding-v4 + BM25/RRF，.env预留空Key。合同、融合及降级已验证；真实向量API与召回质量待用户填写Key后验证，不能用BM25基线或人工向量代替。
- 远端交付：Git、远端连接、GitHub登录及提交身份已配置。最终commit与推送结果以交付消息和远端实际状态为准，不在此文件内硬编码自身commit的SHA。
- 实际用时：[examples/worklog.json](examples/worklog.json)。

## 提交前核对

1. 真实模型记录至少包含两条根据不同工具结果调整动作的路径，核对正常、冲突、资料外、相似问题、上下文、工具失败与上限状态。
2. 人工检查引用是否在语义上支持回答，尤其是冲突和资料外场景。
3. 保留失败与限制，更新评估文档和本文件的实际状态。
4. .env不进入Git；.env.example中不含真实Key。
5. 推送最终commit，确认URL、SHA与评审访问权限；不宣称未执行的远端CI已经通过。
