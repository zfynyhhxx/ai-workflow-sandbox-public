# 工作流连接层规格

## 目标

在现有 Claude Code、Codex、OpenCode V2、AO、GitHub 和 Obsidian 上建立可追溯的完整闭环。用户批准本轮 API 总预算 1 USD，测试时仅将 Claude Code 临时指向 DeepSeek。正式 Profile 保持 Claude API。

## 必须保持的合同

- 七职责：Director/Planner/Builder/Reviewer/Knowledge 为独立原生 CLI 产物阶段，Router 为确定性分级，Dispatcher 为可信控制面。
- 输入以 GitHub Issue 中的白名单 JSON 为准，不接受任意命令、审批字段或 URL；输入、规则、Profile 和基准变化使批准失效。
- 低风险 T3，跨模块至少 T2，高风险 T1 并等待针对具体计划的人工批准；两次技术失败依次升级，三次候选失败停止。
- 依赖未完成、并发锁冲突、审批过期、预算不足、候选 SHA 变化、CI 非成功均不得继续完成状态。
- 一任务一隔离 worktree；结构化文件变更先完整验证再落盘，拒绝穿越、重复文件、符号链接和未批准路径。
- 测试用例来自已冻结的控制面模板。先记录预期失败，再接受 Builder 产物。测试在无外网/无凭据环境运行。
- 独立 Reviewer 绑定 base/head/规则与证据；只输出问题与建议，不签发人类批准。
- 审查证据必须绑定实际通过的 Git tree、完整动态门禁记录及控制面冻结测试来源；规格/ADR 变化使批准失效。信息性报告与未解决 findings 分开，任何未解决 finding 仍阻止发布。
- 允许自动推送受审候选、创建 draft PR 和等待 CI；最终合并不自动执行。
- Knowledge 仅获取脱敏证据；无复用价值不建笔记。Writer 只能在人类确认后创建 pending，不能修改原笔记。

## 预算语义

- 一个固定私有账本覆盖本轮全部 CLI、后台模型请求、重试与恢复，不随新 repo 或新 run 重置。
- 请求预留按已核实模型整个上下文上限及网关输出上限计算；DeepSeek 使用官方峰时 cache miss 上限。真实用量可核查时按峰时上限保守计账；不可核查时保留整个预留。
- 这是一项按已核实费率与模型上限执行的本地保守限制，不是 Provider 账户硬限额，也不能约束其他程序使用同一账户。
- actual_cost 没有最终账单证据时为 null；保守记账不能冒称最终扣款。

## 授权信任边界

签名密钥和 GitHub 凭据只属于可信控制面。隔离 API Worker 不能访问它们；Codex 产物模式无 shell/MCP/Apps。主机管理员、同用户宿主机恶意程序或被攻陷的控制面不属于该隔离承诺，需要更强身份/主机安全体系时另行评估，不能声称 HMAC 可以证明所有同 UID 进程都是人类。

## 验收矩阵

分别验收：核心/异常回归、真实三类 CLI、临时 Provider 不持久化、OS 网络与文件访问拒绝、AO 会话/恢复、真实 Issue→候选→独立审查→PR→CI、服务器门禁拒绝错误候选、人类合并后完成状态、Knowledge 和 pending Writer。

使用轮询接单，不开放互联网 webhook 端点；因而没有未验签的入站 webhook。恢复必须复用检查点、检查进程归属并保留费用和重试次数，不重复创建外部对象。
