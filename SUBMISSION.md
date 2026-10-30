# 题目3 · 企业知识问答 Agent 提交入口

- 题号：**3**。
- 项目：企业知识问答 Agent。
- 公开仓库：https://github.com/Christy0831/enterprise-knowledge-agent
- 最终 commit SHA：由打包脚本读取 `git rev-parse HEAD`，自动填入提交框文字和附件说明。
- 完整提交报告：[docs/10-submission-report.md](docs/10-submission-report.md)。
- 运行入口：[README.md](README.md)。

## 上传方式

代码、文档、Trace、实际输出和测试记录都在本仓库。网页提交框的说明文字和附件由同一commit生成：

```powershell
python scripts/package_submission.py
```

将生成的 `submission-output/提交框填写内容.txt` 全文粘贴到网页文字框，选择 `submission-output/enterprise-knowledge-agent-题目3-<SHA前12位>.zip` 作为唯一附件。截图所示上限为6个附件、每个100MB；脚本会检查ZIP小于100MB。

先提交修改，再运行打包命令。ZIP取自对应commit的Git快照，包含源码、空密钥配置模板、文档、验证记录、提交说明和文件哈希清单。`.env`、`.git`、缓存、依赖安装目录及本地构建产物均排除。输出保存在 `submission-output/`，由Git忽略。

## 结果与边界

- Python测试74项；前端解析4项；浏览器用例8项通过。
- 真实DeepSeek + BM25基线11/11、真实DeepSeek + DashScope/RRF严格混合验收11/11，原始记录分别在 `examples/deepseek/` 和 `examples/deepseek-hybrid/`。
- 已提供两种以上观察后动态决策路径，以及正常、资料不足、工具失败等实际输入/输出/Trace。详见提交报告的证据索引。
- React与真实流式输出为追加扩展；本地启动 `python run.py serve --build`，不涉及线上部署。
- 全部资料为合成数据；Mock用于离线流程检查。开发过程、阶段用时和后续计划分别见docs/04、docs/05及worklog。

## 提交前核对

1. 运行离线入口，检查冲突回答、引用原文和工具失败Trace。
2. 确认提交框中的40位SHA与附件说明一致、仓库公开可读。
3. 在网页粘贴文字、选择ZIP，确认后提交。
