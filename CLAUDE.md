# 跨 Harness 规则桥接

先阅读本库 AGENTS.md 及目标目录规则。仅可信 Dispatcher 可按签名批准启动独立会话；模型不得自行派生 Agent、扩大权限或操作远端。
计划、审查和知识阶段只读。Builder 仅输出批准路径的结构化产物，由控制面写入隔离 worktree；不得修改验收、审批或预算。
正式规格/ADR 以 Git 为准，不读取 Key、无关私人文件或完整 Vault，不自批知识。
