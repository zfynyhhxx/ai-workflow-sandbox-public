#!/usr/bin/env python3
"""在用户本地终端不回显地录入专用测试 Key；不联网、不输出凭据。"""
import getpass
import os
from pathlib import Path


def main():
    if not os.isatty(0):
        raise SystemExit('请在自己的交互终端运行，拒绝从管道或聊天接收 Key。')
    folder = Path.home() / '.config/ai-workflow/secrets'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink():
        raise SystemExit('凭据目录不能是符号链接。')
    os.chmod(folder, 0o700)
    target = folder / 'deepseek.key'
    if target.exists() or target.is_symlink():
        raise SystemExit('专用凭据已存在，未覆盖；请告知已配置，不要粘贴内容。')
    value = getpass.getpass('输入专用 DeepSeek API Key（不回显）: ').strip()
    if not value or '\n' in value or '\r' in value:
        raise SystemExit('未收到有效的单行 Key；未保存。')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(value)
    print('已安全保存专用测试凭据；没有发起 API 请求。请仅回复“已配置”。')


if __name__ == '__main__':
    main()
