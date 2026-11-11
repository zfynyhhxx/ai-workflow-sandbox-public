# AI Workflow Sandbox Public 项目工作规范

## 定位与权威来源

- 本库仅用于跨项目工作流连接层及隔离练习，不包含 FishMuse、DevFlow、真实 Vault、凭据或私人运行日志。
- 当前授权：完整构建、真实 CLI/AO 联调、公开此新库、推送练习分支、创建 Issue/draft PR、运行 CI 和配置分支保护。原私库及历史保持不动。
- 规格与安全合同见 `docs/spec.md`，架构取舍见 `docs/adr/0001-controlled-connector.md`；命令、部署与操作入口唯一维护在 `README.md`。
- GitHub Issues/Projects 是业务任务/进度来源；私有 SQLite 仅保存执行、审批、预算与恢复元数据。

## 工具链与验证

- Ubuntu/Linux，Python 3.11+ 标准库；当前本机 Python 3.14；不引入通用编排框架或额外 Python 依赖。
- 统一门禁：仓库根目录 `sh scripts/verify.sh`。它运行单元/控制流回归、语法/配置检查、凭据模式扫描和 diff 检查。
- 真实 CLI 冒烟：`python3 scripts/smoke_harnesses.py`，会消费额度，必须使用共享私有账本。
- 隔离与权限负向验收按 README 的独立入口运行；确定性 CI 不使用模型 Key。
- 新功能的验收模板由控制面冻结，Builder 不得修改测试、规则、预算或通过条件。

## 角色、权限与预算

- 允许可信 Dispatcher 启动已批准的独立角色会话；不允许模型自行派生 Agent 或调用另一个 CLI 扩权。
- 每个任务先绑定 Issue 内容、规则指纹、Profile 模式、基准 SHA 和批准有效期；签名校验失败或输入变化立即停止。
- Planner/Reviewer/Knowledge 不写产品源码；Builder 输出结构化变更，控制面仅写批准路径和冻结验收文件。
- 独立 API Worker 使用 bubblewrap 无外网命名空间、空 HOME、只读上下文和唯一计量 socket；不持有真实 Key、GitHub 写凭据或审批密钥。
- Codex 保留用户原有模型和 ChatGPT 登录；独立产物会话关闭 shell、Apps、MCP 和子 Agent，使用只读 sandbox。
- 本轮所有 API 测试合计上限为 1 USD，付费请求并发 1；失败、重启、重试、恢复均不得重置预算。未知实际费用为 null，未知用量保守占满预留。
- 仅显式 validation 模式将 Claude Code 的 Provider 临时改为 DeepSeek；production 继续使用原工作流 Claude API Profile，不回退测试 Provider。

## 远端与人类动作

- 远端仅为本公开练习库；不得改原私库、其他项目、全局 Codex 规范或现有 Obsidian 笔记。
- 公开提交使用本库的 GitHub noreply 身份，不导入旧历史、私人邮箱或宿主机路径。
- GitHub 服务端合并不继承本机 Git 身份；远端合并必须显式指定 noreply 作者，随后核对 author/committer 均为 noreply，不得省略身份参数或用管理员绕过门禁。不得把私有归档的旧 Git 历史重新推入本库。
- 控制面可在批准范围内自动推送候选并创建 draft PR；最终合并、发布、额外收费不自动执行。
- 开发者不得调用 approve 为自己的模型任务签发批准；操作员在独立交互终端审阅后操作。
- Knowledge 只产生待审正文；人类确认来源、隐私与查重后，Draft Writer 才能新建 pending。禁止编辑 accepted、改变审批状态或搬移笔记。

## 完成标准

- 单元替身、真实 Harness、AO 会话、远端 CI、服务器强制门禁和人类最终验收分别提供证据，不互相替代。
- 只有当前冻结 SHA 的检查与独立审查有效；候选变化必须重新验收。
- 不以人工约定代替服务器门禁，不以旧结果或测试 Provider 冒称正式 Claude API 已验收。
- 运行资料留在用户私有状态目录，公共 PR/CI 仅包含必要脱敏摘要。
