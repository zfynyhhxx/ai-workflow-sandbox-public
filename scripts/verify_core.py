#!/usr/bin/env python3
"""统一动态门禁；由开发入口和无凭据隔离门禁共同调用。"""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys


def verify(root):
    steps=[]
    for name,folder in (('project-tests','tests'),('starter-tests','vendor/starter/tests')):
        command=[sys.executable,'-m','unittest','discover','-s',folder,'-q']
        result=subprocess.run(command,cwd=root,capture_output=True,text=True)
        sys.stdout.write(result.stdout); sys.stderr.write(result.stderr)
        count=re.search(r'Ran (\d+) tests?\b',result.stderr)
        passed=result.returncode==0 and count is not None and int(count[1])>0
        steps.append({'name':name,'command':command,'returncode':result.returncode,
                      'tests':int(count[1]) if count else None,'passed':passed})
        if not passed: return {'schema':'wf-core-checks-v1','passed':False,'steps':steps}
    parsed=[]
    try:
        for folder in ('workflowkit','tests','demo','scripts'):
            for path in sorted((root/folder).glob('*.py')):
                ast.parse(path.read_text(),filename=str(path.relative_to(root)))
                parsed.append(str(path.relative_to(root)))
        for folder in ('config','.workflow'):
            for path in sorted((root/folder).glob('*.json')):
                json.loads(path.read_text()); parsed.append(str(path.relative_to(root)))
    except (OSError,ValueError,SyntaxError) as exc:
        print(str(exc),file=sys.stderr)
        steps.append({'name':'syntax-and-config','returncode':1,'passed':False})
        return {'schema':'wf-core-checks-v1','passed':False,'steps':steps}
    steps.append({'name':'syntax-and-config','returncode':0,'passed':True,'parsed':parsed})
    return {'schema':'wf-core-checks-v1','passed':True,'steps':steps}


def main():
    result=verify(Path.cwd())
    # 测试可以输出不带换行的文本；最终机器记录必须独占一行。
    print('\n'+json.dumps(result,ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
