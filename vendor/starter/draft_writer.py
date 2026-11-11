#!/usr/bin/env python3
"""独立、确定性的草稿写入器，不读取密钥、不调用模型、不批准知识。"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo

HEADINGS = ["问题与背景", "核心发现", "证据与来源", "适用范围", "不确定性与限制", "相关项目及文档", "审核意见"]


def validate_body(body):
    body = body.strip()
    if not body or len(body) > 40000:
        raise ValueError("正文为空或超过 40000 字符")
    if re.search(r"(?m)^\s*---\s*$", body) or "{{" in body:
        raise ValueError("正文不能包含 frontmatter 分隔符或模板占位符")
    if re.search(r"(?im)^\s*status\s*:", body):
        raise ValueError("状态只能由 Draft Writer 设置")
    if any(f"## {h}" not in body for h in HEADINGS):
        raise ValueError("正文缺少模板要求的七个章节")
    if re.search(r"(?i)\b(sk-ant-|sk-[a-z0-9]{20,}|ghp_|github_pat_)", body):
        raise ValueError("疑似凭据，拒绝写入；此检查不是完整秘密扫描")
    return body


def render(template, body, title, project, source, date):
    if not source.strip():
        raise ValueError("必须提供证据来源")
    for value in [title, project, source]:
        if not value or len(value) > 1000 or "\n" in value or "\r" in value:
            raise ValueError("标题、项目和来源必须是非空单行")
    if not template.startswith("---\n") or "\n---\n" not in template[4:]:
        raise ValueError("模板 frontmatter 格式不受支持")
    front, template_body = template[4:].split("\n---\n", 1)
    if not re.search(r"(?m)^status: pending$", front) or not re.search(r"(?m)^type: ai-knowledge$", front):
        raise ValueError("模板必须声明 type: ai-knowledge 和 status: pending")
    if 'project: ""' not in front or 'source: ""' not in front:
        raise ValueError("模板字段已变化，请人工适配")
    if any(f"## {h}" not in template_body for h in HEADINGS):
        raise ValueError("模板章节已变化，请人工适配")
    front = front.replace('project: ""', "project: " + json.dumps(project, ensure_ascii=False))
    front = front.replace('source: ""', "source: " + json.dumps(source, ensure_ascii=False))
    front = front.replace("{{date}}", date)
    if "{{" in front:
        raise ValueError("模板含未解析字段，请人工适配")
    return "---\n" + front + "\n---\n\n# " + title + "\n\n" + body + "\n"


def write_draft(vault, body, title, project, source, slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,60}", slug):
        raise ValueError("slug 必须是 3 至 61 位小写英文、数字或短横线")
    body = validate_body(body)
    vault = Path(vault).expanduser().resolve(strict=True)
    template_path = vault / "90-Templates/AI-Knowledge-Draft.md"
    if template_path.is_symlink() or template_path.parent.is_symlink():
        raise ValueError("模板或模板目录不能是符号链接")
    template = template_path.read_text(encoding="utf-8")
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    result = render(template, body, title, project, source, date)
    if re.search(r"(?i)\b(sk-ant-|sk-[a-z0-9]{20,}|ghp_|github_pat_)", result):
        raise ValueError("标题、来源或模板含疑似凭据，拒绝写入")
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]
    filename = f"{date}-{slug}-{digest}.md"
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(vault, flags)
    try:
        for part in ["00-Inbox", "AI-Drafts"]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        for name in os.listdir(fd):
            if not name.endswith(".md"):
                continue
            if not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                continue
            read_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(read_fd, "r", encoding="utf-8") as stream:
                existing = stream.read(100000)
            if existing.endswith("\n\n" + body + "\n"):
                raise ValueError("已有完全相同正文的草稿；请人工查重，不重复创建")
        target_fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(target_fd, "w", encoding="utf-8") as stream:
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return vault / "00-Inbox/AI-Drafts" / filename


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(Path.home() / "Knowledge/Engineering"))
    parser.add_argument("--body", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    try:
        path = write_draft(args.vault, Path(args.body).read_text(encoding="utf-8"), args.title, args.project, args.source, args.slug)
        print(path)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"未写入：{exc}\n")


if __name__ == "__main__":
    main()
