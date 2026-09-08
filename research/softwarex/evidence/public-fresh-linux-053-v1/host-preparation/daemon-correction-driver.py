from pathlib import Path
import json,subprocess,sys,time
sys.path.insert(0,str(Path(__file__).parent))
from fresh_public_wsl_v1 import ROOT,DISTRO,command,save,stamp
(ROOT/'daemon-correction-driver.py').write_bytes(Path(__file__).read_bytes())
record={'schema':'zerorun.fresh-wsl-daemon-lifecycle-correction.v2','started_utc':stamp(),'passed':False,'failure':None,'prior_attempt':'prerequisites.json','intervention':'Retain a hidden foreground WSL client for dockerd because the initial shell-background launch did not leave a usable daemon. No ZeroRun code, fixture, source pin or image digest is changed.'}
try:
    command('daemon-correction-preflight',['wsl.exe','-d',DISTRO,'-u','root','--exec','/bin/sh','-c','ps -ef; command -v dockerd; ls -ld /var/lib/docker /run/docker /var/run/docker.sock 2>/dev/null; cat /var/log/zerorun-fresh-dockerd.log; true'],30)
    argv=['wsl.exe','-d',DISTRO,'-u','root','--exec','dockerd','--data-root=/var/lib/docker','--exec-root=/run/docker','--pidfile=/run/zerorun-fresh-dockerd.pid','--host=unix:///var/run/docker.sock','--iptables=false','--bridge=none']
    with (ROOT/'dockerd-v2.stdout.txt').open('xb') as stdout,(ROOT/'dockerd-v2.stderr.txt').open('xb') as stderr:
        proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
    record.update(host_pid=proc.pid,argv=argv)
    save('dockerd-v2-launch.json',{'argv':argv,'host_pid':proc.pid,'started_utc':stamp(),'stdout':'dockerd-v2.stdout.txt','stderr':'dockerd-v2.stderr.txt','window_hidden':True})
    command('docker-v2-ready',['wsl.exe','-d',DISTRO,'-u','root','--exec','/bin/sh','-c','i=0; while [ "$i" -lt 30 ]; do if docker info >/dev/null 2>&1; then docker version; docker image ls --digests; cat /run/zerorun-fresh-dockerd.pid; exit 0; fi; i=$((i+1)); sleep 1; done; exit 1'],45)
    command('package-version-inventory',['wsl.exe','-d',DISTRO,'-u','root','--exec','/bin/sh','-c','apk info -v; python3 --version; git --version; cat /etc/os-release; uname -a; df -h /; cat /etc/apk/repositories'],30)
    command('nonroot-docker-empty-images',['wsl.exe','-d',DISTRO,'-u','reviewer','--exec','/usr/bin/env','-i','HOME=/home/reviewer','PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','docker','image','ls','--digests'],30)
    record['passed']=True
except Exception as e:record['failure']={'type':type(e).__name__,'message':str(e)}
record['finished_utc']=stamp();save('daemon-correction.json',record);print(json.dumps(record),flush=True)
raise SystemExit(0 if record['passed'] else 1)
