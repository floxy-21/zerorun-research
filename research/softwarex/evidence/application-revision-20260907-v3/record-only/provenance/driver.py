"""Prospectively bounded, sequential public-source application revision."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone

sys.dont_write_bytecode = True
HARNESS = '0528905a52b74df78aa4e5a09219df34620282dd'
ENGINE = 'ebf2884df12573d63f45813200e0675288d12096'
PUBLIC = 'https://github.com/floxy-21/zerorun-research.git'
BASE = 'docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef'
REGISTRY = 'docker.io/library/registry@sha256:46faa9a1ae6813194b53921a370f2f4f8c5e1aae228a89bceafef5847a6a3278'
MAIN_SHA = '4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997'
PILOT_SHA = '2017a0414475b7a0bc19da04cc824c5d49c23b7713e6940865c5e87985756b59'

def utc():
    return datetime.now(timezone.utc).isoformat()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')

def file_row(path, base):
    raw = path.read_bytes()
    return {'path': path.relative_to(base).as_posix(), 'bytes': len(raw), 'sha256': sha(raw)}

def git_env():
    # Public Git transport has no credential helper, askpass or user Git config.
    env = {k:v for k,v in os.environ.items() if not k.startswith(('GIT_', 'GCM_', 'GH_', 'GITHUB_'))
           and k not in {'SSH_ASKPASS', 'SSH_AUTH_SOCK'}}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    return env

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--amendment', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, default=Path('/dev/shm/zerorun-application-revision-20260907-v3'))
    parser.add_argument('--output', type=Path, default=Path('/home/floxy/zerorun-application-revision-20260907-v3'))
    parser.add_argument('--no-cache-wrapper', type=Path, required=True)
    args = parser.parse_args()
    workspace, output = args.workspace.resolve(), args.output.resolve()
    assert platform.system() == 'Linux' and platform.machine() in {'x86_64','amd64'}
    assert os.getuid() != 0 and not workspace.exists() and not output.exists()
    assert workspace.parent == Path('/dev/shm') and output.parent == Path('/home/floxy')
    assert not args.amendment.is_symlink() and args.amendment.is_file()
    workspace.mkdir(); output.mkdir()
    bundle = output / 'record-only'; bundle.mkdir()
    commands = bundle / 'commands'; commands.mkdir()
    provenance = bundle / 'provenance'; provenance.mkdir()
    (provenance / 'driver.py').write_bytes(Path(__file__).read_bytes())
    (provenance / 'APPLICATION_REVISION_V3_AMENDMENT.md').write_bytes(args.amendment.read_bytes())
    (provenance / 'build_image_no_cache_v3.py').write_bytes(args.no_cache_wrapper.read_bytes())
    (provenance / 'OUTCOME_CLASSIFICATION_V3.md').write_bytes((args.amendment.parent / 'OUTCOME_CLASSIFICATION_V3.md').read_bytes())
    save(provenance / 'protocol.json', {
        'schema':'zerorun.application-revision-driver.v3', 'started_utc':utc(),
        'harness_commit':HARNESS, 'engine_commit':ENGINE, 'public_remote':PUBLIC,
        'sequence':['fresh_image_build_no_cache', 'main_original_24_new_image'],
        'main_budget_seconds':1200, 'execution_seconds':120,
        'main_outer_timeout_seconds':1560, 'image_build_outer_timeout_seconds':960,
        'registry_port':19529, 'no_concurrent_benchmarks':True,
        'driver':file_row(provenance/'driver.py',bundle),
        'amendment':file_row(provenance/'APPLICATION_REVISION_V3_AMENDMENT.md',bundle),
        'no_cache_adapter':file_row(provenance/'build_image_no_cache_v3.py',bundle),
        'outcome_clarification':file_row(provenance/'OUTCOME_CLASSIFICATION_V3.md',bundle),
        'docker_build_cache_disabled':True, 'preexisting_base_layers_permitted':True,
        'model_calls':0, 'runtime_source_modified':False,
        'git_credentials_used':False, 'bit_identical_rebuild_required':False})
    rows = []

    def command(label, argv, timeout=120, *, cwd=None, anonymous=False, input_bytes=None, required=True):
        row = {'label':label,'argv':[str(v) for v in argv], 'started_utc':utc(),
               'timeout_seconds':timeout, 'returncode':None, 'timed_out':False, 'error':None}
        save(commands/(label+'.started.json'),row)
        before=time.monotonic()
        out,err=commands/(label+'.stdout.log'),commands/(label+'.stderr.log')
        process=None
        try:
            with out.open('xb') as stdout,err.open('xb') as stderr:
                process=subprocess.Popen(row['argv'],cwd=cwd,env=git_env() if anonymous else None,
                    stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                    stdout=stdout,stderr=stderr,start_new_session=True)
                try:
                    process.communicate(input=input_bytes,timeout=timeout)
                except subprocess.TimeoutExpired:
                    row['timed_out']=True
                    os.killpg(process.pid,signal.SIGTERM)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid,signal.SIGKILL); process.wait(timeout=10)
                row['returncode']=process.returncode
        except Exception as exc:
            row['error']={'type':type(exc).__name__,'message':str(exc)}
        finally:
            row.update(completed_utc=utc(),elapsed_seconds=time.monotonic()-before)
            for kind,path in [('stdout',out),('stderr',err)]:
                if path.exists(): row[kind]=file_row(path,bundle)
            save(commands/(label+'.json'),row); rows.append(row)
            print(json.dumps({'command':label,'returncode':row['returncode'],'timed_out':row['timed_out']},sort_keys=True),flush=True)
        if required and (row['error'] or row['timed_out'] or row['returncode'] != 0):
            raise RuntimeError('command failed; retained record: '+label)
        return row

    git=shutil.which('git'); assert git
    g=[git,'-c','credential.helper=','-c','http.extraHeader=','-c','core.hooksPath=/dev/null','-c','init.templateDir=']
    harness,engine=workspace/'harness',workspace/'engine'
    source_checks={}
    error=None; material=False; exported=[]; finished=False
    try:
        command('environment',[sys.executable,'-I','-B','-c',
            'import json,platform,os,shutil;print(json.dumps({"python":platform.python_version(),"platform":platform.platform(),"uid":os.getuid(),"cpu_count":os.cpu_count(),"ram_meminfo":open("/proc/meminfo").read(),"shm_free":shutil.disk_usage("/dev/shm").free,"guest_free":shutil.disk_usage("/home/floxy").free},sort_keys=True))'])
        command('docker-version',['docker','version'])
        sparse_harness=['/PUBLIC_RELEASE_MANIFEST.json','/research/softwarex/handoff_v1/',
            '/research/softwarex/handoff_image_v2/', '/research/softwarex/evidence/handoff-acquisition-recovery-v1/']
        sparse_engine=['/PUBLIC_RELEASE_MANIFEST.json','/src/zerorun/',
            '/tools/product_generalization_benchmark.py','/ci/generalization-runtime-requirements.txt']
        for label,root,commit,sparse in [('harness',harness,HARNESS,sparse_harness),('engine',engine,ENGINE,sparse_engine)]:
            command(label+'-init',g+['init',str(root)],anonymous=True)
            (root/'.git/info').mkdir(exist_ok=True)
            command(label+'-autocrlf',g+['-C',str(root),'config','core.autocrlf','false'],anonymous=True)
            command(label+'-remote',g+['-C',str(root),'remote','add','origin',PUBLIC],anonymous=True)
            command(label+'-promisor',g+['-C',str(root),'config','remote.origin.promisor','true'],anonymous=True)
            command(label+'-filter',g+['-C',str(root),'config','remote.origin.partialclonefilter','blob:none'],anonymous=True)
            command(label+'-sparse-init',g+['-C',str(root),'sparse-checkout','init','--no-cone'],anonymous=True)
            command(label+'-sparse-set',g+['-C',str(root),'sparse-checkout','set','--no-cone','--stdin'],
                anonymous=True,input_bytes=('\n'.join(sparse)+'\n').encode())
            command(label+'-fetch',g+['-C',str(root),'-c','remote.origin.promisor=true',
                '-c','remote.origin.partialclonefilter=blob:none','fetch','--filter=blob:none','--depth=1','origin',commit],timeout=300,anonymous=True)
            command(label+'-checkout',g+['-C',str(root),'checkout','--detach',commit],timeout=300,anonymous=True)
            head=command(label+'-head-before',g+['-C',str(root),'rev-parse','HEAD'],anonymous=True)
            clean=command(label+'-status-before',g+['-C',str(root),'status','--porcelain'],anonymous=True)
            assert (bundle/head['stdout']['path']).read_text().strip()==commit
            assert not (bundle/clean['stdout']['path']).read_bytes()
            source_checks[label]={'commit':commit,'sparse_paths':sparse,'clean_before':True,
                'manifest_sha256':sha((root/'PUBLIC_RELEASE_MANIFEST.json').read_bytes())}
        assert source_checks['harness']['manifest_sha256']=='229ce2c029555dc8e132a36beca4d24bfae524ded68e6fce1d1801a3f7b2dcb0'
        assert source_checks['engine']['manifest_sha256']=='f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564'
        sys.path.insert(0,str(harness))
        from research.softwarex.handoff_v1 import run as h
        from research.softwarex.handoff_image_v2 import validate as v
        from research.softwarex.handoff_image_v2 import export as export_module
        acquisition=harness/'research/softwarex/evidence/handoff-acquisition-recovery-v1'
        for name,expected,count in [('main',MAIN_SHA,24),('pilot',PILOT_SHA,2)]:
            raw=(acquisition/(name+'.json')).read_bytes(); assert sha(raw)==expected
            cases=h.validate_ledger(h.strict(raw)); assert len(cases)==count
            for case in cases:
                if case.get('disposition')!='UNAVAILABLE_ACQUISITION':
                    h.bound(acquisition,case['metadata']); h.bound(acquisition,case['source_archive'],h.MAX_ARCHIVE_BYTES)
        binding=h.load_engine(engine)[-1]
        assert binding['requirements']['sha256']=='a04f815c62754114a0f0a4b7db15d02c920e7c7d006d813ac9955be00482c02c'
        save(provenance/'source-check.json',{'checkouts':source_checks,'engine_binding':binding,
            'main_ledger_sha256':MAIN_SHA,'pilot_ledger_sha256':PILOT_SHA})
        command('base-preexisting',['docker','image','inspect',BASE],required=False)
        command('registry-preexisting',['docker','image','inspect',REGISTRY],required=False)
        command('acquire-base',['docker','pull','--platform','linux/amd64',BASE],timeout=360)
        command('acquire-registry',['docker','pull','--platform','linux/amd64',REGISTRY],timeout=360)
        fresh_image=output/'handoff-image-build-no-cache-v1'
        command('fresh-image-build-no-cache',[sys.executable,'-B',str(provenance/'build_image_no_cache_v3.py'),
            '--harness',str(harness),'--engine',str(engine),'--output',str(fresh_image),'--registry-image',REGISTRY,
            '--port','19529','--approve-loopback-registry'],timeout=960,cwd=harness)
        image_summary=v.validate_image(fresh_image)
        build_record=json.loads((fresh_image/'commands/build.json').read_text())
        adapter_record=json.loads((fresh_image/'commands/no-cache-adapter.json').read_text())
        assert build_record['argv']==adapter_record['actual_argv'] and build_record['argv'].count('--no-cache')==1
        assert adapter_record['adapter_sha256']==sha((provenance/'build_image_no_cache_v3.py').read_bytes())
        save(provenance/'fresh-image-reconciliation.json',image_summary)
        export_module.export(fresh_image,bundle/'handoff-image-build-no-cache-v1')
        exported.append('handoff-image-build-no-cache-v1')
        main_output=output/'handoff-main-clean-v1'
        command('main-clean',[sys.executable,'-B','-m','research.softwarex.handoff_image_v2.run',
            '--ledger',str(acquisition/'main.json'),'--engine',str(engine),'--image-build',str(fresh_image),
            '--output',str(main_output),'--execution-seconds','120','--budget-seconds','1200','--execute-reviewed-lab'],
            timeout=1560,cwd=harness,required=False)
        if (main_output/'run/completion.json').is_file():
            material=json.loads((main_output/'run/completion.json').read_text())['material_correctness_stop']
        if (main_output/'completion.json').is_file():
            summary=v.validate_saved(main_output,fresh_image,acquisition)
            save(provenance/'main-reconciliation.json',summary)
            export_module.export(main_output,bundle/'handoff-main-clean-v1',bundle/'handoff-image-build-no-cache-v1')
            exported.append('handoff-main-clean-v1')
        else:
            raise RuntimeError('main campaign has no complete outer receipt; all partial records retained')
        if material:
            raise RuntimeError('material correctness stop; no further benchmark launched')
        finished=True
    except Exception as exc:
        error={'type':type(exc).__name__,'message':str(exc)}
    finally:
        for label,root,commit in [('harness',harness,HARNESS),('engine',engine,ENGINE)]:
            if (root/'.git').is_dir():
                head=command(label+'-head-after',g+['-C',str(root),'rev-parse','HEAD'],anonymous=True,required=False)
                clean=command(label+'-status-after',g+['-C',str(root),'status','--porcelain'],anonymous=True,required=False)
                unchanged=head['returncode']==0 and clean['returncode']==0 and (bundle/head['stdout']['path']).read_text().strip()==commit and not (bundle/clean['stdout']['path']).read_bytes()
                source_checks.setdefault(label,{})['clean_exact_after']=unchanged
                if not unchanged: error=error or {'type':'SourceBindingError','message':label+' source changed'}
        # Preserve failed/incomplete records without treating them as validated exports.
        excluded={'context','registry-store','workspace','source','preflight-workspace',
            'dependency-starter','.git','.zerorun','.zerorun-env',
            'private-cache-authentication-NOT-FOR-PUBLICATION','__pycache__'}
        partial=[]
        for name in ['handoff-main-clean-v1','handoff-image-build-no-cache-v1']:
            original=output/name
            if not original.is_dir() or name in exported: continue
            for current,dirs,names in os.walk(original,followlinks=False):
                dirs[:]=sorted(d for d in dirs if d not in excluded and not (Path(current)/d).is_symlink())
                for filename in sorted(names):
                    path=Path(current)/filename
                    if path.suffix not in {'.json','.py','.md','.log'} or path.is_symlink(): continue
                    target=bundle/'unreconciled-attempts'/name/path.relative_to(original)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open('xb') as stream: stream.write(path.read_bytes())
            partial.append(name)
        receipt={'schema':'zerorun.application-revision-completion.v3','completed_utc':utc(),
            'finished_sequence':finished,'passed':finished and error is None and not material,
            'error':error,'material_correctness_stop':material,'checkouts':source_checks,
            'exported_directories':exported,'commands':rows,'automatic_retries':0,
            'unreconciled_attempts_preserved':partial,
            'workspaces_and_private_keys_exported':False,'external_independent_researcher':False,
            'old_results_overwritten':False,'bit_identical_rebuild_claimed':False}
        save(bundle/'completion.json',receipt)
        files=[file_row(p,bundle) for p in sorted(bundle.rglob('*')) if p.is_file()]
        save(bundle/'RECORD_MANIFEST.json',{'files':files,'contains_credentials':False})
        archive=output/'record-only.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    assert not path.is_symlink()
                    tar.add(path,arcname='record-only/'+path.relative_to(bundle).as_posix(),recursive=False)
        transfer={'path':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive.read_bytes()),
            'within_20_mib':archive.stat().st_size<=20*1024*1024,'passed':receipt['passed'],'error':error}
        save(output/'transfer.json',transfer)
        print(json.dumps(transfer,sort_keys=True),flush=True)
    return int(error is not None or material or not finished)

if __name__=='__main__':
    raise SystemExit(main())
