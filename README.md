# AI Workflow Sandbox Public

跨项目 AI 开发工作流的公开练习库。复用原生 Claude Code、Codex、OpenCode V2、Agent Orchestrator、GitHub 和 Obsidian；连接层负责批准、预算、派发、隔离与证据，不取代这些工具。

**正式配置与验证配置分开。** 本轮验证总 API 预算为 1 USD；只在显式 `validation` 模式下让 Claude Code 临时使用 DeepSeek。默认 `production` 仍引用原工作流的 Claude API Profile。任何验证结果都不冒称正式 Claude API 已验收。

## 1. 架构与权威来源

```text
人类目标 / GitHub Issue
  → Director → Router（确定性）→ Planner
  → 签名批准 / 依赖 / 并发 / 预算检查
  → Dispatcher → AO 会话 → 正确的原生 CLI
  → Builder 结构化产物 → 批准路径 worktree → 冻结测试
  → 独立 Reviewer → draft PR → GitHub 必需 CI → 人类合并
  → 脱敏证据 → 独立 Knowledge → 人类审阅/查重 → Writer → pending
```

- Git：正式规格、ADR、代码、冻结验收。
- GitHub Issues/Projects：任务与进度。看板为 [AI Development Portfolio](https://github.com/users/zfynyhhxx/projects/1)，看板本身仍保持私有。
- 私有 SQLite：执行元数据、批准签名、预算与恢复，不另建平行业务任务库。
- Obsidian：人工审核的知识；pending/rejected 不作为已确认知识。

规格见 `docs/spec.md`，架构取舍见 `docs/adr/0001-controlled-connector.md`。人类合并和知识批准不是缺失的自动化步骤，而是明确保留的批准边界。

跨项目复用的是角色、授权、隔离和执行协议，不代表任意技术栈零配置接入。本练习的可信本地测试入口是 Python unittest；新项目必须先审阅自己的规则、`.workflow/project.json`、冻结验收与真实 CI，其他构建/测试体系需先适配可信门禁，不能在 Issue 中注入命令。FishMuse、DevFlow 和 Windows 目标尚未接入。

## 2. 环境与安装

本机已核实的组合：Ubuntu/Linux、Python 3.14、Node 24、Git/gh/tmux/bubblewrap、Codex 0.167.1、Claude Code 2.1.206、OpenCode 2.2.7、AO 0.9.5、Spec Kit 1.4.0。

AO 0.10.3 的桌面启动器不适配本手册安全启动参数；本库使用已核实的 0.9.5，不自行升级。CLI 版本变化必须重新检查适配合同与门禁。

```zsh
git clone https://github.com/zfynyhhxx/ai-workflow-sandbox-public.git
cd ai-workflow-sandbox-public
python3 scripts/install_runtime.py
wf --mode validation status
```

安装只创建用户级 `wf` 入口，并在缺少起步包时安装随库提供的可移植快照；不覆盖既有起步包、CLI、全局 Codex 规则、模型、登录或凭据。`vendor/starter/README-PUBLIC.md` 说明快照与原交付包的关系。

本轮安全录入 DeepSeek Key 的入口如下，不接受聊天或管道输入，不覆盖已有 Key：

```zsh
python3 scripts/configure_deepseek.py
```

Key 保存在用户私有凭据目录，文件权限 600；本地文件权限不是加密，也不防被攻陷的同用户宿主进程。不要上传凭据、整个用户目录或 Vault。

## 3. 日常任务

### 建立 Issue

Issue 正文使用白名单 JSON。示例：

```json
{
  "id": "normalize-labels-001",
  "role": "Builder",
  "summary": "实现内部练习模块 normalize_label",
  "requirements_clear": true,
  "bounded": true,
  "modules": 1,
  "paths": ["demo/labels.py"],
  "risk_tags": [],
  "attempts": 0,
  "acceptance": [
    "normalize_label(text) 去除首尾空白、合并连续空白并使用 Unicode casefold",
    "纯空白输入抛 ValueError；非字符串抛 TypeError",
    "通过冻结的 normalize_labels_v1 验收，不改测试或规则"
  ],
  "depends_on": [],
  "acceptance_template": "normalize_labels_v1"
}
```

Issue 是数据，不是权限来源。不能填命令、Provider URL、审批字段或预算；未知字段会被拒绝。风险标签必须经过实际审阅，不以空列表自动推定安全。

### 人类批准，然后自动执行

以下编号换成真实 Issue 编号：

```zsh
wf --mode validation approve 1
# 在自己的终端审阅任务、模式、SHA 与规则指纹，确认后输入 APPROVE。
wf --mode validation watch --once
# 长期前台轮询：wf --mode validation watch --interval 30
```

在 GitHub 为任务添加 `workflow:ready` 标签后，Watcher 自动进行路由、规划、派发、修复、隔离测试、独立审查、推送、draft PR、CI 等待和知识正文生成，不需要人工 Dispatcher 逐个启动工具。

高风险任务停在具体计划批准点：

```zsh
wf --mode validation approve 1 --plan
wf --mode validation recover 1
```

Planner/Reviewer/Knowledge 不写源码。Builder 输出结构化文件，可信控制面完整验证后才写批准路径。AO 为原生 CLI 阶段管理会话/工作区；连接层的候选 worktree 是唯一可写业务候选，AO 阶段不另行实施或抢占同一任务。native adapter 不把 AO 提示或模型文本当 shell 执行。

### 人类收口

1. 查看当前 draft PR 的 diff、独立审查摘要、真实 CI 与已知限制。
2. 由人将 PR 标记 ready 并合并；连接层没有自动合并命令。
3. 执行 `wf finish 1`，它核对 mergedAt、冻结 HEAD 和真实成功检查后才标记 DONE，不会代替人合并。

**公开身份也包括服务器端合并。** 本机的 `git config user.email` 只约束本机提交，不约束 GitHub 生成的合并提交。由人确认 PR 和完整 HEAD SHA 后，使用显式 noreply 身份；将下面的编号和 SHA 替换为已审阅的实际值：

```zsh
gh pr ready PR_NUMBER -R zfynyhhxx/ai-workflow-sandbox-public
gh pr merge PR_NUMBER -R zfynyhhxx/ai-workflow-sandbox-public --merge \
  --match-head-commit REVIEWED_HEAD_SHA \
  --author-email '186288354+zfynyhhxx@users.noreply.github.com'
```

不得改用省略 `--author-email` 的命令或未经确认邮箱隐私设置的网页合并；不使用 `--admin` 或 `--auto`。合并后检查服务器返回的 author/committer 身份，核对失败即停止后续任务。其他操作者应使用自己账户对应的 GitHub noreply 地址，不能照搬此身份。

本公开库为干净历史重建库。旧 Git 目录、分支、PR 和 Issue 保留在私有归档；不得把旧克隆重新指向此公开远端再推送，避免重新引入历史。归档只用于查阅，不作为当前门禁验收库。

## 4. 停止与恢复

```zsh
wf status
wf --mode validation cancel 1
wf --mode validation recover 1
```

- 检查 PID 与启动时间，活进程未结束时拒绝重复派发。
- 同一输入的已完成角色产物缓存复用，变化的输入不得沿用。
- 不重置费用或三次候选失败次数；已有 PR 先查询，不重复创建。
- 未知用量按完整预留保守记账；停止请求不保证 Provider 退款。
- worktree 与证据保留，不执行 reset/clean 或删除用户工作。
- 本轮上限不得增加。未来获得新的明确消费授权后，先修改项目政策并提交，再由人通过 `wf budget --cap-usd ... --reason ...` 确认；已有费用始终保留，运行中不允许改预算。

## 5. AO 与隔离

AO 由专用环境启动，`scripts/ao-loopback.cjs` 在该进程树中把 TCP listener 收紧为回环地址，不改系统防火墙或上游包。首次启动必须使用 `--no-orchestrator --no-restore`，不得默认启动未批准模型。

```zsh
wf ao start       # 固定使用上述两个安全参数与回环 preload
# start 保持前台运行；以下命令在另一个终端执行。
wf ao status
wf ao stop        # 仅本配置实例，不使用 --all；保留模型产物与恢复记录
```

只使用该入口启动当前实例，不在普通终端直接执行未收紧的 `ao start`。已完成阶段的原生 AO restore 只重放绑定产物，不重复请求；未完成阶段必须走 `wf recover` 重新核对批准和费用。

AO 0.9.5 原版存在“无会话不启动 polling、无 polling 不允许 spawn”的冷启动死锁。本库的 `ao-compat.cjs` 在当前进程中修复监督谓词：显式启动的项目即使零会话也启动并保持真实 lifecycle worker；其他项目仍按原逻辑处理。它不伪造 running 状态，不禁用 spawn 的轮询检查，不修改安装包，不保留占位会话。版本和原文件 SHA 不匹配就拒绝启动，禁止静默套补丁。

同一兼容层将内置 OpenCode 插件的 V1 预热/交互启动改为受控单次阶段；实际调用仍是隔离的原生 OpenCode V2。目录查询仅返回原生事件中核实的真实 session ID/标题/时间，不调用模型，不伪造 V1 ID，也不暴露其他宿主会话。原始事件与实际模型证据在私有目录。AO 阶段报告 completed 只表示该 CLI 产物阶段结束，不表示业务 DONE；所有默认反馈派发已显式关闭，Dispatcher 是唯一执行控制者。

升级 AO 时先审查官方修复与新源码，再运行零会话冷启动、空闲后派发、停止/重启和三类 Harness 回归。官方版本通过这些回归后才能移除兼容层。直接绕过 `wf` 启动原版 AO 不在修复保证内。观察界面成功仍不代表 Worker 或完整流水线验收。

`workflowkit/ao.py` 经原生插件/tmux 启动受控适配器，实际模型调用仍经过原生 CLI。API Worker 只挂载只读运行输入、空 HOME、单个计量 Unix socket；无外网、无真实 Key、无 GitHub/审批凭据。Codex 产物会话关闭 shell、Apps、MCP、子 Agent，保留原 ChatGPT 登录。

默认禁止自动合并、自动反馈派发和自动清理，避免 AO 与连接层争夺控制权。共享状态目录不进入 Git。

## 6. Knowledge 与 Obsidian

Knowledge 使用独立 OpenCode/DeepSeek，只获得脱敏证据；没有长期价值就不建笔记。它不挂载或直接写 Vault。

收到运行 ID 和私有正文后，人类先审阅来源、事实/推断、隐私和重复，再执行：

```zsh
wf draft RUN_ID --title '经审阅的知识标题' --slug reviewed-topic
```

命令显示正文，再要求人工输入 DRAFT。独立 Writer 使用现有 `AI-Knowledge-Draft` 模板，只新建 `00-Inbox/AI-Drafts/` 内的 pending 文件；不能覆盖、批准或搬移正式笔记。

## 7. 正式模式

正式 Profile 直接引用既有 `workflow.json`，测试覆盖不会写回。正式首次运行时，操作员需要输入自己账户的 Anthropic Key、实际完整模型 ID、官方上下文与费率：

```zsh
python3 scripts/configure_anthropic.py
wf --mode production approve ISSUE_NUMBER
wf --mode production watch --once
```

这项录入不会发请求。本轮不会执行正式 Claude API 消费；账户输入和费率不成立时拒绝运行，不暗中回退 DeepSeek。正式定价超出核实期限也会停止。更换模型、Harness 或权限后，必须重新核对适配、计费与回归，不能只改字符串就宣称兼容。

## 8. 验证与证据

```zsh
sh scripts/verify.sh
python3 scripts/check_isolation.py
python3 scripts/check_ao_lifecycle.py  # 先停止现有 AO；包含 65 秒零会话空闲与重启，零模型调用
python3 scripts/smoke_harnesses.py
```

前两项不使用模型；最后一项是真实模型调用，复用同一个 1 USD 账本。确定性 CI 不配置模型 Key。Action 固定为核实过的完整 SHA。

运行日志、模型事件、签名密钥、用量账本及知识正文位于用户私有状态目录，禁止公开上传。公开 PR/CI 只放必要脱敏摘要。actual_cost 没有最终账单证据时为 null；保守预算计账不能冒称真实扣款。

`scripts/verify.sh` 与候选隔离门禁复用 `scripts/verify_core.py`：两组 unittest（项目和 starter）及原有 AST/JSON 检查全部执行。隔离门禁只读挂载控制面提供的 core，不执行候选可替换的验证入口，也不挂载宿主 `.git`。依赖 Git 元数据的凭据模式扫描与 diff 检查由控制面静态执行，不在宿主导入或运行候选代码。

Reviewer 接收冻结 head/tree、规则指纹、控制面验收模板的 Git blob/SHA256、一致性记录、真实检查命令/退出码/测试数及记录摘要哈希。规格和 ADR 也纳入规则指纹；实际模型无法确认时保留 null，不伪造。Reviewer 的信息性说明放在 report，findings 仅列未解决问题；仍要求 `approved is True` 且 findings 为空，不过滤旧问题强行通过。缺少证据、模板变化或 tree 不匹配时，在审查前停止。

控制面修复必须独立审阅、通过 CI 并由人合并；规则或基准变化后重新签名批准业务任务。保留原运行的审查拒绝、候选与账本，不能修改旧批准或覆盖旧审查来恢复。

**实施状态以当前证据为准。** 单元替身、三类 CLI 冒烟、AO 会话、真实远端 CI、分支保护和人类最终合并分别验收，不能互相替代；尚未完成的运行项不得标为通过。
