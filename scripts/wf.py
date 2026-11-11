#!/usr/bin/env python3
"""从固定安装源加载控制面，避免当前项目中的同名模块抢占入口。"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflowkit.cli import main
main()
