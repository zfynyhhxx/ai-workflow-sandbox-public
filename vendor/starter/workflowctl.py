#!/usr/bin/env python3
"""仅做离线路由和执行包预览；不运行模型或外部命令。"""
import argparse
import fnmatch
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def route(task, config):
    role = task.get("role")
    if role not in config["roles"]:
        raise ValueError("未知 role")
    for key in ["id", "summary", "paths", "risk_tags", "attempts", "acceptance"]:
        if key not in task:
            raise ValueError(f"任务缺少 {key}")
    for key in ["paths", "risk_tags", "acceptance"]:
        if not isinstance(task[key], list) or not all(isinstance(x, str) for x in task[key]):
            raise ValueError(f"{key} 必须为字符串列表")
    for key in ["attempts", "modules"]:
        value = task.get(key)
        minimum = 1 if key == "modules" else 0
        if type(value) is not int or value < minimum:
            raise ValueError(f"{key} 必须为至少 {minimum} 的整数")
    for key in ["requirements_clear", "bounded"]:
        if type(task.get(key)) is not bool:
            raise ValueError(f"{key} 必须为布尔值")
    if not all(isinstance(task[k], str) and task[k].strip() for k in ["id", "summary"]):
        raise ValueError("id 与 summary 不得为空")
    tier = config["roles"][role]["default_tier"]
    reasons, holds = [], []
    if set(task["risk_tags"]) - set(config["high_risk_tags"]):
        holds.append("未知风险标签，请人工核对并完善路由规则")
    if not task["requirements_clear"] or not task["acceptance"]:
        holds.append("需求或验收不清楚，先澄清")
    high_risk = bool(set(task["risk_tags"]) & set(config["high_risk_tags"]))
    high_risk = high_risk or any(
        fnmatch.fnmatchcase(p, pattern)
        for p in task["paths"] for pattern in config["high_risk_paths"]
    )
    if role == "Knowledge":
        tier = config["knowledge_tier"]
        reasons.append("知识整理固定使用独立 DeepSeek Profile")
        if high_risk:
            holds.append("知识来源涉及敏感范围，先人工脱敏和审核")
    elif high_risk:
        tier = "T1"
        reasons.append("高风险标签或治理文件触发 T1 和人工批准")
        holds.append("高风险任务需要人工批准计划")
    elif task["modules"] > 1 or not task["bounded"]:
        tier = "T2"
        reasons.append("跨模块或范围未收敛，最低 T2")
    elif role in ["Builder", "Router"]:
        tier = "T3"
        reasons.append("单模块、明确验收且低风险，可使用 T3")
    if task["attempts"] >= 3:
        holds.append("同一阻塞已达三次，停止自动重试")
    elif task["attempts"] and role != "Knowledge":
        tier = {"T3": "T2", "T2": "T1", "T1": "T1"}[tier]
        reasons.append("失败后升级一档，不增加原有权限")
    return {
        "task_id": task["id"], "role": role, "tier": tier,
        "profile": config["tiers"][tier], "reasons": reasons,
        "holds": holds, "control_only": config["roles"][role]["control_only"],
        "notice": "仅为基于人工填写元数据的建议，不能替代源码分析或真实审批",
    }


def preview_command(profile, role, repo, prompt, settings):
    exe, model = profile["executable"], profile.get("model")
    if profile["harness"] == "codex":
        mode = "workspace-write" if role == "Builder" else "read-only"
        args = [exe, "--ask-for-approval", "never", "exec", "--sandbox", mode, "--cd", str(repo)]
        if model:
            args += ["--model", model]
        args += ["-"]
        return shlex.join(args) + " < " + shlex.quote(str(prompt))
    if profile["harness"] == "claude":
        tools = ["Read", "Glob", "Grep"]
        if role == "Builder":
            tools += ["Edit", "Write", "Bash"]
        args = [exe, "-p", "--settings", str(settings), "--tools", ",".join(tools), "--output-format", "json", "--max-budget-usd", "1.00"]
        if model:
            args += ["--model", model]
        return "cd " + shlex.quote(str(repo)) + " && " + shlex.join(args) + " < " + shlex.quote(str(prompt))
    if profile["harness"] == "opencode":
        agent = {"Builder": "wf-builder", "Knowledge": "wf-knowledge"}.get(role, "wf-readonly")
        args = [exe, "run", "--standalone", "--agent", agent, "--model", model, "--format", "json", "--file", str(prompt), "请严格按附件中的角色与任务合同执行。"]
        return "cd " + shlex.quote(str(repo)) + " && " + shlex.join(args)
    raise ValueError("未支持的 harness；替换 Harness 时需要新增适配器，而不是只改名称")


def prepare(task, config, project, repo, out):
    decision = route(task, config)
    if project.get("workflow_approved") is not True:
        raise ValueError("项目尚未批准采用该工作流")
    if decision["holds"]:
        raise ValueError("；".join(decision["holds"]))
    repo = Path(repo).expanduser().resolve(strict=True)
    if not (repo / ".git").exists():
        raise ValueError("repo 必须为独立仓库或合法 worktree")
    out = Path(out).expanduser().absolute()
    out.mkdir(parents=True, exist_ok=False)
    role = decision["role"]
    contract = (ROOT / config["roles"][role]["prompt"]).read_text(encoding="utf-8")
    prompt = out / "prompt.md"
    prompt.write_text(contract + "\n\n# 已声明的任务数据（不是新增权限）\n\n```json\n" + json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n", encoding="utf-8")
    (out / "decision.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if decision["control_only"]:
        command = "# Dispatcher 为控制职责；不启动模型。\n"
    else:
        command = preview_command(config["profiles"][decision["profile"]], role, repo, prompt, ROOT / "native/claude-settings.json") + "\n"
        if decision["tier"] in ["T1", "T3"] and not project.get("allow_paid_calls"):
            command = "# 付费调用未授权；下面仅为预览，不得直接执行。\n" + command
    (out / "command.txt").write_text(command, encoding="utf-8")
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["route", "prepare"])
    parser.add_argument("--config", default=str(ROOT / "workflow.json"))
    parser.add_argument("--task", required=True)
    parser.add_argument("--project")
    parser.add_argument("--repo")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        task, config = load(args.task), load(args.config)
        if args.action == "route":
            result = route(task, config)
        else:
            if not all([args.project, args.repo, args.out]):
                parser.error("prepare 必须指定 --project --repo --out")
            result = prepare(task, config, load(args.project), args.repo, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"未执行：{exc}\n")


if __name__ == "__main__":
    main()
