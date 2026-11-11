#!/usr/bin/env python3
"""AO 原生插件的受控适配入口；不解释或执行插件传来的提示文本。"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflowkit.ao import agent_main
from workflowkit.store import GateError
try:
    raise SystemExit(agent_main(sys.argv[1],sys.argv[2:]))
except (GateError,OSError,ValueError) as exc:
    print('AO_STAGE_STOPPED: '+str(exc),file=sys.stderr); raise SystemExit(2)
