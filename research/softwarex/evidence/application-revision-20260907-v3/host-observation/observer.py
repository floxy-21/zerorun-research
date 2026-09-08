"""Trusted transport plus prospective host/guest continuity observation for v3."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import shlex
import paramiko

BASE = Path('D:/ZeroRun-revision-20260907')
INPUTS = BASE / 'application-v3-preparation'
LOCAL = BASE / 'application-revision-20260907-v3'
REMOTE_INPUT = '/home/floxy/zerorun-v3-inputs-20260907'
REMOTE_OUTPUT = '/home/floxy/zerorun-application-revision-20260907-v3'
VBOX = 'C:/Program Files/Oracle/VirtualBox/VBoxManage.exe'
EXPECTED_KEY = 'SHA256:5rUVqDOVpFiVuEmQuKFaavxhvu1BWFt7PKtGgs9YNfY'
DELTA = 'a32b0595-4e14-4fd2-89af-d6ec6094b9a1'

def utc():
    return datetime.now(timezone.utc).isoformat()

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def save(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')

def row(path, base):
    return {'path': path.relative_to(base).as_posix(), 'bytes': path.stat().st_size, 'sha256': sha(path)}

def ssh(client, command, timeout=30):
    _, out, err = client.exec_command(command, timeout=timeout)
    raw, errors = out.read(), err.read()
    status = out.channel.recv_exit_status()
    if status:
        raise RuntimeError('read-only guest probe failed: ' + errors.decode(errors='replace')[-1000:])
    return raw

def main():
    assert not LOCAL.exists()
    LOCAL.mkdir()
    host = LOCAL / 'host-observation'
    host.mkdir()
    (host / 'observer.py').write_bytes(Path(__file__).read_bytes())
    names = ['revision_application_vm_v3.py', 'build_image_no_cache_v3.py', 'APPLICATION_REVISION_V3_AMENDMENT.md', 'OUTCOME_CLASSIFICATION_V3.md']
    protocol = {'schema':'zerorun.host-continuity-protocol.v3','created_utc':utc(),
                'endpoint':'127.0.0.1:2222','expected_host_key':EXPECTED_KEY,
                'expected_delta_uuid':DELTA,'sample_interval_seconds':10,
                'maximum_sample_start_gap_seconds':45,'elapsed_bracket_slack_seconds':2,
                'expected_vm_state':'running','stable_guest_boot_id_required':True,
                'sample_errors_qualify_as_uninterrupted':False,
                'guest_command_span_must_be_covered':True,
                'observer':row(host/'observer.py',host),
                'input_files':[row(INPUTS/name,INPUTS) for name in names],
                'remote_output':REMOTE_OUTPUT,'source_edits':False,'other_benchmarks_permitted':False,
                'monitoring_is_part_of_recorded_environment':True}
    save(host/'protocol.json',protocol)
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home()/'.ssh/known_hosts'))
    client.connect('127.0.0.1',port=2222,username='floxy',password=os.environ['ZERORUN_VM_PASSWORD'],
                   allow_agent=False,look_for_keys=False,timeout=15,auth_timeout=15,banner_timeout=15)
    key=client.get_transport().get_remote_server_key()
    actual_key='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
    assert actual_key==EXPECTED_KEY
    stop=threading.Event(); sample_lock=threading.Lock(); samples=[]; phase_seen=set(); driver_exit=None; error=None; transfer=None
    log=host/'continuity.log'
    probe = "import datetime,json,pathlib,time; root=pathlib.Path('"+REMOTE_OUTPUT+"'); start=root/'record-only/commands/main-clean.started.json'; print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'monotonic_seconds':time.monotonic(),'uptime_seconds':float(pathlib.Path('/proc/uptime').read_text().split()[0]),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'image_started':(root/'record-only/commands/fresh-image-build-no-cache.started.json').is_file(),'main_started':start.is_file(),'main_started_utc':json.loads(start.read_text())['started_utc'] if start.is_file() else None,'main_finished':(root/'record-only/commands/main-clean.json').is_file(),'case_completion_files':len(list((root/'handoff-main-clean-v1/run/cases').glob('*/completion.json')))}))"
    def sample_unlocked():
        item={'sample_index':len(samples),'host_utc_start':utc(),'host_monotonic_start':time.monotonic(),'error':None}
        try:
            state=subprocess.run([VBOX,'showvminfo','ZeroRun','--machinereadable'],capture_output=True,text=True,timeout=15,check=True)
            fields=dict(line.split('=',1) for line in state.stdout.splitlines() if '=' in line)
            item.update(vm_state=fields.get('VMState','').strip('"'),
                        vm_state_change_time=fields.get('VMStateChangeTime','').strip('"'),
                        delta_uuid=fields.get('"SATA-ImageUUID-0-0"','').strip('"'),
                        host_c_free_bytes=shutil.disk_usage('C:/').free,host_d_free_bytes=shutil.disk_usage('D:/').free)
            item['guest']=json.loads(ssh(client,'python3 -B -c '+shlex.quote(probe)))
            for phase in ('image_started','main_started','main_finished'):
                if item['guest'][phase] and phase not in phase_seen:
                    phase_seen.add(phase)
                    print(json.dumps({'phase':phase,'host_utc':utc(),'guest':item['guest']},sort_keys=True),flush=True)
        except Exception as exc:
            item['error']={'type':type(exc).__name__,'message':str(exc)}
        item.update(host_utc_end=utc(),host_monotonic_end=time.monotonic())
        samples.append(item)
        with log.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(item,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
    def sample():
        with sample_lock:
            sample_unlocked()
    def monitor():
        while not stop.wait(10):
            sample()
    try:
        with client.open_sftp() as sftp:
            try:
                sftp.stat(REMOTE_INPUT)
                raise RuntimeError('new remote input directory already exists')
            except FileNotFoundError:
                sftp.mkdir(REMOTE_INPUT)
            for name in names:
                raw=(INPUTS/name).read_bytes()
                with sftp.open(REMOTE_INPUT+'/'+name,'wx') as stream:
                    stream.write(raw)
                with sftp.open(REMOTE_INPUT+'/'+name,'rb') as stream:
                    assert hashlib.sha256(stream.read()).hexdigest()==hashlib.sha256(raw).hexdigest()
        sample()
        assert samples[0]['error'] is None and samples[0]['vm_state']=='running' and samples[0]['delta_uuid']==DELTA
        thread=threading.Thread(target=monitor,daemon=True);thread.start()
        argv=['python3','-B',REMOTE_INPUT+'/revision_application_vm_v3.py','--amendment',REMOTE_INPUT+'/APPLICATION_REVISION_V3_AMENDMENT.md','--no-cache-wrapper',REMOTE_INPUT+'/build_image_no_cache_v3.py']
        save(host/'launch.json',{'started_utc':utc(),'argv':argv,'host_key_verified':True,'credentials_logged':False})
        _,out,err=client.exec_command(shlex.join(argv),timeout=3000)
        channel=out.channel
        with (host/'driver.stdout.log').open('xb') as stdout,(host/'driver.stderr.log').open('xb') as stderr:
            while True:
                if channel.recv_ready():
                    raw=channel.recv(65536);stdout.write(raw);stdout.flush()
                    print(raw.decode(errors='replace'),end='',flush=True)
                if channel.recv_stderr_ready():
                    raw=channel.recv_stderr(65536);stderr.write(raw);stderr.flush()
                    print(raw.decode(errors='replace'),end='',flush=True)
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(.1)
        driver_exit=channel.recv_exit_status()
        stop.set();thread.join(timeout=45)
        assert not thread.is_alive(), 'host observer did not stop before sealing'
        sample()
        with client.open_sftp() as sftp:
            with sftp.open(REMOTE_OUTPUT+'/transfer.json','rb') as stream:
                transfer=json.loads(stream.read())
            save(LOCAL/'guest-transfer.json',transfer)
            archive=LOCAL/'record-only.tar.gz'
            assert transfer['path']==REMOTE_OUTPUT+'/record-only.tar.gz'
            with sftp.open(transfer['path'],'rb') as remote,archive.open('xb') as target:
                while True:
                    block=remote.read(1024*1024)
                    if not block: break
                    target.write(block)
            assert archive.stat().st_size==transfer['bytes'] and sha(archive)==transfer['sha256']
        with tarfile.open(archive,'r:gz') as tar:
            members=tar.getmembers();names_seen=set()
            for member in members:
                path=PurePosixPath(member.name)
                assert member.isfile() and not path.is_absolute() and '..' not in path.parts and path.parts[0]=='record-only'
                assert member.name not in names_seen and member.size<80*1024*1024
                names_seen.add(member.name)
            for member in members:
                target=LOCAL/Path(*PurePosixPath(member.name).parts)
                assert not target.exists() and target.resolve().is_relative_to(LOCAL)
                target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as original,target.open('xb') as saved:
                    shutil.copyfileobj(original,saved,1024*1024)
        manifest=json.loads((LOCAL/'record-only/RECORD_MANIFEST.json').read_text())
        for item in manifest['files']:
            target=LOCAL/'record-only'/item['path']
            assert target.resolve().is_relative_to(LOCAL/'record-only')
            assert target.stat().st_size==item['bytes'] and sha(target)==item['sha256']
        actual={p.relative_to(LOCAL/'record-only').as_posix() for p in (LOCAL/'record-only').rglob('*') if p.is_file()}
        assert actual=={r['path'] for r in manifest['files']}|{'RECORD_MANIFEST.json'}
    except Exception as exc:
        error={'type':type(exc).__name__,'message':str(exc)}
    finally:
        stop.set()
        if 'thread' in locals(): thread.join(timeout=45)
        if 'thread' in locals() and thread.is_alive():
            client.close();thread.join(timeout=45)
            error=error or {'type':'ObserverStopError','message':'observer needed transport close to stop'}
        assert 'thread' not in locals() or not thread.is_alive(), 'refusing to seal concurrently written observations'
        if client.get_transport() is not None and client.get_transport().is_active():
            sample()
        client.close()
        files=[row(p,host) for p in sorted(host.iterdir()) if p.is_file()]
        completion={'schema':'zerorun.host-continuity-completion.v3','completed_utc':utc(),
                    'driver_exit_code':driver_exit,'error':error,'sample_count':len(samples),
                    'sample_errors':sum(item['error'] is not None for item in samples),
                    'host_key_verified':True,'files':files,'guest_transfer':transfer,
                    'guest_record_manifest':row(LOCAL/'record-only/RECORD_MANIFEST.json',LOCAL) if (LOCAL/'record-only/RECORD_MANIFEST.json').is_file() else None,
                    'archive_downloaded_and_verified':error is None and transfer is not None,
                    'uninterrupted_claim_requires_independent_reconciliation':True,
                    'vm_stop_or_savestate_requested':False}
        save(host/'completion.json',completion)
        print(json.dumps({'host_completion':str(host/'completion.json'),'driver_exit_code':driver_exit,'error':error,'sample_count':len(samples),'transfer':transfer},sort_keys=True),flush=True)
    return int(error is not None or driver_exit!=0)

if __name__=='__main__':
    raise SystemExit(main())
