from pathlib import Path
import json,sys,time
sys.path.insert(0,str(Path(__file__).parent))
from fresh_public_wsl_v1 import ROOT,DISTRO,command,save,stamp
def guest(label,script,timeout=300):
    return command(label,['wsl.exe','-d',DISTRO,'-u','root','--exec','/bin/sh','-c',script],timeout)
record={'schema':'zerorun.fresh-wsl-prerequisites.v1','started_utc':stamp(),'passed':False,'failure':None,'scope':'Only the newly imported Alpine distro; no existing Ubuntu packages or old VirtualBox image are used.'}
(ROOT/'setup-driver.py').write_bytes(Path(__file__).read_bytes())
try:
    guest('apk-prerequisites','apk add --no-cache ca-certificates python3 py3-pip py3-virtualenv git docker',600)
    guest('new-reviewer-account','adduser -D -u 1000 reviewer && addgroup reviewer docker && mkdir -p /home/reviewer/zerorun-fresh-v1 && chown reviewer:reviewer /home/reviewer/zerorun-fresh-v1 && chmod 700 /home/reviewer/zerorun-fresh-v1 && id reviewer')
    guest('empty-docker-state','test ! -e /var/lib/docker && test ! -e /home/reviewer/.docker && test ! -e /home/reviewer/.cache && printf "Fresh daemon storage and user Docker/cache directories absent\\n"')
    guest('new-docker-daemon-launch','nohup dockerd --data-root=/var/lib/docker --exec-root=/run/docker --pidfile=/run/zerorun-fresh-dockerd.pid --host=unix:///var/run/docker.sock --iptables=false --bridge=none > /var/log/zerorun-fresh-dockerd.log 2>&1 < /dev/null & echo $!',30)
    guest('docker-ready','i=0; while [ "$i" -lt 30 ]; do if docker info >/dev/null 2>&1; then docker version; docker image ls --digests; cat /run/zerorun-fresh-dockerd.pid; exit 0; fi; i=$((i+1)); sleep 1; done; cat /var/log/zerorun-fresh-dockerd.log; exit 1',45)
    guest('package-version-inventory','apk info -v; python3 --version; git --version; cat /etc/os-release; uname -a; df -h /; cat /etc/apk/repositories')
    command('nonroot-docker-empty-images',['wsl.exe','-d',DISTRO,'-u','reviewer','--exec','/usr/bin/env','-i','HOME=/home/reviewer','PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','docker','image','ls','--digests'],60)
    record['passed']=True
except Exception as e:record['failure']={'type':type(e).__name__,'message':str(e)}
record['finished_utc']=stamp();save('prerequisites.json',record);print(json.dumps(record),flush=True)
raise SystemExit(0 if record['passed'] else 1)
