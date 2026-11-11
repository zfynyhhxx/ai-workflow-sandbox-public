# 配套文件验证报告

日期：2026-11-11（Asia/Shanghai）

## 已完成

- 标准库单元测试：31 项，退出码 0，测试框架报告 0.005 秒。
- 临时真实 Git 仓库的 prepare 命令：退出码 0，运行前后仓库状态一致；只生成仓库外的执行包，没有执行模型命令。
- FishMuse 默认禁用检查：退出码 2，明确拒绝未批准项目，没有进入真实项目。
- OpenCode V2 原生 JSON 与 AO YAML：对获取到的官方 Schema 校验通过；来源 hash 见 SOURCES.json。
- Python AST、JSON、TOML、YAML 语法检查通过。
- 主手册 73 个代码块与 20 个目录锚点检查通过；bash/zsh 代码块只做语法解析，没有执行安装/付费命令。
- AO Codex 包装器：sh 语法检查通过；危险隔离覆盖参数被拒绝（退出码 2）；仅版本查询返回 codex-cli 0.167.1。
- 所有文本的 Git 无索引空白检查通过。此交付目录不是现有 Git 项目，不伪造仓库提交状态。
- 常见秘密模式与禁止产物检查通过；没有认证文件、Key、用户笔记副本、日志或缓存。
- 原全局 AGENTS、FishMuse AGENTS、Obsidian 模板的 SHA256 在操作前后相同；FishMuse 工作树保持干净，DevFlow 原有脏状态被保留。

## 未运行与原因

- OpenCode/AO/Spec Kit 安装及真实 CLI 联调：本任务交付部署手册，不擅自改变工具与启动服务。
- Claude/DeepSeek API、Codex 真实任务调用：未发起额外账号或付费请求。
- FishMuse/DevFlow 产品测试与 Windows 实机验收：没有修改产品代码，不把配套工具测试当产品验收。
- 真实 Vault 写入：只用临时 Vault 做 writer 测试，不向真实知识库创建例行测试笔记。
- 专用 Ruff/ShellCheck/类型检查：当前未发现已安装命令，样例未引入额外开发依赖；替代验证为 AST、配置 Schema、Shell 语法、31 项功能/边界测试与主会话代码审查。仍不等于严格静态类型证明。

## 仍有的边界

- workflowctl 只生成路由与命令预览，不是有身份认证和预算事务的执行服务。
- 项目批准字段可以由本机用户编辑，不是不可伪造的人类授权。
- 路由依赖人正确提供任务范围与风险标签，不做完整源码语义分析。
- Draft Writer 的来源字段和七节检查不证明内容真实；查重为完全相同正文，秘密检查为常见模式。
- 原生权限配置与只读包装器是防误操作层，不替代 OS 隔离；worktree 也不是安全边界。
- 不同发布版本的插件、事件协议、模型权益与后台服务行为仍需逐个冒烟测试。

## 复验

```bash
cd /你的/AI-Workflow-Starter-2026-11-11
python3 -m unittest discover -s tests -q
sha256sum -c SHA256SUMS
```

运行测试会在本包目录生成 Python 缓存，这是本地复验产生的文件，不在原交付包内。
