"""GitHub 是任务状态来源；私有运行账本只存执行证据，不复制业务任务系统。"""
import json
import subprocess
from .store import GateError
from .harness import clean_env


def gh(*args,timeout=120):
    result=subprocess.run(['gh',*args],env=clean_env(),capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise GateError('GITHUB_OPERATION_FAILED: '+result.stderr[:800])
    return result.stdout.strip()


class Portfolio:
    def __init__(self,owner='zfynyhhxx',number=1):
        self.owner=owner; self.number=number
        self.fields={x['name']:x for x in json.loads(gh('project','field-list',str(number),'--owner',owner,'--format','json'))['fields']}
        self.project=json.loads(gh('project','view',str(number),'--owner',owner,'--format','json'))['id']

    def add(self,url):
        return json.loads(gh('project','item-add',str(self.number),'--owner',self.owner,'--url',url,'--format','json'))['id']

    def update(self,item,**values):
        aliases=[]; variables={'project':self.project,'item':item}
        for index,(name,value) in enumerate(values.items()):
            field=self.fields[name]; key=f'v{index}'
            if field.get('options'):
                option=next((x['id'] for x in field['options'] if x['name']==value),None)
                if not option: raise GateError('未知 Projects 选项：'+name)
                data={'singleSelectOptionId':option}
            else: data={'text':str(value)}
            variables[key]={'projectId':self.project,'itemId':item,'fieldId':field['id'],'value':data}
            aliases.append(f'{key}:updateProjectV2ItemFieldValue(input:${key}){{clientMutationId}}')
        if not aliases: return
        names=','.join(f'${k}:UpdateProjectV2ItemFieldValueInput!' for k in variables if k not in ('project','item'))
        variables.pop('project'); variables.pop('item')
        query=f'mutation({names})'+'{'+''.join(aliases)+'}'
        result=subprocess.run(['gh','api','graphql','--input','-'],input=json.dumps({'query':query,'variables':variables}),
                              env=clean_env(),capture_output=True,text=True,timeout=120)
        if result.returncode or json.loads(result.stdout or '{}').get('errors'):
            raise GateError('GITHUB_PROJECT_UPDATE_FAILED')
