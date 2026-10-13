#!/usr/bin/env python3
"""Write user-owned deployment configuration. Does not change live services."""
import argparse, ipaddress, json, os, pathlib, re, shlex

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--master-ip',required=True)
    p.add_argument('--worker',action='append',required=True,help='NAME,IP,SSH_USER; repeat for more workers')
    p.add_argument('--output-dir',default=str(pathlib.Path.home()))
    p.add_argument('--kubeconfig',default=str(pathlib.Path.home()/'.kube/rv-admin.kubeconfig'))
    p.add_argument('--image',default='localhost/rv-communication:0.2.0')
    p.add_argument('--force',action='store_true',help='replace only the four named user configuration files')
    args=p.parse_args();ipaddress.IPv4Address(args.master_ip)
    workers=[]
    for value in args.worker:
        name,ip,user=value.split(',')
        if not re.fullmatch('[a-z0-9.-]+',name) or not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_-]*',user):raise ValueError('Invalid worker name/user')
        ipaddress.IPv4Address(ip)
        if name in {w[0] for w in workers}:raise ValueError('Duplicate worker name')
        workers.append((name,ip,user))
    output=pathlib.Path(args.output_dir).expanduser().resolve();output.mkdir(parents=True,exist_ok=True)
    names=['rv-platform.json','rv-migration.json','rv-monitoring.json','rv-platform.env']
    if not args.force and any((output/n).exists() for n in names):raise SystemExit('配置文件已存在。保留旧文件，或明确使用 --force 生成新配置。')
    os.umask(0o077)
    platform=pathlib.Path(__file__).resolve().parent
    src=platform.parent;release=src.parent.parent
    first=workers[0]
    controls={'kubeconfig':'/etc/rv-platform/platform.kubeconfig','prometheusURL':'http://'+first[1]+':30090','pollSeconds':10,'modules':['resource','network-host','scenario']}
    migration={'kubeconfig':'/etc/rv-platform/migration.kubeconfig','nodeHelper':'/usr/local/libexec/rv-migrate-runtime','allowedArchitectures':['riscv64'],'operationTimeoutSeconds':1200,'restoreTimeoutSeconds':600,'pollSeconds':3,'nodes':{name:{'host':ip,'user':user,'sudo':True,'port':22,'identityFile':'/etc/rv-platform/migration_ed25519'} for name,ip,user in workers}}
    monitoring={'namespace':'rv-monitoring','prometheusImage':'localhost/rv-prometheus:3.13.4','nodeTargets':[ip+':9108' for name,ip,user in workers],'nodeSelector':{'kubernetes.io/hostname':first[0]},'service':{'type':'NodePort','prometheusNodePort':30090},'storage':{'enabled':False},'retention':'7d'}
    for name,obj in zip(names,[controls,migration,monitoring]):
        path=output/name;path.write_text(json.dumps(obj,indent=2)+'\n',encoding='utf-8');path.chmod(0o600)
    env={'RV_RELEASE':str(release),'RV_SRC':str(src),'RV_OFFLINE':str(release/'offline'),'KUBECONFIG':str(pathlib.Path(args.kubeconfig).expanduser().resolve()),'MASTER_IP':args.master_ip,'WORKER_IP':first[1],'TEST_NODE':first[0],'TEST_IMAGE':args.image,'TARGET_NODE':workers[1][0] if len(workers)>1 else '', 'PROMETHEUS_URL':controls['prometheusURL'],'TEST_RESULTS':str(pathlib.Path.home()/'rv-test-results/acceptance'),'MPI_WORKERS':'1','TEST_TIMEOUT':'600','KEEP_TEST_NAMESPACE':'0'}
    path=output/names[-1];path.write_text(''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in env.items()),encoding='utf-8');path.chmod(0o600)
    print('用户配置已生成；尚未安装或重启任何服务。')
    for n in names:print(output/n)

if __name__=='__main__':main()
