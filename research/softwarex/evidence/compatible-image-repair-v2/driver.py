"""Build and attest a separately bound compatible application environment."""
import email
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile

sys.dont_write_bytecode=True
ROOT=Path('/home/floxy/zerorun-compatibility-preflight-20260907-v2')
INPUT=Path('/home/floxy/zerorun-compatibility-inputs-20260907-v1')
OUTPUT=ROOT/'image-repair-v2'
REGISTRY='docker.io/library/registry@sha256:46faa9a1ae6813194b53921a370f2f4f8c5e1aae228a89bceafef5847a6a3278'
BASE_TAG='docker.io/library/python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a'
REQUIREMENTS='pytest==8.4.2\npretend==1.0.9\nsix==1.17.0\nPyYAML==6.0.2\ntoml==0.10.2\nDjango==3.2.25\n'
sys.path.insert(0,str(ROOT/'harness'))
from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_image_v2 import build_image as b
assert not OUTPUT.exists()
OUTPUT.mkdir();commands=OUTPUT/'commands';commands.mkdir();context=OUTPUT/'context';context.mkdir()
wheelhouse=context/'wheelhouse';wheelhouse.mkdir()
(context/'requirements.in').write_text(REQUIREMENTS,encoding='utf-8')
(OUTPUT/'driver.py').write_bytes(Path(__file__).read_bytes())
(OUTPUT/'PYTEST_COMPATIBILITY_AMENDMENT.md').write_bytes((INPUT/'PYTEST_COMPATIBILITY_AMENDMENT.md').read_bytes())
h.save(OUTPUT/'protocol.json',{'schema':'zerorun.compatibility-image-candidate.v2','started_utc':h.utc(),
    'base_acquisition_tag':BASE_TAG,'registry_image':REGISTRY,'requirements_in':REQUIREMENTS,
    'driver':h.record(OUTPUT/'driver.py','driver.py'),
    'amendment':h.record(OUTPUT/'PYTEST_COMPATIBILITY_AMENDMENT.md','PYTEST_COMPATIBILITY_AMENDMENT.md'),
    'download_network':True,'build_network':'none','docker_build_cache':False,
    'historical_environment_reproduced':False,'source_or_assertion_changes':False,
    'guest_meminfo':Path('/proc/meminfo').read_text(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()})
def command(label,argv,timeout=300,stdin=None):return b.command(commands,label,argv,timeout,stdin)
started=time.monotonic();registry_id=None;result=None;error=None
try:
    command('base-pull',['docker','pull','--platform','linux/amd64',BASE_TAG],timeout=600)
    base_rows=json.loads(command('base-tag-inspect',['docker','image','inspect',BASE_TAG]))
    assert len(base_rows)==1
    base=next(x for x in base_rows[0]['RepoDigests'] if x.startswith(('python@sha256:','docker.io/library/python@sha256:')))
    base_meta=b.image_metadata(command('base-pinned-inspect',['docker','image','inspect',base]),base)
    command('download-wheels',['docker','run','--rm','--pull=never','--platform','linux/amd64','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','512m',
        '--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
        '--mount',f'type=bind,src={context},dst=/out',base,'python','-B','-m','pip','--isolated',
        'download','--disable-pip-version-check','--no-cache-dir','--only-binary=:all:',
        '--dest','/out/wheelhouse','-r','/out/requirements.in'],timeout=600)
    packages=[]
    for path in sorted(wheelhouse.iterdir()):
        assert path.suffix=='.whl'
        with zipfile.ZipFile(path) as wheel:
            names=[n for n in wheel.namelist() if n.endswith('.dist-info/METADATA')];assert len(names)==1
            metadata=email.message_from_bytes(wheel.read(names[0]))
        packages.append({'name':metadata['Name'],'version':metadata['Version'],
            'wheel':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    assert len({p['name'].lower().replace('_','-') for p in packages})==len(packages)
    lock=''.join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n" for p in packages)
    (context/'requirements.lock').write_text(lock,encoding='utf-8')
    dockerfile=(f'FROM {base}\nCOPY wheelhouse/ /opt/zerorun-wheelhouse/\n'
        'COPY requirements.lock /opt/zerorun-requirements.lock\n'
        'RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index --find-links=/opt/zerorun-wheelhouse --require-hashes -r /opt/zerorun-requirements.lock\n'
        'ENV PYTHONPATH=/workspace/src:/workspace\n'
        'LABEL org.opencontainers.image.title=ZeroRun-compatible-handoff-dependencies\n')
    (context/'Dockerfile').write_text(dockerfile,encoding='utf-8')
    context_rows=b.inventory(context)
    h.save(OUTPUT/'locked-inputs.json',{'base':base_meta,'packages':packages,
        'dockerfile':h.record(context/'Dockerfile','Dockerfile'),'context_files':context_rows,
        'recorded_before_build':h.utc()})
    token=uuid.uuid4().hex;tag=f'127.0.0.1:19559/zerorun-compatible-v2:{token}'
    command('offline-build',['docker','build','--no-cache','--pull=false','--network=none','--tag',tag,str(context)],timeout=600)
    assert b.inventory(context)==context_rows
    probe="import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.version,'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))"
    probe_raw=command('import-probe',['docker','run','--rm','--pull=never','--read-only','--network=none',
        '--cap-drop','ALL','--security-opt','no-new-privileges',tag,'python','-B','-c',probe],timeout=60)
    probe_result=json.loads(probe_raw);assert probe_result['python'].startswith('3.10.')
    for p in packages:assert probe_result['packages'][p['name']]==p['version']
    command('pip-check',['docker','run','--rm','--pull=never','--read-only','--network=none',tag,'python','-B','-m','pip','check'],timeout=60)
    registry_meta=b.image_metadata(command('registry-inspect',['docker','image','inspect',REGISTRY]),REGISTRY)
    store=OUTPUT/'registry-store';store.mkdir()
    registry_id=command('registry-create',['docker','create','--name','zerorun-compatible-registry-'+token,
        '--pull=never','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{os.getuid()}:{os.getgid()}','--pids-limit','128','--memory','256m',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=16m','--publish','127.0.0.1:19559:5000',
        '--mount',f'type=bind,src={store},dst=/var/lib/registry',REGISTRY]).decode().strip()
    assert re.fullmatch(r'[0-9a-f]{64}',registry_id)
    command('registry-start',['docker','start',registry_id])
    assert b.wait_registry_ready('http://127.0.0.1:19559/v2/')
    push=command('push',['docker','push',tag],timeout=300)
    digests=set(re.findall(rb'digest: (sha256:[0-9a-f]{64})',push));assert len(digests)==1
    reference=tag.rsplit(':',1)[0]+'@'+digests.pop().decode()
    command('pull-digest',['docker','pull','--platform','linux/amd64',reference],timeout=300)
    identity=b.image_metadata(command('derived-inspect',['docker','image','inspect',reference]),reference)
    result={'image':identity,'base':base_meta,'packages':packages,'runtime_probe':probe_result,
        'context_unchanged':b.inventory(context)==context_rows,'image_publicly_pullable':False,
        'offline_hash_locked_build':True,'build_cache_disabled':True}
except Exception as caught:
    error={'type':type(caught).__name__,'message':str(caught)}
finally:
    cleanup=True
    if registry_id:
        try:command('registry-remove',['docker','container','rm','--force',registry_id],timeout=30)
        except Exception as caught:
            cleanup=False;error=error or {'type':type(caught).__name__,'message':str(caught)}
    final={'schema':'zerorun.compatibility-image-candidate-completion.v2','completed_utc':h.utc(),
        'passed':error is None and result is not None and cleanup,'error':error,'result':result,
        'registry_cleanup':cleanup,'setup_outer_seconds':time.monotonic()-started,
        'protocol':h.record(OUTPUT/'protocol.json','protocol.json'),'workload_preflight_passed':False}
    h.save(OUTPUT/'completion.json',final)
    print(json.dumps(final),flush=True)
raise SystemExit(int(not final['passed']))
