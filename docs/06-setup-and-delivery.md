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

CLI真实模式自动读取.env。不要将实际Key写回.env.example，也不用发送到聊天。`.env.example`必须保留空密钥以便提交。环境变量优先于文件，旧终端变量可能覆盖.env中的新值。

```powershell
python run.py ask "报销期限多久？" --provider deepseek --output examples/deepseek/manual-conflict.json
python run.py evaluate --provider deepseek
python scripts/check.py
```

模型默认值及Responses接口依据 [DeepSeek官方入门](https://api-docs.deepseek.com/) 与 [兼容说明](https://api-docs.deepseek.com/guides/responses_api/)，核对日期2026-10-29。具体可用模型和账户权限以平台实际返回为准。HTTP401先核对Key，402核对余额，429核对限流；不要通过改写验收结果掩盖API失败。

## 可选DashScope配置

新增React界面可从根目录运行 `python run.py serve --build`，打开127.0.0.1:8765。首次需要Node 22.12+或24，本开发机已有自动可识别的便携Node；密钥仍在根目录.env。详见 [09](09-react-ui-and-streaming.md)。

向量检索的本机配置独立于DeepSeek生成模型。`.env`已预留空的 `DASHSCOPE_API_KEY`；后续按 [07-hybrid-retrieval.md](07-hybrid-retrieval.md) 填写后以 `--retrieval hybrid`启用。无需额外安装SDK。

## 创建仓库

登录GitHub的 `Christy0831` 账号后，在 [新建仓库](https://github.com/new) 页面：

- Owner选择Christy0831；仓库名可使用 `enterprise-knowledge-agent`。
- 公开仓库方便评审；若选择私有，按题目要求添加指定评审账号。
- 创建空仓库，不预先生成README、.gitignore或License，避免与本地交付文件发生初始化冲突。

创建后复制实际HTTPS URL，形如 `https://github.com/Christy0831/enterprise-knowledge-agent.git`。这个示例地址不代表远端已经创建。流程依据 [GitHub官方创建仓库说明](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)。

## Git安装与首次推送

本次开始时终端未找到可用git。若 `git --version` 失败，先从 [Git官方Windows安装入口](https://git-scm.com/downloads/win) 安装Git for Windows，再重开IDE终端。安装后可使用Git Credential Manager在推送时登录GitHub，不在远端URL中嵌入密码或Token。

本次开发机已准备官方MinGit便携版，不修改全局PATH。若在这台机器的普通终端中仍找不到git，可在当前PowerShell会话中执行：

```powershell
$agentGitBin = Join-Path $env:LOCALAPPDATA 'CodexTools\MinGit-2.56.0.4\cmd'
$env:Path = "$agentGitBin;$env:Path"
git --version
```

本地仓库已配置远端、提交身份和Git Credential Manager。开发中遇到旧Git证书文件路径失效，已在本仓库使用Windows系统证书库（schannel），没有关闭TLS验证。重新克隆到其他机器时使用该机器正常的Git配置即可。

确认模型验收和文档实际状态后，在仓库根目录执行：

```powershell
git init -b main

# 使用你自己的提交身份；邮箱从GitHub Settings > Emails复制可用地址或noreply地址
git config user.name "Christy0831"
git config user.email "替换成你确认的GitHub提交邮箱"

# 检查.env确实被忽略，且可提交模板为空密钥
git check-ignore .env
git status --short

git add README.md SUBMISSION.md pyproject.toml requirements.txt run.py .gitignore .env.example src tests scripts data docs examples .github test.md
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

最终SHA在提交消息中提供，不在自身commit内写入自身SHA以避免循环修改。发布后若修改代码、证据或文档，需要重新提交、推送并使用新的最终SHA。
