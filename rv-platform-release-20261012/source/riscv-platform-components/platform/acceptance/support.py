#!/usr/bin/env python3
"""Shared transport and evidence only; each directory owns its test assertions."""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid

class Skip(Exception):
    pass

def resource_kind(kind):
    names={'nodeprofile':'nodeprofiles.platform.riscv.io','resourceclaim':'resourceclaims.platform.riscv.io',
           'resourcepolicy':'resourcepolicies.platform.riscv.io','scenariopolicy':'scenariopolicies.platform.riscv.io',
           'hostnetworkworkload':'hostnetworkworkloads.platform.riscv.io','nodemigration':'nodemigrations.migration.riscv.io',
           'mpijob':'mpijobs.kubeflow.org'}
    for singular,qualified in names.items():
        if kind in (singular,qualified.split('.')[0]):return qualified
    return kind

def render(text, values):
    def token(match):
        key = match.group(1)
        if key not in values:
            raise ValueError('未配置模板变量：' + key)
        value = str(values[key])
        if '\n' in value or '\r' in value:
            raise ValueError('模板变量不允许换行：' + key)
        return value
    # Environment variables in embedded shell/Python programs are not expanded.
    result = re.sub(r'@@([A-Z0-9_]+)@@', token, text)
    return re.sub(r'(?m)^(\s*(?:replicas|containerPort|hostPort): )([\'\"])([0-9]+)\2\s*$',r'\1\3',result)

def canonical_image(raw, aliases=None):
    digest = raw.rsplit('@', 1)[-1]
    # This relation was verified by reading the actual manifest content, not by
    # guessing that different imageID strings mean the same image.
    aliases = aliases or {}
    return aliases.get(digest, digest)

class Test:
    def __init__(self, case_dir):
        self.case_dir = pathlib.Path(case_dir)
        self.platform = self.case_dir.parents[1]
        self.name = self.case_dir.name
        self.nonce = uuid.uuid4().hex[:8]
        self.ns = 'rv-a-' + self.name[:24] + '-' + self.nonce
        self.node = os.environ.get('TEST_NODE', '')
        self.image = os.environ.get('TEST_IMAGE', '')
        self.timeout = int(os.environ.get('TEST_TIMEOUT', '600'))
        if not self.node or not self.image:
            raise ValueError('请先配置 TEST_NODE 和 TEST_IMAGE，或设置 RV_ENV 指向配置文件')
        self.base = ['kubectl']
        if os.environ.get('KUBECONFIG'):
            self.base += ['--kubeconfig', os.environ['KUBECONFIG']]
        root = pathlib.Path(os.environ.get('TEST_RESULTS',str(pathlib.Path.home()/'rv-test-results/acceptance')))
        self.directory = root / (self.name + '-' + self.nonce)
        self.directory.mkdir(parents=True, mode=0o700)
        self.started = time.time()
        self.events = []
        self.cleanups = []
        self.node_obj = self.get('node',self.node,cluster=True)
        assert self.node_obj['metadata']['labels'].get('kubernetes.io/arch') == 'riscv64', 'TEST_NODE 不是 RISC-V 节点'
        assert not self.node_obj['spec'].get('unschedulable'), 'TEST_NODE 仍被 cordon'
        assert any(c['type']=='Ready' and c['status']=='True' for c in self.node_obj['status']['conditions']), 'TEST_NODE 未 Ready'
        self.values = {**os.environ,'NS':self.ns,'NONCE':self.nonce,'TEST_NODE':self.node,'TEST_IMAGE':self.image,
                       'NODE_HOSTNAME':self.node_obj['metadata']['labels']['kubernetes.io/hostname']}
        # Named values are substituted into YAML scalars; disallow YAML syntax
        # injection in identifiers and image references before rendering.
        for key in ('TEST_NODE','TEST_IMAGE','NODE_HOSTNAME'):
            if not re.fullmatch(r'[A-Za-z0-9._:/@+-]+',self.values[key]):
                raise ValueError('非法 YAML 标量：'+key)
        manifest={'apiVersion':'v1','kind':'Namespace','metadata':{'name':self.ns,
                  'labels':{'platform.riscv.io/acceptance':self.nonce}}}
        self.command(['create','-f','-'],input=json.dumps(manifest))

    def command(self, args, input=None, check=True, timeout=240):
        result=subprocess.run(self.base+list(args),input=input,text=True,capture_output=True,timeout=timeout)
        if check and result.returncode:
            raise RuntimeError('kubectl '+str(args[:3])+'\n'+result.stdout+result.stderr)
        return result

    def cmd(self,*args):
        return self.command(args).stdout

    def get(self,kind,name,cluster=False,missing=False,namespace=None):
        args=['get',resource_kind(kind),name,'-o','json']
        if not cluster:args += ['-n',namespace or self.ns]
        if missing:args += ['--ignore-not-found']
        text=self.cmd(*args)
        return json.loads(text) if text.strip() else None

    def items(self,kind,cluster=False,all_ns=False):
        args=['get',resource_kind(kind),'-o','json']
        if all_ns:args += ['-A']
        elif not cluster:args += ['-n',self.ns]
        return json.loads(self.cmd(*args)).get('items',[])

    def apply(self,filename):
        target=self.render_file(filename)
        self.cmd('apply','-f',str(target))
        self.record('applied',file=str(target))

    def render_file(self,filename):
        source=self.case_dir/'manifests'/filename
        target=self.directory/'rendered'/filename
        target.parent.mkdir(exist_ok=True)
        target.write_text(render(source.read_text(encoding='utf-8'),self.values),encoding='utf-8')
        return target

    def prepare_profile(self,scenario=False):
        """Reuse one existing profile; never create competing writers for a node."""
        peers=[p for p in self.items('nodeprofiles',all_ns=True) if p['spec']['node']==self.node]
        if len(peers)>1:raise Skip('节点已有多个互相冲突的 NodeProfile')
        self.profile_original=peers[0] if peers else None
        old=self.profile_original
        policy='feedback-'+self.nonce
        annotation='platform.riscv.io/allow-scenario'
        if scenario and old and old['metadata'].get('annotations',{}).get(annotation):raise Skip('现有 NodeProfile 已授权其他场景策略')
        self.values.update(PROFILE_NAME=old['metadata']['name'] if old else 'profile',PROFILE_NS=old['metadata']['namespace'] if old else self.ns,PROFILE_POLICY=policy)
        (self.directory/'profile-original.json').write_text(json.dumps(old,indent=2),encoding='utf-8')
        def restore():
            current=self.get('nodeprofile',self.values['PROFILE_NAME'],namespace=self.values['PROFILE_NS'],missing=True)
            key='platform.riscv.io/accept-'+self.nonce
            if old:
                if not current or current['metadata']['uid']!=old['metadata']['uid']:raise RuntimeError('NodeProfile 归属已变化，停止自动恢复')
                labels={k:v for k,v in current['spec'].get('labels',{}).items() if k!=key}
                annotations=dict(current['metadata'].get('annotations',{}))
                if scenario:
                    if annotations.get(annotation)!=policy:raise RuntimeError('场景授权发生变化，停止自动恢复')
                    annotations.pop(annotation,None)
                self.patch_guarded('nodeprofiles.platform.riscv.io',current,{'spec':{**current['spec'],'labels':labels},'metadata':{**current['metadata'],'annotations':annotations}})
                self.wait(lambda:key not in self.get('node',self.node,cluster=True)['metadata'].get('labels',{}),'原有节点配置已恢复')
            else:
                self.cmd('delete','nodeprofile',self.values['PROFILE_NAME'],'-n',self.values['PROFILE_NS'],'--ignore-not-found','--wait=true')
                self.cmd('label','node',self.node,key+'-')
        self.cleanups.append(restore)

    def patch_guarded(self,kind,current,fields):
        patch=[{'op':'test','path':'/metadata/uid','value':current['metadata']['uid']},
               {'op':'test','path':'/metadata/resourceVersion','value':current['metadata']['resourceVersion']}]
        patch += [{'op':'add','path':'/'+key,'value':value} for key,value in fields.items()]
        self.command(['patch',kind,current['metadata']['name'],'-n',current['metadata']['namespace'],'--type=json','--patch-file','-'],input=json.dumps(patch))

    def apply_profile(self,filename):
        target=self.render_file(filename)
        desired=json.loads(self.cmd('create','--dry-run=client','--validate=false','-f',str(target),'-o','json'))
        current=self.get('nodeprofile',self.values['PROFILE_NAME'],namespace=self.values['PROFILE_NS'],missing=True)
        if current:
            labels={**current['spec'].get('labels',{}),**desired['spec'].get('labels',{})}
            annotations={**current['metadata'].get('annotations',{}),**desired['metadata'].get('annotations',{})}
            self.patch_guarded('nodeprofiles.platform.riscv.io',current,{'spec':{**current['spec'],'labels':labels},'metadata':{**current['metadata'],'annotations':annotations}})
        else:
            desired['spec']['taints']=[v for v in self.node_obj['spec'].get('taints',[]) if v['key'].startswith('platform.riscv.io/')]
            self.command(['create','-f','-'],input=json.dumps(desired))
        self.record('profile-input',file=str(target),object=desired)

    def record(self,message,**values):
        self.events.append({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'message':message,**values})

    def wait(self,fn,message,timeout=None):
        end=time.monotonic()+(timeout or self.timeout)
        while time.monotonic()<end:
            value=fn()
            if value:self.record(message);return value
            time.sleep(3)
        raise TimeoutError(message)

    @staticmethod
    def is_ready(pod):
        return any(c['type']=='Ready' and c['status']=='True' for c in pod.get('status',{}).get('conditions',[]))

    def ready(self,name):
        def check():
            pod=self.get('pod',name)
            if pod.get('status',{}).get('phase')=='Failed':raise AssertionError(pod.get('status'))
            return pod if self.is_ready(pod) else None
        return self.wait(check,name+' Ready')

    def unscheduled(self,name):
        def check():
            p=self.get('pod',name)
            if p['spec'].get('nodeName'):raise AssertionError('Pod 意外获得节点：'+name)
            return p if any(c['type']=='PodScheduled' and c['status']=='False' and c.get('reason')=='Unschedulable' for c in p.get('status',{}).get('conditions',[])) else None
        self.record('unschedulable',pod=self.wait(check,name+' Unschedulable'))

    def rollout(self,name,timeout=300):
        self.command(['rollout','status','deployment/'+name,'-n',self.ns,'--timeout='+str(timeout)+'s'],timeout=timeout+30)

    def same_images(self,ids):
        aliases={'sha256:8bdc6843680f510de914c73a5ade3ad97f2f13999036dd0c532bc0249c9141d9':'sha256:f662eb868fb97d08721bb9e3e68bc86db19fe5867d38134d83bd4cd5fce3e31c'}
        if os.environ.get('IMAGE_DIGEST_MAP'):
            aliases.update(json.loads(pathlib.Path(os.environ['IMAGE_DIGEST_MAP']).read_text()))
        normalized=[canonical_image(x,aliases) for x in ids]
        assert ids and len(set(normalized))==1, {'imageIDs':ids,'normalized':normalized}
        self.record('image-identity-verified',imageIDs=ids,normalized=normalized[0])

    def finish(self,error=None):
        status='SKIP' if isinstance(error,Skip) else 'FAIL' if error else 'PASS'
        for kind in ('pods','events','nodemigrations.migration.riscv.io'):
            result=self.command(['get',kind,'-n',self.ns,'-o','json'],check=False)
            (self.directory/(kind+'.json')).write_text(result.stdout or result.stderr,encoding='utf-8')
        # Stop controllers that can keep reapplying test node labels before
        # removing those labels. These objects belong to our own namespace.
        if self.cleanups:
            for kind in ('scenariopolicies.platform.riscv.io',):
                result=self.command(['delete',kind,'--all','-n',self.ns,'--ignore-not-found','--wait=true','--timeout=60s'],check=False,timeout=90)
                if result.returncode:self.record('cleanup-warning',detail=result.stderr)
            for fn in reversed(self.cleanups):
                try:fn()
                except Exception as nested:
                    self.record('cleanup-failed',detail=str(nested))
                    status='FAIL'
        result={'case':self.name,'namespace':self.ns,'ownership':self.nonce,'status':status,'startedAtEpoch':self.started,
                'durationSeconds':time.time()-self.started,'testNode':self.node,'testImage':self.image,
                'events':self.events,'error':str(error) if error else None,'evidence':'real-kubernetes',
                'namespaceRetained':status=='FAIL' or os.environ.get('KEEP_TEST_NAMESPACE')=='1'}
        if not result['namespaceRetained']:
            deleted=self.command(['delete','namespace',self.ns,'--wait=false'],check=False)
            if deleted.returncode:
                result['namespaceRetained']=True;result['cleanupError']=deleted.stderr;result['status']='FAIL'
        (self.directory/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        print('结果目录：'+str(self.directory))
        return 77 if result['status']=='SKIP' else 1 if result['status']=='FAIL' else 0

def run(fn,case_dir):
    os.umask(0o077)
    test=Test(case_dir);error=None
    try:fn(test)
    except Exception as caught:error=caught
    sys.exit(test.finish(error))

def cleanup(report):
    result=json.loads(pathlib.Path(report).read_text(encoding='utf-8'))
    ns=result['namespace'];nonce=result['ownership']
    assert ns.startswith('rv-a-') and re.fullmatch('[0-9a-f]{8}',nonce)
    base=['kubectl']
    if os.environ.get('KUBECONFIG'):base += ['--kubeconfig',os.environ['KUBECONFIG']]
    obj=json.loads(subprocess.check_output(base+['get','namespace',ns,'-o','json'],text=True))
    assert obj['metadata'].get('labels',{}).get('platform.riscv.io/acceptance')==nonce,'命名空间归属不匹配'
    migrations=subprocess.run(base+['get','nodemigrations.migration.riscv.io','-n',ns,'-o','json'],capture_output=True,text=True)
    if migrations.returncode==0:
        items=json.loads(migrations.stdout).get('items',[])
        assert all(x.get('status',{}).get('phase') in ('Succeeded','Failed') and not x['metadata'].get('finalizers') for x in items),'迁移事务尚未安全结束；先按手册恢复，禁止强删 finalizer'
    subprocess.run(base+['delete','namespace',ns,'--wait=true','--timeout=180s'],check=True)

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='cleanup':cleanup(sys.argv[2])
    else:raise SystemExit('用法：support.py cleanup /path/to/report.json')
