# 06 · 本机配置与GitHub交付

## DeepSeek

1. 登录 [DeepSeek开放平台](https://platform.deepseek.com/api_keys)，创建API Key并确认可调用额度。
2. 首次在项目根目录执行 `Copy-Item .env.example .env`，已有.env时只编辑，不覆盖。
3. 本机.env配置：

```dotenv
DEEPSEEK_API_KEY=你的真实密钥仅填在本机
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

CLI真实模式自动读取 `.env`，`.env.example`保留空密钥。环境变量优先于文件；更新配置后如未生效，检查终端中已有的同名变量。

```powershell
python run.py ask "报销期限多久？" --provider deepseek --output examples/deepseek/manual-conflict.json
python run.py evaluate --provider deepseek
python scripts/check.py
```

模型默认值及Responses接口依据 [DeepSeek官方入门](https://api-docs.deepseek.com/) 与 [兼容说明](https://api-docs.deepseek.com/guides/responses_api/)，核对日期2026-10-29。可用模型和账户权限以平台返回为准。HTTP401检查Key，402检查余额，429检查限流。

## 可选DashScope配置

从根目录运行 `python run.py serve --build`，打开127.0.0.1:8765即可使用React界面。首次构建需要Node 22.12+或24，密钥配置在根目录.env。详见 [09](09-react-ui-and-streaming.md)。

向量检索单独使用 `DASHSCOPE_API_KEY`，配置后以 `--retrieval hybrid`启用，无需额外安装SDK。详见 [07](07-hybrid-retrieval.md)。

## 创建仓库

登录GitHub的 `Christy0831` 账号后，在 [新建仓库](https://github.com/new) 页面：

- Owner选择Christy0831；仓库名可使用 `enterprise-knowledge-agent`。
- 公开仓库方便评审；若选择私有，按题目要求添加指定评审账号。
- 创建空仓库，不预先生成README、.gitignore或License，避免与本地交付文件发生初始化冲突。

当前公开仓库为 `https://github.com/Christy0831/enterprise-knowledge-agent.git`。新建其他仓库可参考 [GitHub官方说明](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)。

## Git安装与首次推送

运行 `git --version` 检查安装。Windows可从 [Git官方入口](https://git-scm.com/downloads/win) 安装，再重开IDE终端。Git Credential Manager可用于推送时登录GitHub。

本机也可使用便携MinGit，在当前PowerShell会话中加入PATH：

```powershell
$agentGitBin = Join-Path $env:LOCALAPPDATA 'CodexTools\MinGit-2.56.0.4\cmd'
$env:Path = "$agentGitBin;$env:Path"
git --version
```

本地仓库已配置远端、提交身份和Git Credential Manager，Windows使用系统证书库（schannel）。其他机器按自身Git环境配置即可。

确认模型验收和文档实际状态后，在仓库根目录执行：

```powershell
git init -b main

# 使用你自己的提交身份；邮箱从GitHub Settings > Emails复制可用地址或noreply地址
git config user.name "Christy0831"
git config user.email "替换成你确认的GitHub提交邮箱"

# 检查.env确实被忽略，且可提交模板为空密钥
git check-ignore .env
git status --short

git add README.md SUBMISSION.md pyproject.toml requirements.txt run.py .gitignore .env.example src tests scripts data docs examples frontend submission .github test.md
git diff --cached --stat
git commit -m "Implement evidence-grounded enterprise knowledge agent"

# 若仓库名不同，替换为实际URL；已有origin时先检查，不要盲目覆盖
git remote add origin https://github.com/Christy0831/enterprise-knowledge-agent.git
git push -u origin main
git rev-parse HEAD
```

邮箱是必须替换的占位符，不能按占位符直接提交。若git已初始化或远端已有提交，应先查看状态再同步，不能强推覆盖已有内容。`git check-ignore .env`正常应输出 `.env`；若没输出，先修复忽略规则再提交。

## 最终交付

提交题号3、实际GitHub网页URL、`git rev-parse HEAD`输出的完整40位SHA。确认该commit已推送，且评审账号能读取。GitHub Actions若实际运行通过，可作为额外复现证据；本地测试结果不等同于远端CI状态。

提交并推送后运行 `python scripts/package_submission.py`，生成填写文字和ZIP附件。修改代码或文档后，重新提交、推送并打包，以保持附件与最终SHA一致。
