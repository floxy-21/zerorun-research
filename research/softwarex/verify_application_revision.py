"""Read-only reconciliation of independently pinned application-revision bundles."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime
import json
import math
from pathlib import Path
import re

from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_v1 import validate as v1
from research.softwarex.handoff_image_v2 import validate as v2

HARNESS = '0528905a52b74df78aa4e5a09219df34620282dd'
ENGINE = 'ebf2884df12573d63f45813200e0675288d12096'
PUBLIC = 'https://github.com/floxy-21/zerorun-research.git'
MANIFESTS = {'harness':'229ce2c029555dc8e132a36beca4d24bfae524ded68e6fce1d1801a3f7b2dcb0',
             'engine':'f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564'}
LEDGERS = {'main':'4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997',
           'pilot':'2017a0414475b7a0bc19da04cc824c5d49c23b7713e6940865c5e87985756b59'}
BLOCKED = {'context','registry-store','workspace','source','preflight-workspace','dependency-starter',
           '.git','.zerorun','.zerorun-env','private-cache-authentication-NOT-FOR-PUBLICATION','__pycache__'}
TOP_LEVEL = {'commands','provenance','image-existing','handoff-main-repeat-v1',
             'handoff-image-build-fresh-v1','handoff-pilot-fresh-image-v1','unreconciled-attempts',
             'completion.json','RECORD_MANIFEST.json'}

def read(path):
    return h.strict(h.ordinary(path))

def same(actual, expected, label):
    v1.exact(actual, expected, label)

def inventory(directory):
    """Require every published byte, including raw streams, in one exact ledger."""
    directory=Path(directory)
    h.require(directory.is_dir() and not directory.is_symlink(), 'ordinary bundle directory required')
    manifest=read(directory/'RECORD_MANIFEST.json')
    h.require(set(manifest)=={'files','contains_credentials'} and manifest['contains_credentials'] is False,
              'unexpected bundle manifest scope')
    rows=manifest['files']
    h.require(isinstance(rows,list) and rows, 'empty record manifest')
    names=[]
    for row in rows:
        h.bound(directory,row)
        names.append(row['path'])
    h.require(len(names)==len(set(names)), 'duplicate record manifest path')
    actual=[]
    for path in directory.rglob('*'):
        relative=path.relative_to(directory)
        h.require(not path.is_symlink() and not getattr(path.lstat(),'st_file_attributes',0)&0x400,
                  'linked/reparse bundle member refused')
        h.require(relative.parts[0] in TOP_LEVEL and not set(relative.parts)&BLOCKED,
                  'private or unexpected bundle path')
        if path.is_file():
            h.require(path.suffix in {'.json','.py','.md','.log'}, 'unexpected bundle file type')
            if relative.as_posix()!='RECORD_MANIFEST.json': actual.append(relative.as_posix())
    same(sorted(names),sorted(actual),'record manifest is not exhaustive')
    return rows

def commands(directory, completion):
    rows=completion['commands']
    h.require(isinstance(rows,list), 'command list required')
    result={}
    initial={'label','argv','started_utc','timeout_seconds','returncode','timed_out','error'}
    for row in rows:
        label=row.get('label')
        h.require(isinstance(label,str) and re.fullmatch('[a-z0-9-]+',label) and label not in result,
                  'invalid or duplicate command label')
        same(read(directory/'commands'/(label+'.json')),row,'completion command differs from raw command record')
        started=read(directory/'commands'/(label+'.started.json'))
        h.require(set(started)==initial and started['returncode'] is None and started['timed_out'] is False
                  and started['error'] is None, 'command initial state changed')
        for key in initial-{'returncode','timed_out','error'}:
            same(started[key],row[key],'command start/final identity differs')
        h.require(type(row['timeout_seconds']) is int and 0<row['timeout_seconds']<=1560,
                  'command timeout outside protocol')
        h.require(type(row['elapsed_seconds']) in (int,float) and math.isfinite(row['elapsed_seconds'])
                  and row['elapsed_seconds']>=0 and type(row['timed_out']) is bool,
                  'invalid command elapsed time/status')
        h.require(row['returncode'] is None or type(row['returncode']) is int,'invalid command exit type')
        h.require(isinstance(row['argv'],list) and row['argv'] and all(isinstance(a,str) for a in row['argv']),
                  'invalid command argv')
        for stream_name in ('stdout','stderr'):
            h.require(row[stream_name]['path']==f'commands/{label}.{stream_name}.log','stream path crosses command boundary')
            h.bound(directory,row[stream_name])
        result[label]=row
    actual={p.name.removesuffix('.started.json') for p in (directory/'commands').glob('*.started.json')}
    same(sorted(actual),sorted(result),'command records omitted from completion')
    expected_files={label+suffix for label in result for suffix in
                    ('.started.json','.json','.stdout.log','.stderr.log')}
    same(sorted(p.name for p in (directory/'commands').iterdir()),sorted(expected_files),
         'unexpected command record file')
    return result

def stream(directory,row,name='stdout'):
    return h.bound(directory,row[name])

def succeeded(row):
    return row['returncode']==0 and not row['timed_out'] and row['error'] is None

def export_inventory(directory):
    saved=read(directory/'EXPORT_MANIFEST.json')
    h.require(saved['schema']=='zerorun.image-handoff-export.v2' and saved['credentials_included'] is False
              and saved['registry_or_image_archive_included'] is False,'export scope differs')
    names=[]
    for row in saved['files']:
        h.bound(directory,row)
        names.append(row['path'])
    h.require(len(names)==len(set(names)),'duplicate export member')
    actual=[p.relative_to(directory).as_posix() for p in directory.rglob('*')
            if p.is_file() and p.name!='EXPORT_MANIFEST.json']
    same(sorted(names),sorted(actual),'export manifest is not exhaustive')

def wrapper_summary_expected(summary,directory,python_version):
    """Re-execute the producer's old sum rule exactly; never apply a tolerance.

    CPython 3.12 changed float summation. Only the two global chain sums and
    their directly derived fraction need the older left-to-right operation.
    All block values, counts, identities, oracles and other fields stay exact.
    The canonical summary returned by verify is always recomputed by v1.
    """
    match=re.fullmatch(r'3\.(\d+)\.\d+',python_version)
    h.require(match is not None and int(match[1])>=10,'unsupported wrapper Python version')
    if int(match[1])>=12: return summary
    expected=copy.deepcopy(summary)
    totals={'fresh':0.0,'zerorun':0.0}
    completion=read(directory/'run/completion.json')
    for case in completion['cases']:
        if case['disposition']!='COMPLETE': continue
        for index in (0,1):
            block=read(directory/'run/cases'/case['case_id']/('block-'+str(index))/'summary.json')
            for arm in totals: totals[arm]+=block['measurements']['chain_ms'][arm]
    expected['controlled_handoffs']['complete_paired_chain_ms']=totals
    expected['controlled_handoffs']['complete_pair_chain_saved_fraction']=(
        1-totals['zerorun']/totals['fresh'] if totals['fresh'] else None)
    return expected

def checkout_bindings(directory,rows,source):
    for label,commit in [('harness',HARNESS),('engine',ENGINE)]:
        selected=source['checkouts'][label]
        h.require(selected['commit']==commit and selected['manifest_sha256']==MANIFESTS[label]
                  and selected['clean_before'] is True,'wrong public source identity')
        for state in ('before','after'):
            head=rows[label+'-head-'+state]
            status=rows[label+'-status-'+state]
            h.require(succeeded(head) and succeeded(status),'checkout inspection failed')
            h.require(stream(directory,head).decode().strip()==commit and not stream(directory,status),
                      'checkout HEAD or clean status differs')
        remote=rows[label+'-remote']
        fetch=rows[label+'-fetch']
        checkout=rows[label+'-checkout']
        h.require(remote['argv'][-3:]==['add','origin',PUBLIC], 'nonpublic Git source')
        h.require(fetch['argv'][-2:]==['origin',commit] and '--depth=1' in fetch['argv']
                  and '--filter=blob:none' in fetch['argv'], 'Git fetch commit/filter differs')
        h.require(checkout['argv'][-3:]==['checkout','--detach',commit],'checkout command differs')
        for name,row in rows.items():
            if name.startswith(label+'-'):
                h.require('credential.helper=' in row['argv'] and 'http.extraHeader=' in row['argv'],
                          'public Git credential policy absent')
    binding=source['engine_binding']
    h.require(binding['runtime_core_commit']==h.CORE and binding['public_manifest_sha256']==MANIFESTS['engine'],
              'historical engine manifest/core differs')
    h.validate_runtime_rows(binding['runtime_files'])
    h.require(binding['helper']['sha256']==h.HELPER_SHA
              and binding['requirements']['sha256']=='a04f815c62754114a0f0a4b7db15d02c920e7c7d006d813ac9955be00482c02c',
              'engine helper or dependency lock differs')

def verify(root, directory, driver_sha, amendment_sha):
    """Pins come from separately reviewed source, never from the bundle itself."""
    root,directory=Path(root),Path(directory)
    for value in (driver_sha,amendment_sha):
        h.require(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value),'external SHA-256 pin required')
    bound_rows=inventory(directory)
    protocol=read(directory/'provenance/protocol.json')
    completion=read(directory/'completion.json')
    h.require(protocol['schema']=='zerorun.application-revision-driver.v1'
              and completion['schema']=='zerorun.application-revision-completion.v1','unknown wrapper schema')
    h.require(protocol['harness_commit']==HARNESS and protocol['engine_commit']==ENGINE
              and protocol['public_remote']==PUBLIC,'wrapper public checkout identity differs')
    same(protocol['sequence'],['main_original_24_existing_image','fresh_image_build','original_two_case_pilot_new_image'],
         'prospective sequence differs')
    for key,value in {'main_budget_seconds':1200,'pilot_budget_seconds':600,'execution_seconds':120,
                      'main_outer_timeout_seconds':1560,'pilot_outer_timeout_seconds':960,'registry_port':19519}.items():
        h.require(type(protocol[key]) is int and protocol[key]==value,'prospective budget/port differs')
    for name,expected in [('driver',driver_sha),('amendment',amendment_sha)]:
        raw=h.bound(directory,protocol[name])
        h.require(h.sha(raw)==expected,'independently pinned '+name+' source differs')
        h.require(protocol[name]['path']==('provenance/driver.py' if name=='driver' else
                                         'provenance/APPLICATION_REVISION_AMENDMENT.md'),'source record path differs')
    h.require(protocol['no_concurrent_benchmarks'] is True and type(protocol['model_calls']) is int
              and protocol['model_calls']==0 and protocol['runtime_source_modified'] is False
              and protocol['git_credentials_used'] is False and protocol['bit_identical_rebuild_required'] is False,
              'wrapper scope differs')
    h.require(type(completion['automatic_retries']) is int and completion['automatic_retries']==0
              and completion['old_results_overwritten'] is False
              and completion['workspaces_and_private_keys_exported'] is False
              and completion['external_independent_researcher'] is False
              and completion['bit_identical_rebuild_claimed'] is False,'completion scope differs')
    rows=commands(directory,completion)
    base={'schema':'zerorun.application-revision-reconciliation.v1',
          'record_manifest_sha256':h.sha(h.ordinary(directory/'RECORD_MANIFEST.json')),
          'verifier_sha256':h.sha(h.ordinary(Path(__file__))),
          'driver_sha256':driver_sha,'amendment_sha256':amendment_sha,
          'bound_files':len(bound_rows),'commands':len(rows),'source_and_raw_bindings_reconciled':True,
          'external_independent_researcher':False,'bit_identical_rebuild_claimed':False,
          'acceptance_probability_estimated':False}
    if completion['passed'] is not True:
        h.require(isinstance(completion['error'],dict) or completion['material_correctness_stop'] is True
                  or any(not succeeded(row) for row in rows.values()),'failed attempt has no retained failure')
        return {**base,'state':'RECONCILED_FAILED_ATTEMPT','sequence_completed':False,
                'checkout_execution_binding_confirmed':False,
                'fresh_real_workload_reproduction_confirmed':False,'error':completion['error'],
                'failed_commands':[name for name,row in rows.items() if not succeeded(row)],
                'material_correctness_stop':completion['material_correctness_stop'],
                'completed_workload_outcomes_reconciled':False}
    h.require(completion['finished_sequence'] is True and completion['error'] is None
              and completion['material_correctness_stop'] is False,'claimed complete sequence failed')
    h.require(all(succeeded(row) for row in rows.values()),'passed sequence contains failed command')
    source=read(directory/'provenance/source-check.json')
    checkout_bindings(directory,rows,source)
    environment=h.strict(stream(directory,rows['environment']))
    wrapper_python=environment['python']
    for label in ('harness','engine'):
        h.require(completion['checkouts'][label]['clean_exact_after'] is True,'source changed after execution')
    names=['image-existing','handoff-main-repeat-v1','handoff-image-build-fresh-v1','handoff-pilot-fresh-image-v1']
    same(completion['exported_directories'],names,'complete export set/order differs')
    for name in names: export_inventory(directory/name)
    h.require(not completion.get('unreconciled_attempts_preserved',[]),'passed sequence contains unreconciled attempt')
    image_old=v2.validate_image(directory/'image-existing')
    image_new=v2.validate_image(directory/'handoff-image-build-fresh-v1')
    same(source['existing_image'],image_old,'original image binding differs')
    same(read(directory/'provenance/fresh-image-reconciliation.json'),image_new,'saved fresh image summary differs')
    h.require(read(directory/'handoff-image-build-fresh-v1/protocol.json')['port']==19519,'fresh registry port differs')
    acquisition=root/'research/softwarex/evidence/handoff-acquisition-recovery-v1'
    summaries={}
    for phase,folder,image,budget,command_label in [
        ('main','handoff-main-repeat-v1','image-existing',1200,'main-repeat'),
        ('pilot','handoff-pilot-fresh-image-v1','handoff-image-build-fresh-v1',600,'fresh-pilot')]:
        ledger_raw=h.ordinary(acquisition/(phase+'.json'))
        h.require(h.sha(ledger_raw)==LEDGERS[phase] and source[phase+'_ledger_sha256']==LEDGERS[phase],
                  'original ledger identity differs')
        raw_protocol=read(directory/folder/'run/protocol.json')
        same(raw_protocol['selection'],h.strict(ledger_raw),'case selection/order differs')
        h.require(raw_protocol['selection_sha256']==LEDGERS[phase]
                  and raw_protocol['budget_seconds']==budget and raw_protocol['execution_seconds']==120,
                  'actual campaign limits differ')
        argv=rows[command_label]['argv']
        for flag,value in [('--budget-seconds',str(budget)),('--execution-seconds','120')]:
            h.require(argv.count(flag)==1 and argv[argv.index(flag)+1]==value,'invocation limit differs')
        summary=v2.validate_saved(directory/folder,directory/image,acquisition)
        saved='main-reconciliation.json' if phase=='main' else 'fresh-pilot-reconciliation.json'
        same(read(directory/'provenance'/saved),wrapper_summary_expected(summary,directory/folder,wrapper_python),
             'saved campaign reconciliation differs under its exact producer summation rule')
        summaries[phase]=summary
    ordered=list(rows)
    h.require(ordered.index('main-repeat')<ordered.index('fresh-image-build')<ordered.index('fresh-pilot'),
              'recorded benchmark sequence differs')
    for earlier,later in [('main-repeat','fresh-image-build'),('fresh-image-build','fresh-pilot')]:
        h.require(datetime.fromisoformat(rows[earlier]['completed_utc'])<=datetime.fromisoformat(rows[later]['started_utc']),
                  'timed commands overlap or chronological order differs')
    pilot=summaries['pilot']['controlled_handoffs']
    return {**base,'state':'RECONCILED_COMPLETED_SEQUENCE','sequence_completed':True,
            'checkout_execution_binding_confirmed':True,
            'completed_workload_outcomes_reconciled':True,'material_correctness_stop':False,
            'wrapper_python_version':wrapper_python,
            'wrapper_aggregate_check':'Exact producer-version sum rule re-executed from raw blocks; no floating tolerance. Returned aggregates use the canonical validator.',
            'fresh_real_workload_reproduction_confirmed':pilot['complete_cases']==2 and pilot['complete_blocks']==4,
            'image_existing':image_old,'image_fresh':image_new,'main':summaries['main'],'pilot':summaries['pilot'],
            'scope':'Author-side VM sequence; preserved original cases, distinct image bindings and fresh oracles. Completion does not imply every main case was supported.'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--driver-sha',required=True)
    parser.add_argument('--amendment-sha',required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=verify(args.root,args.directory,args.driver_sha,args.amendment_sha)
    raw=json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n'
    if args.output:
        with args.output.open('x',encoding='utf-8') as output_stream: output_stream.write(raw)
    else: print(raw,end='')

if __name__=='__main__':
    main()

