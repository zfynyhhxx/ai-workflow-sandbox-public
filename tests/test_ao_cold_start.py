"""核心修复不依赖会话占位，官方包变化必须失败关闭。真实 AO 另行验收。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

PRELOAD=Path(__file__).resolve().parents[1]/'scripts/ao-compat.cjs'
SOURCE='''
async function projectHasNonTerminalSession(config, projectId) { return config.active.includes(projectId); }
export async function inspect(config, projectId) {
    const hasNonTerminalSession = await projectHasNonTerminalSession(config, projectId);
    return hasNonTerminalSession;
}
'''

class ColdStartTests(unittest.TestCase):
    def test_selected_empty_and_idle_projects_stay_supervised_without_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'supervisor.mjs'; target.write_text(SOURCE)
            code='''
const fs=require('fs'),crypto=require('crypto'),{pathToFileURL}=require('url');
const helper=require(process.argv[1]);
const target=process.argv[2], original=fs.readFileSync(target);
const hash=crypto.createHash('sha256').update(original).digest('hex');
helper.installPinnedHook(target,hash,'selected');
import(pathToFileURL(target).href).then(async api=>{
 const result=[await api.inspect({active:[]},'selected'),await api.inspect({active:[]},'foreign'),
               await api.inspect({active:['foreign']},'foreign'),await api.inspect({active:[]},'selected')];
 console.log(JSON.stringify({result,unchanged:fs.readFileSync(target).equals(original)}));
});
'''
            r=subprocess.run(['node','-e',code,str(PRELOAD),str(target)],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(r.stdout),{'result':[True,False,True,True],'unchanged':True})
    def test_unverified_source_and_changed_shape_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'changed.mjs'; target.write_text('export const changed=true;')
            code="const h=require(process.argv[1]);h.installPinnedHook(process.argv[2],'wrong-sha','selected');"
            r=subprocess.run(['node','-e',code,str(PRELOAD),str(target)],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0); self.assertIn('AO_SOURCE_HASH_MISMATCH',r.stderr)
            code="require(process.argv[1]).rewriteSupervisor('changed source','selected');"
            r=subprocess.run(['node','-e',code,str(PRELOAD)],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0); self.assertIn('source contract changed',r.stderr)
    def test_opencode_v2_launch_has_no_legacy_model_warmup(self):
        code='''
const h=require(process.argv[1]);
const source='const shellEscape=x=>JSON.stringify(x);module.exports={getLaunchCommand(config) {return "legacy run --command true";}};';
const rewritten=h.rewriteOpenCode(source);
const Module=require('module'),m=new Module('fixture');m._compile(rewritten,'fixture.cjs');
console.log(JSON.stringify(m.exports.getLaunchCommand({prompt:'approved-marker'})));
'''
        r=subprocess.run(['node','-e',code,str(PRELOAD)],capture_output=True,text=True,check=True)
        command=json.loads(r.stdout)
        self.assertIn('--wf-stage',command); self.assertIn('approved-marker',command)
        self.assertNotIn('--command true',command)

if __name__=='__main__': unittest.main()
