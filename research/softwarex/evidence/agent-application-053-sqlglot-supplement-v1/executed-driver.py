"""One production-only assisted supplement; original model outcomes remain fixed."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile
from unittest.mock import patch
from research.softwarex.handoff_v1 import run as h, validate as hv
from research.softwarex.handoff_image_v2 import build_image as ib

IMAGE = '127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465'
CONFIG = 'b3a8632a1f903b463526c0f59377f8a447b2f90bba920871d1da816fb198a198'
HELPER_SHA = '1dc95421b9c0f1fe92dc65e3e92748f2a921638f2048ed28112c9b0fe5b88a08'
PATCH_SHA = 'ad40dbe819b58b8568956b8c129fbcf5061fef77d911ea5c53337e9168268ebb'
AMENDMENT_SHA = '8571bc8d15d4aa3f6debfa2fe7cbf4af0ff6d4794a8d01214b7e26cdecae9244'
FINAL_SHA = 'fa6f8ab5df1af6329c8bba5b93e9357ba177996ff6e1019ba608cb5025eda8e7'
CASE = 'tobymao__sqlglot-3182'
TARGETS = ['tests/dialects/test_duckdb.py','tests/dialects/test_snowflake.py','tests/test_expressions.py']
CHANGED = {'sqlglot/dialects/duckdb.py':'570f0d85ee14c95e166dabc3b4bc3898f8e584aea752d900715af3bf98bde75d',
 'sqlglot/dialects/snowflake.py':'ef1fc81eb80e6d4ffed7532bc80e6027f56a33bafc465d327f4dd93ee11d279b'}
LIMIT=192*1024*1024

def record(path, relative):
    raw=h.ordinary(path,LIMIT)
    return {'path':relative,'bytes':len(raw),'sha256':h.sha(raw)}

def load_helper(path):
    h.require(h.sha(h.ordinary(path))==HELPER_SHA,'frozen oracle helper changed')
    spec=importlib.util.spec_from_file_location('sqlglot_supplement_oracle_helper',path)
    module=importlib.util.module_from_spec(spec)
    exec(compile(h.ordinary(path), str(path), 'exec'), module.__dict__)
    return module

def exact_change(before,after):
    left={row['path']:row for row in before};right={row['path']:row for row in after}
    h.require(set(left)==set(right),'supplement added or removed source paths')
    changed={name for name in left if left[name]!=right[name]}
    h.require(changed==set(CHANGED),'supplement modified files outside the two production paths')
    for name,sha in CHANGED.items():
        h.require(right[name].get('sha256')==sha,'supplement source differs from frozen repair')

def check(directory):
    directory=Path(directory)
    manifest=hv.read(directory/'RECORD_MANIFEST.json');names=[]
    for row in manifest['files']:h.bound(directory,row,LIMIT);names.append(row['path'])
    actual=[]
    for path in directory.rglob('*'):
        h.require(not path.is_symlink() and not getattr(path.lstat(),'st_file_attributes',0)&0x400,'linked supplement record')
        if path.is_file() and path.name!='RECORD_MANIFEST.json':actual.append(path.relative_to(directory).as_posix())
    h.require(len(names)==len(set(names)) and sorted(names)==sorted(actual),'supplement manifest not exhaustive')
    p,c=hv.read(directory/'protocol.json'),hv.read(directory/'completion.json')
    h.require(p['schema']=='zerorun.sqlglot-assisted-supplement.v1' and c['protocol_sha256']==h.sha(h.ordinary(directory/'protocol.json')),'supplement binding differs')
    for row in p['inputs']:h.bound(directory,row,LIMIT)
    h.require(h.bound(directory,p['driver'])==h.ordinary(Path(__file__)),'supplement driver differs')
    h.require(p['targets']==TARGETS and p['case_id']==CASE and p['image']==IMAGE and p['new_model_calls']==0
              and p['changes_original_model_success_count'] is False and p['execution_seconds']==120,'supplement scope differs')
    h.require(h.sha(h.ordinary(directory/'production-followup.patch'))==PATCH_SHA
              and h.sha(h.ordinary(directory/'SUPPLEMENT_PROTOCOL.md'))==AMENDMENT_SHA,'supplement patch/amendment changed')
    for row in p['harness_sources']:h.bound(h.HERE,row)
    h.require(p['image_helper_sha256']==h.sha(h.ordinary(Path(ib.__file__))), 'image-helper source changed')
    o=load_helper(directory/'oracles_v2.py')
    session=hv.read(directory/'prepared/case-02/session.json')
    raw=h.bound(directory/'prepared/case-02',session['final_source'],LIMIT)
    h.require(h.sha(raw)==FINAL_SHA,'original model final archive differs')
    original_inventory=o.pv.source_archive_inventory(raw)
    h.require(original_inventory==sorted(session['after'],key=lambda row:row['path']),'captured original source inventory differs')
    if c['error'] is not None:
        return {'reconciled':True,'supplement_passed':False,'error':c['error'],'original_primary_verified_fixes':5,'original_selected_cases':6}
    original=hv.read(directory/'original-source-before.json');repaired=hv.read(directory/'repaired-source-before.json')
    expected=sorted((row for row in original_inventory if row['path'].split('/')[0] not in h.STATE),key=lambda row:row['path'])
    hv.exact(original['rows'],expected,'executed original source differs from captured model snapshot')
    exact_change(original['rows'],repaired['rows'])
    for name,sha in CHANGED.items():
        h.require(h.sha(h.ordinary(directory/'repaired-production'/name))==sha,'captured supplemental production file changed')
    oracles={}
    for state in ('original','repaired'):
        before,after=hv.read(directory/(state+'-source-before.json')),hv.read(directory/(state+'-source-after.json'))
        hv.exact(before,after,'supplement source drift during oracle')
        h.require(before['sha256']==h.sha(h.encoded(before['rows'])),'supplement source inventory hash differs')
        oracle=hv.oracle(directory,state+'-oracle',state+'-capture');oracles[state]=oracle['result']['verdict']
        commands=[];logs=directory/(state+'-docker-commands')
        for path in sorted(logs.glob('*.json')):
            if path.name.endswith('.started.json'):continue
            row=hv.read(path);start=hv.read(path.with_name(path.stem+'.started.json'))
            hv.exact({k:row[k] for k in start if k not in ('error','returncode')},{k:v for k,v in start.items() if k not in ('error','returncode')},'Docker command start/final differs')
            for stream in ('stdout','stderr'):h.bound(logs,row[stream],LIMIT)
            commands.append(row)
        creates=[row for row in commands if row['argv'][1:2]==['create']]
        h.require(len(creates)==1,'fresh supplement oracle needs one actual container create')
        argv=creates[0]['argv']
        h.require(IMAGE in argv and argv[argv.index(IMAGE)+1:]==['/usr/local/bin/python','-m','pytest','-p','no:cacheprovider','-p','benchmark_shadow_plugin',*TARGETS]
                  and '--read-only' in argv and argv[argv.index('--network')+1]=='none' and argv[argv.index('--pull')+1]=='never'
                  and ('type=bind,src='+c['workspaces'][state]+',dst=/workspace,readonly') in argv,'supplement oracle target/image/isolation/source differs')
    for phase in ('before','after'):
        command=hv.read(directory/f'provenance/image-{phase}.json')
        h.require(command['argv']==['docker','image','inspect',IMAGE] and command['returncode']==0 and command['error'] is None,'supplement image inspection differs')
        h.require(ib.image_metadata(command['stdout'].encode(),IMAGE)['config_sha256']==CONFIG,'supplement image config differs')
    binding=hv.read(directory/'provenance/engine-binding.json');h.validate_runtime_rows(binding['runtime_files'])
    hv.exact(binding['runtime_files'],hv.read(directory/'provenance/runtime-after.json'),'oracle engine changed')
    same=set(oracles['original']['nodes'])==set(oracles['repaired']['nodes']) and len(oracles['repaired']['nodes'])==99
    passed=same and oracles['repaired']['exit_code']==0 and all(value['call']=='passed' and not value['wasxfail'] for value in oracles['repaired']['nodes'].values())
    return {'schema':'zerorun.sqlglot-assisted-supplement-reconciliation.v1','reconciled':True,'supplement_passed':passed,
        'same_original_99_node_ids':same,'original_exit_code':oracles['original']['exit_code'],'repaired_exit_code':oracles['repaired']['exit_code'],
        'original_failed_nodes':[name for name,value in oracles['original']['nodes'].items() if value['call']=='failed'],
        'repaired_passes':sum(value['call']=='passed' for value in oracles['repaired']['nodes'].values()),
        'original_primary_verified_fixes':5,'original_selected_cases':6,'new_model_calls':0,
        'scope':'Separate assisted production repair of the original model snapshot; no relabeling of primary six-case model outcome, consumer result or performance claim.'}

def run(args):
    h.require(os.name=='posix' and os.getuid()!=0,'reviewed non-root Linux laboratory required')
    o=load_helper(args.oracle_helper)
    frozen,_=o.producer_summaries(args.prepared);case=frozen['cases'][2]
    h.require(case['case_id']==CASE and case['targets']==TARGETS,'original selected case/targets differ')
    h.require(not args.output.exists() and args.output.parent.is_dir(),'new external output required')
    for path,sha in [(args.patch,PATCH_SHA),(args.amendment,AMENDMENT_SHA)]:h.require(h.sha(h.ordinary(path))==sha,'frozen supplement input differs')
    args.output.mkdir();records=args.output/'record-only';records.mkdir();work=args.output/'workspaces';work.mkdir()
    for path,name in [(Path(__file__),'driver.py'),(args.oracle_helper,'oracles_v2.py'),(args.patch,'production-followup.patch'),(args.amendment,'SUPPLEMENT_PROTOCOL.md')]:shutil.copyfile(path,records/name)
    copied=records/'prepared';(copied/'case-02').mkdir(parents=True)
    shutil.copyfile(args.prepared/'freeze.json',copied/'freeze.json')
    for name in o.FILES:shutil.copyfile(args.prepared/'case-02'/name,copied/'case-02'/name)
    inputs=[record(path,path.relative_to(records).as_posix()) for path in sorted(records.rglob('*')) if path.is_file()]
    protocol={'schema':'zerorun.sqlglot-assisted-supplement.v1','case_id':CASE,'targets':TARGETS,'image':IMAGE,'started_utc':h.utc(),
        'execution_seconds':120,'new_model_calls':0,'changes_original_model_success_count':False,'inputs':inputs,'driver':record(records/'driver.py','driver.py'),
        'harness_sources':h.code_inventory(),'image_helper_sha256':h.sha(h.ordinary(Path(ib.__file__)))}
    h.save(records/'protocol.json',protocol);error=None;spaces={}
    try:
        components=h.load_engine(args.engine);bench=components[0]
        provenance=records/'provenance';provenance.mkdir();h.save(provenance/'engine-binding.json',components[-1])
        image=ib.image_metadata(ib.command(provenance,'image-before',['docker','image','inspect',IMAGE]),IMAGE)
        h.require(image['config_sha256']==CONFIG,'oracle image differs')
        _,original,reconstruction=o.reconstruct(copied,2,work)
        h.save(records/'reconstruction.json',reconstruction)
        h.require(h.sha(h.ordinary(copied/'case-02/final-source.tar.gz',LIMIT))==FINAL_SHA,'original agent archive differs')
        repaired=work/'repaired';shutil.copytree(original,repaired)
        patch_raw=h.ordinary(records/'production-followup.patch')
        operations=[h.git(repaired,['apply','--check','--whitespace=nowarn','-'],patch_raw),h.git(repaired,['apply','--whitespace=nowarn','-'],patch_raw)]
        h.save(records/'supplement-application.json',{'operations':operations,'patch_sha256':PATCH_SHA})
        exact_change(h.identity(original)['rows'],h.identity(repaired)['rows'])
        for name in CHANGED:
            dest=records/'repaired-production'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(repaired/name,dest)
        with ExitStack() as stack:
            stack.enter_context(patch.object(h,'IMAGE',IMAGE));stack.enter_context(patch.object(bench,'_PYTEST_EXECUTION_TIMEOUT_SECONDS',120))
            for state,source in [('original',original),('repaired',repaired)]:
                spaces[state]=str(source);before=h.identity(source);h.save(records/(state+'-source-before.json'),before)
                with patch.object(bench,'_plain_run',o.log_plain_calls(bench,records/(state+'-docker-commands'))):
                    h.operation(records,state+'-oracle',lambda:h.fresh_oracle(bench,source,TARGETS,records/(state+'-capture')))
                after=h.identity(source);h.save(records/(state+'-source-after.json'),after);h.require(before==after,'oracle source changed')
        after=ib.image_metadata(ib.command(provenance,'image-after',['docker','image','inspect',IMAGE]),IMAGE);h.require(after==image,'image changed')
        runtime=[record(args.engine/row['path'],row['path']) for row in components[-1]['runtime_files']]
        h.validate_runtime_rows(runtime);h.save(provenance/'runtime-after.json',runtime)
    except Exception as caught:error={'type':type(caught).__name__,'message':str(caught)}
    finally:
        if h.code_inventory()!=protocol['harness_sources'] or h.sha(h.ordinary(Path(ib.__file__)))!=protocol['image_helper_sha256']:
            error=error or {'type':'SourceBindingError','message':'Oracle harness changed during supplemental run'}
        h.save(records/'completion.json',{'protocol_sha256':h.sha(h.ordinary(records/'protocol.json')),'completed_utc':h.utc(),'error':error,'workspaces':spaces})
        rows=[record(path,path.relative_to(records).as_posix()) for path in sorted(records.rglob('*')) if path.is_file()]
        h.save(records/'RECORD_MANIFEST.json',{'files':rows,'workspaces_included':False})
        archive=args.output/'record-only.tar.gz'
        with tarfile.open(archive,'x:gz') as tf:
            for path in sorted(records.rglob('*')):
                if path.is_file():tf.add(path,arcname='record-only/'+path.relative_to(records).as_posix(),recursive=False)
        h.save(args.output/'transfer.json',record(archive,'record-only.tar.gz'))
    result=check(records);print(json.dumps(result,sort_keys=True),flush=True)
    return 0 if result['supplement_passed'] else 2

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',type=Path)
    for name in ('prepared','engine','oracle-helper','patch','amendment','output'):parser.add_argument('--'+name,type=Path)
    args=parser.parse_args()
    if args.check:print(json.dumps(check(args.check),sort_keys=True));return 0
    h.require(all(getattr(args,name.replace('-','_')) for name in ('prepared','engine','oracle-helper','patch','amendment','output')),'all explicit reviewed inputs required')
    return run(args)
if __name__=='__main__':raise SystemExit(main())
