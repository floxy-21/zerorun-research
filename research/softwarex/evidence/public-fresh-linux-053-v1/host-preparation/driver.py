from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,subprocess,time,urllib.request

ROOT=Path('C:/ZeroRun-recovery-20260908/public-fresh-reproduction-v1')
DISTRO='ZeroRunFresh053_20260908v1'
URL='https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-minirootfs-3.24.1-x86_64.tar.gz'
def stamp():return datetime.now(timezone.utc).isoformat()
def save(name,value):
    (ROOT/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')
def ident(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def command(label,argv,timeout=180):
    start=time.monotonic();row={'label':label,'argv':argv,'started_utc':stamp(),'returncode':None,'error':None}
    print(json.dumps({'starting':label}),flush=True)
    environment={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA'}}
    try:
        p=subprocess.run(argv,input=b'',stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,timeout=timeout)
        out,err=p.stdout,p.stderr;row['returncode']=p.returncode
    except subprocess.TimeoutExpired as e:
        out,err=e.stdout or b'',e.stderr or b'';row['error']={'type':'TimeoutExpired','message':'bounded host command timed out'}
    for kind,raw in [('stdout',out),('stderr',err)]:
        path=label+'.'+kind+'.txt';(ROOT/path).write_bytes(raw);row[kind]={'path':path,**ident(raw)}
    row.update(finished_utc=stamp(),elapsed_seconds=time.monotonic()-start);save(label+'.json',row)
    if row['returncode']!=0:raise RuntimeError('preserved command refused: '+label)
    return out
def download(label,url):
    start=time.monotonic();row={'url':url,'started_utc':stamp(),'credentials_supplied':False}
    with urllib.request.urlopen(url,timeout=60) as response:
        raw=response.read(50*1024*1024)
        if response.read(1):raise ValueError('rootfs download exceeded50MiB')
        row.update(status=response.status,final_url=response.url)
    (ROOT/label).write_bytes(raw);row.update(ident(raw),finished_utc=stamp(),elapsed_seconds=time.monotonic()-start);save(label+'.download.json',row)
    return raw
def main():
    ROOT.mkdir()
    (ROOT/'driver.py').write_bytes(Path(__file__).read_bytes())
    result={'schema':'zerorun.fresh-public-wsl-preparation.v1','started_utc':stamp(),'distro':DISTRO,'rootfs_url':URL,'passed':False,'failure':None,'existing_ubuntu_modified':False,'old_virtualbox_vm_used':False,'D_accessed':False,'human_independent':False}
    try:
        command('wsl-list-before',['wsl.exe','--list','--verbose'])
        published=download('alpine-rootfs.sha256',URL+'.sha256').decode().split()[0]
        raw=download('alpine-rootfs.tar.gz',URL)
        assert len(published)==64 and hashlib.sha256(raw).hexdigest()==published
        result['publisher_rootfs_sha256']=published
        command('wsl-import',['wsl.exe','--import',DISTRO,str(ROOT/'distro'),str(ROOT/'alpine-rootfs.tar.gz'),'--version','2'],180)
        command('fresh-os-preflight',['wsl.exe','-d',DISTRO,'-u','root','--exec','/bin/sh','-c','uname -a; cat /etc/os-release; id; df -h /; cat /etc/apk/repositories; test ! -e /var/lib/docker; test ! -e /home/reviewer'],90)
        result['passed']=True
    except Exception as e:result['failure']={'type':type(e).__name__,'message':str(e)}
    result['finished_utc']=stamp();save('preparation.json',result);print(json.dumps(result),flush=True)
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
