# 题目3 · 企业知识问答 Agent 提交入口

- 题号：**3**。
- 项目：企业知识问答 Agent。
- 公开仓库：https://github.com/Christy0831/enterprise-knowledge-agent
- 最终 commit SHA：以提交框及附件中的完整40位SHA为准；通过 `git rev-parse HEAD` 获取。仓库内文件不能预写包含自身修改的commit SHA，提交后由打包脚本自动填入。
- 完整提交报告：[docs/10-submission-report.md](docs/10-submission-report.md)。
- 运行入口：[README.md](README.md)。

## 上传方式

代码、文档、Trace、实际输出和测试记录都在本仓库。网页提交框的说明文字和附件由同一commit生成：

```powershell
python scripts/package_submission.py
```

将生成的 `submission-output/提交框填写内容.txt` 全文粘贴到网页文字框，选择 `submission-output/enterprise-knowledge-agent-题目3-<SHA前12位>.zip` 作为唯一附件。截图所示上限为6个附件、每个100MB；脚本会检查ZIP小于100MB。

打包前须已提交全部修改；打包来源仅为该commit的Git快照，排除 `.env`、`.git`、缓存、依赖安装目录及本地构建产物。ZIP同时含完整源码、环境变量空模板、文档、验证材料、自动填入SHA的提交说明和文件哈希清单。生成文件保存在Git忽略目录，避免重复提交压缩包和SHA自引用。

## 结果与边界

- Python测试74项；前端解析4项；浏览器用例8项通过。
- 真实DeepSeek + BM25基线11/11、真实DeepSeek + DashScope/RRF严格混合验收11/11，原始记录分别在 `examples/deepseek/` 和 `examples/deepseek-hybrid/`。
- 已提供两种以上观察后动态决策路径，以及正常、资料不足、工具失败等实际输入/输出/Trace。详见提交报告的证据索引。
- React与真实流式输出为追加扩展；本地启动 `python run.py serve --build`，不涉及线上部署。
- 全部资料为合成数据；离线Mock仅证明流程。AI协助内容、实际用时及未完成边界均据实披露，不声称提交者已经独立手写或逐段审阅。

## 提交者最后核对

1. 亲自运行离线入口，阅读冲突回答、引用原文和工具失败Trace；个人修改/学习记录只按实际补充。
2. 确认提交框中的40位SHA与附件说明一致、仓库公开可读。
3. 在网页粘贴文字、选择ZIP，再由提交者点击最终提交。本脚本只整理材料，不访问或提交网页表单。
