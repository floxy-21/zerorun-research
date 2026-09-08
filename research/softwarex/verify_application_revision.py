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

def inventory(directory, *, allowed_top_level=TOP_LEVEL):
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
        h.require(relative.parts[0] in allowed_top_level and not set(relative.parts)&BLOCKED,
                  'private or unexpected bundle path')
        if path.is_file():
            h.require(path.suffix in {'.json','.py','.md','.log'}, 'unexpected bundle file type')
            if relative.as_posix()!='RECORD_MANIFEST.json': actual.append(relative.as_posix())
    same(sorted(names),sorted(actual),'record manifest is not exhaustive')
    return rows

def commands(directory, completion, *, timeout_exceptions=None):
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
        timeout_limit = (timeout_exceptions or {}).get(label, 1560)
        h.require(type(row['timeout_seconds']) is int and 0<row['timeout_seconds']<=timeout_limit,
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

CLEAN_TOP_LEVEL = {'commands', 'provenance', 'handoff-image-build-no-cache-v1',
                   'handoff-main-clean-v1', 'unreconciled-attempts',
                   'completion.json', 'RECORD_MANIFEST.json'}
CLEAN_ADAPTER_SHA = 'b6588cd74f878152e6a492e3d91ec1c1622fcaf18b3f0e6873a6c4758eb156e5'
CLEAN_CLASSIFICATION_SHA = '1a008750c10200639e875a25f61b19ea77a72f7b698352f41ecd34ca2db28bd0'
FROZEN_IMAGE_BUILDER_SHA = '21e1b2490b6f4d6649032e14011e5c8c6f457b905b366c4f1f239dd10547fc2c'
REGISTRY = 'docker.io/library/registry@sha256:46faa9a1ae6813194b53921a370f2f4f8c5e1aae228a89bceafef5847a6a3278'


def full_attempt_gate(control):
    """A complete execution ledger is distinct from successful validation of every case."""
    names = {'COMPLETE', 'INCOMPLETE_OR_UNSUPPORTED', 'MATERIAL_CORRECTNESS_STOP',
             'NOT_RUN_BUDGET', 'NOT_RUN_CAMPAIGN_FAILURE', 'NOT_RUN_CORRECTNESS_STOP',
             'UNAVAILABLE_ACQUISITION'}
    dispositions = control['dispositions']
    h.require(type(control['selected_cases']) is int and control['selected_cases'] == 24
              and set(dispositions) == names
              and all(type(value) is int and value >= 0 for value in dispositions.values())
              and sum(dispositions.values()) == 24, 'full cohort disposition denominator differs')
    unattempted = {name: dispositions[name] for name in sorted(names)
                  if name.startswith('NOT_RUN') or name == 'UNAVAILABLE_ACQUISITION'}
    attempted = 24 - sum(unattempted.values())
    return {'selected_cases': 24, 'attempted_cases': attempted,
            'complete_cases': dispositions['COMPLETE'],
            'incomplete_or_unsupported_cases': dispositions['INCOMPLETE_OR_UNSUPPORTED'],
            'material_correctness_stop_cases': dispositions['MATERIAL_CORRECTNESS_STOP'],
            'unattempted_dispositions': unattempted,
            'all_selected_cases_attempted': attempted == 24,
            'all_selected_cases_completed': dispositions['COMPLETE'] == 24,
            'scope': 'Attempted includes retained incompatible or failed cases; it never turns those outcomes into successful validation.'}


def no_cache_binding(directory, adapter_sha=CLEAN_ADAPTER_SHA):
    """Check actual Docker invocation; never infer cache bypass from a label."""
    directory = Path(directory)
    record = read(directory/'commands/no-cache-adapter.json')
    h.require(record['schema'] == 'zerorun.docker-no-cache-invocation.v3'
              and record['adapter_file'] == 'provenance/build_image_no_cache_v3.py'
              and record['adapter_sha256'] == adapter_sha
              and record['frozen_builder_sha256'] == FROZEN_IMAGE_BUILDER_SHA
              and record['only_added_argument'] == '--no-cache'
              and record['runtime_source_modified'] is False, 'no-cache adapter identity differs')
    requested, actual = record['requested_argv'], record['actual_argv']
    h.require(isinstance(requested, list) and len(requested) == 9
              and all(isinstance(value, str) for value in requested)
              and requested[1:7] == ['build', '--pull=false', '--network=none', '--build-arg',
                                     'SOURCE_DATE_EPOCH=0', '--tag'], 'original Docker build shape differs')
    same(actual, [*requested[:2], '--no-cache', *requested[2:]], 'adapter changed more than cache flag')
    command = v2.command(directory/'commands', 'build')
    same(command['argv'], actual, 'recorded Docker build did not use the adapter invocation')
    protocol = read(directory/'protocol.json')
    h.require(requested[7].startswith('127.0.0.1:'+str(protocol['port'])+'/zerorun-handoff-v2:')
              and Path(requested[8]).name == 'context', 'no-cache build context/tag differs')
    return {'actual_docker_build_no_cache': True, 'adapter_sha256': adapter_sha,
            'frozen_builder_sha256': FROZEN_IMAGE_BUILDER_SHA,
            'adapter_record_sha256': h.sha(h.ordinary(directory/'commands/no-cache-adapter.json')),
            'build_record_sha256': h.sha(h.ordinary(directory/'commands/build.json')),
            'preexisting_base_layers_permitted': True, 'empty_daemon_claimed': False,
            'uncached_dependency_acquisition_claimed': False}


def verify_clean_v3(root, directory, driver_sha, amendment_sha):
    """Reconcile the separately pinned no-cache image/main sequence, without pooling v2."""
    root, directory = Path(root), Path(directory)
    for value in (driver_sha, amendment_sha):
        h.require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value),
                  'external SHA-256 pin required')
    bound_rows = inventory(directory, allowed_top_level=CLEAN_TOP_LEVEL)
    protocol, completion = read(directory/'provenance/protocol.json'), read(directory/'completion.json')
    h.require(protocol['schema'] == 'zerorun.application-revision-driver.v3'
              and completion['schema'] == 'zerorun.application-revision-completion.v3',
              'unknown clean wrapper schema')
    h.require(protocol['harness_commit'] == HARNESS and protocol['engine_commit'] == ENGINE
              and protocol['public_remote'] == PUBLIC, 'clean wrapper public source differs')
    same(protocol['sequence'], ['fresh_image_build_no_cache', 'main_original_24_new_image'],
         'clean prospective sequence differs')
    for key, value in {'main_budget_seconds': 1200, 'execution_seconds': 120,
                       'main_outer_timeout_seconds': 1560, 'image_build_outer_timeout_seconds': 960,
                       'registry_port': 19529}.items():
        h.require(type(protocol[key]) is int and protocol[key] == value, 'clean prospective limits differ')
    for name, expected, path in (
            ('driver', driver_sha, 'provenance/driver.py'),
            ('amendment', amendment_sha, 'provenance/APPLICATION_REVISION_V3_AMENDMENT.md'),
            ('no_cache_adapter', CLEAN_ADAPTER_SHA, 'provenance/build_image_no_cache_v3.py'),
            ('outcome_clarification', CLEAN_CLASSIFICATION_SHA, 'provenance/OUTCOME_CLASSIFICATION_V3.md')):
        h.require(protocol[name]['path'] == path and h.sha(h.bound(directory, protocol[name])) == expected,
                  'independently pinned clean '+name+' differs')
    h.require(all(protocol[key] is True for key in ('no_concurrent_benchmarks',
                  'docker_build_cache_disabled', 'preexisting_base_layers_permitted'))
              and type(protocol['model_calls']) is int and protocol['model_calls'] == 0
              and all(protocol[key] is False for key in ('runtime_source_modified',
                  'git_credentials_used', 'bit_identical_rebuild_required')), 'clean wrapper scope differs')
    h.require(type(completion['automatic_retries']) is int and completion['automatic_retries'] == 0
              and all(completion[key] is False for key in ('old_results_overwritten',
                  'workspaces_and_private_keys_exported', 'external_independent_researcher',
                  'bit_identical_rebuild_claimed')), 'clean completion scope differs')
    rows = commands(directory, completion)
    base = {'schema': 'zerorun.application-revision-reconciliation.v3',
            'record_manifest_sha256': h.sha(h.ordinary(directory/'RECORD_MANIFEST.json')),
            'verifier_sha256': h.sha(h.ordinary(Path(__file__))),
            'driver_sha256': driver_sha, 'amendment_sha256': amendment_sha,
            'adapter_sha256': CLEAN_ADAPTER_SHA, 'bound_files': len(bound_rows), 'commands': len(rows),
            'source_and_raw_bindings_reconciled': True, 'external_independent_researcher': False,
            'bit_identical_rebuild_claimed': False, 'pooled_with_previous_attempts': False,
            'acceptance_probability_estimated': False, 'uninterrupted_timing_certified': False}
    if completion['passed'] is not True:
        h.require(isinstance(completion['error'], dict) or completion['material_correctness_stop'] is True
                  or any(not succeeded(row) for row in rows.values()), 'failed clean attempt has no failure')
        return {**base, 'state': 'RECONCILED_FAILED_ATTEMPT', 'sequence_completed': False,
                'checkout_execution_binding_confirmed': False,
                'fresh_image_main_reproduction_confirmed': False,
                'completed_workload_outcomes_reconciled': False, 'error': completion['error'],
                'failed_commands': [name for name, row in rows.items() if not succeeded(row)],
                'material_correctness_stop': completion['material_correctness_stop']}
    h.require(completion['finished_sequence'] is True and completion['error'] is None
              and completion['material_correctness_stop'] is False, 'clean completion hides failure')
    informational = {'base-preexisting', 'registry-preexisting'}
    h.require(all(succeeded(row) for name, row in rows.items() if name not in informational),
              'clean sequence contains failed execution command')
    source = read(directory/'provenance/source-check.json')
    checkout_bindings(directory, rows, source)
    wrapper_python = h.strict(stream(directory, rows['environment']))['python']
    for label in ('harness', 'engine'):
        h.require(completion['checkouts'][label]['clean_exact_after'] is True, 'clean source changed after execution')
    names = ['handoff-image-build-no-cache-v1', 'handoff-main-clean-v1']
    same(completion['exported_directories'], names, 'clean complete export set/order differs')
    h.require(not completion.get('unreconciled_attempts_preserved', []), 'clean pass includes unreconciled attempt')
    for name in names:
        export_inventory(directory/name)
    image_directory, main_directory = directory/names[0], directory/names[1]
    image = v2.validate_image(image_directory)
    same(read(directory/'provenance/fresh-image-reconciliation.json'), image, 'saved clean image summary differs')
    h.require(read(image_directory/'protocol.json')['port'] == 19529, 'clean image registry port differs')
    no_cache = no_cache_binding(image_directory)
    for label, reference in [('acquire-base', v2.b.BASE_IMAGE), ('acquire-registry', REGISTRY)]:
        h.require(rows[label]['argv'] == ['docker', 'pull', '--platform', 'linux/amd64', reference],
                  'clean prerequisite acquisition differs')
    acquisition = root/'research/softwarex/evidence/handoff-acquisition-recovery-v1'
    ledger_raw = h.ordinary(acquisition/'main.json')
    h.require(h.sha(ledger_raw) == LEDGERS['main'] and source['main_ledger_sha256'] == LEDGERS['main'],
              'clean main ledger identity differs')
    raw_protocol = read(main_directory/'run/protocol.json')
    same(raw_protocol['selection'], h.strict(ledger_raw), 'clean main case selection/order differs')
    h.require(raw_protocol['selection_sha256'] == LEDGERS['main']
              and type(raw_protocol['budget_seconds']) is int and raw_protocol['budget_seconds'] == 1200
              and type(raw_protocol['execution_seconds']) is int and raw_protocol['execution_seconds'] == 120,
              'actual clean campaign limits differ')
    argv = rows['main-clean']['argv']
    for flag, value in [('--budget-seconds', '1200'), ('--execution-seconds', '120')]:
        h.require(argv.count(flag) == 1 and argv[argv.index(flag)+1] == value, 'clean invocation limit differs')
    build_argv = rows['fresh-image-build-no-cache']['argv']
    h.require(argv.count('--image-build') == build_argv.count('--output') == 1
              and argv[argv.index('--image-build')+1] == build_argv[build_argv.index('--output')+1],
              'clean main invocation uses another image build')
    h.require(rows['main-clean']['timeout_seconds'] == 1560
              and rows['fresh-image-build-no-cache']['timeout_seconds'] == 960,
              'actual clean outer safeguard differs')
    summary = v2.validate_saved(main_directory, image_directory, acquisition)
    h.require(summary['controlled_handoffs']['material_correctness_stop'] is False,
              'clean wrapper hides an actual material correctness stop')
    same(read(directory/'provenance/main-reconciliation.json'),
         wrapper_summary_expected(summary, main_directory, wrapper_python),
         'saved clean main reconciliation differs under exact producer summation rule')
    ordered = list(rows)
    h.require(ordered.index('fresh-image-build-no-cache') < ordered.index('main-clean'),
              'clean main preceded image construction')
    h.require(datetime.fromisoformat(rows['fresh-image-build-no-cache']['completed_utc'])
              <= datetime.fromisoformat(rows['main-clean']['started_utc']), 'clean image/main commands overlap')
    return {**base, 'state': 'RECONCILED_COMPLETED_SEQUENCE', 'sequence_completed': True,
            'checkout_execution_binding_confirmed': True, 'completed_workload_outcomes_reconciled': True,
            'fresh_image_main_reproduction_confirmed': summary['controlled_handoffs']['complete_cases'] > 0,
            'material_correctness_stop': False, 'wrapper_python_version': wrapper_python,
            'image_fresh': image, 'main': summary, 'no_cache_build': no_cache,
            'main_command': rows['main-clean'],
            'preexisting_image_inspections': {name: {'returncode': rows[name]['returncode'],
                'timed_out': rows[name]['timed_out'], 'error': rows[name]['error']} for name in sorted(informational)},
            'scope': 'Author-side public-source main rerun with Docker build-cache bypass. Existing base layers and dependency caches are allowed. Host continuity requires separate observations; no independent-human or universal acceleration claim.'}


FULL_TOP_LEVEL = {'commands', 'provenance', 'image-v3-no-cache', 'handoff-main-full-v1',
                  'unreconciled-attempts', 'completion.json', 'RECORD_MANIFEST.json'}
PRIOR_V3_PROTOCOL_SHA = '843687aaaf5d9434e81fb578ae4e3319380816414be11e11ec8d47de2446f311'
PRIOR_V3_IMAGE_PROTOCOL_SHA = '09725efbc075ee4e3a32322906ef2ca1f76b097fce8f3a795bf5b2ae8313ab62'
PRIOR_V3_IMAGE_COMPLETION_SHA = '2ec9b117826a8fcbae631cbef8853dd6e9388e36ec4e0474cdc0839c6eec0b89'
PRIOR_V3_IMAGE_DIGEST = 'sha256:4684c5606a3cc79f9755bdde2ea364543a2fd2db2019e298ea70b411dae88c09'


def verify_full_v4(root, directory, driver_sha, amendment_sha):
    """Require a fresh full-cohort attempt using the separately retained v3 image."""
    root, directory = Path(root), Path(directory)
    for value in (driver_sha, amendment_sha):
        h.require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'external SHA-256 pin required')
    bound_rows = inventory(directory, allowed_top_level=FULL_TOP_LEVEL)
    protocol, completion = read(directory/'provenance/protocol.json'), read(directory/'completion.json')
    h.require(protocol['schema'] == 'zerorun.application-revision-driver.v4'
              and completion['schema'] == 'zerorun.application-revision-completion.v4', 'unknown full-cohort wrapper schema')
    h.require(protocol['harness_commit'] == HARNESS and protocol['engine_commit'] == ENGINE
              and protocol['public_remote'] == PUBLIC, 'full-cohort public source differs')
    same(protocol['sequence'], ['reconcile_existing_v3_no_cache_image', 'main_all_24_same_image'],
         'full-cohort prospective sequence differs')
    for key, value in {'main_budget_seconds': 7200, 'execution_seconds': 120,
                       'main_outer_timeout_seconds': 7560}.items():
        h.require(type(protocol[key]) is int and protocol[key] == value, 'full-cohort prospective limits differ')
    h.require(protocol['expected_image_digest'] == PRIOR_V3_IMAGE_DIGEST
              and protocol['completion_requires_all_selected_attempted'] is True,
              'full-cohort image or completeness gate changed')
    for name, expected, path in (
            ('driver', driver_sha, 'provenance/driver.py'),
            ('amendment', amendment_sha, 'provenance/APPLICATION_REVISION_V4_AMENDMENT.md'),
            ('prior_no_cache_adapter', CLEAN_ADAPTER_SHA, 'provenance/build_image_no_cache_v3.py'),
            ('prior_v3_protocol', PRIOR_V3_PROTOCOL_SHA, 'provenance/prior-v3-protocol.json')):
        h.require(protocol[name]['path'] == path and h.sha(h.bound(directory, protocol[name])) == expected,
                  'independently pinned full-cohort '+name+' differs')
    h.require(protocol['no_concurrent_benchmarks'] is True and protocol['prior_build_docker_cache_disabled'] is True
              and type(protocol['model_calls']) is int and protocol['model_calls'] == 0
              and all(protocol[key] is False for key in ('docker_build_performed_in_this_attempt',
                  'runtime_source_modified', 'git_credentials_used', 'bit_identical_rebuild_required')),
              'full-cohort wrapper scope differs')
    h.require(type(completion['automatic_retries']) is int and completion['automatic_retries'] == 0
              and all(completion[key] is False for key in ('old_results_overwritten',
                  'workspaces_and_private_keys_exported', 'external_independent_researcher',
                  'bit_identical_rebuild_claimed')), 'full-cohort completion scope differs')
    rows = commands(directory, completion, timeout_exceptions={'main-full': 7560})
    base = {'schema': 'zerorun.application-revision-reconciliation.v4',
            'record_manifest_sha256': h.sha(h.ordinary(directory/'RECORD_MANIFEST.json')),
            'verifier_sha256': h.sha(h.ordinary(Path(__file__))), 'driver_sha256': driver_sha,
            'amendment_sha256': amendment_sha, 'bound_files': len(bound_rows), 'commands': len(rows),
            'source_and_raw_bindings_reconciled': True, 'external_independent_researcher': False,
            'pooled_with_previous_attempts': False, 'bit_identical_rebuild_claimed': False,
            'new_image_built_in_this_attempt': False, 'acceptance_probability_estimated': False,
            'uninterrupted_timing_certified': False}
    if completion['passed'] is not True:
        h.require(isinstance(completion['error'], dict) or completion['material_correctness_stop'] is True
                  or any(not succeeded(row) for row in rows.values()), 'failed full-cohort attempt has no failure')
        return {**base, 'state': 'RECONCILED_FAILED_ATTEMPT', 'sequence_completed': False,
                'checkout_execution_binding_confirmed': False, 'full_cohort_reproduction_confirmed': False,
                'all_selected_cases_attempted': False, 'completed_workload_outcomes_reconciled': False,
                'error': completion['error'], 'reported_attempt_gate': completion.get('attempt_gate'),
                'failed_commands': [name for name, row in rows.items() if not succeeded(row)],
                'material_correctness_stop': completion['material_correctness_stop']}
    h.require(completion['finished_sequence'] is True and completion['error'] is None
              and completion['material_correctness_stop'] is False
              and completion['all_selected_cases_attempted'] is True, 'full-cohort completion hides failure/unattempted cases')
    h.require(all(succeeded(row) for row in rows.values()), 'full-cohort sequence contains failed execution command')
    source = read(directory/'provenance/source-check.json')
    checkout_bindings(directory, rows, source)
    wrapper_python = h.strict(stream(directory, rows['environment']))['python']
    for label in ('harness', 'engine'):
        h.require(completion['checkouts'][label]['clean_exact_after'] is True, 'full-cohort source changed after execution')
    names = ['image-v3-no-cache', 'handoff-main-full-v1']
    same(completion['exported_directories'], names, 'full-cohort export set/order differs')
    h.require(not completion.get('unreconciled_attempts_preserved', []), 'full-cohort pass includes unreconciled attempt')
    for name in names:
        export_inventory(directory/name)
    image_directory, main_directory = directory/names[0], directory/names[1]
    h.require(h.sha(h.ordinary(image_directory/'protocol.json')) == PRIOR_V3_IMAGE_PROTOCOL_SHA
              and h.sha(h.ordinary(image_directory/'completion.json')) == PRIOR_V3_IMAGE_COMPLETION_SHA,
              'full-cohort image differs from the independently pinned prior build')
    image = v2.validate_image(image_directory)
    same(read(directory/'provenance/existing-image-reconciliation.json'), image, 'saved existing image summary differs')
    requested = '127.0.0.1:19529/zerorun-handoff-v2@'+PRIOR_V3_IMAGE_DIGEST
    h.require(image['image']['requested'] == requested, 'full-cohort execution image digest differs')
    prior = read(directory/'provenance/prior-v3-protocol.json')
    same(prior['no_cache_adapter'], protocol['prior_no_cache_adapter'], 'prior image adapter provenance differs')
    no_cache = no_cache_binding(image_directory)
    inspect = rows['image-existing-inspect']
    h.require(inspect['argv'] == ['docker', 'image', 'inspect', requested], 'full-cohort actual image inspection differs')
    same(v2.b.image_metadata(stream(directory, inspect), requested), image['image'], 'inspected full-cohort image differs')
    acquisition = root/'research/softwarex/evidence/handoff-acquisition-recovery-v1'
    ledger_raw = h.ordinary(acquisition/'main.json')
    h.require(h.sha(ledger_raw) == LEDGERS['main'] and source['main_ledger_sha256'] == LEDGERS['main'],
              'full-cohort ledger identity differs')
    raw_protocol = read(main_directory/'run/protocol.json')
    same(raw_protocol['selection'], h.strict(ledger_raw), 'full-cohort case selection/order differs')
    h.require(raw_protocol['selection_sha256'] == LEDGERS['main']
              and type(raw_protocol['budget_seconds']) is int and raw_protocol['budget_seconds'] == 7200
              and type(raw_protocol['execution_seconds']) is int and raw_protocol['execution_seconds'] == 120,
              'actual full-cohort campaign limits differ')
    argv = rows['main-full']['argv']
    for flag, value in [('--budget-seconds', '7200'), ('--execution-seconds', '120'),
                        ('--image-build', protocol['existing_image'])]:
        h.require(argv.count(flag) == 1 and argv[argv.index(flag)+1] == value, 'full-cohort invocation differs')
    h.require(rows['main-full']['timeout_seconds'] == 7560, 'actual full-cohort outer safeguard differs')
    summary = v2.validate_saved(main_directory, image_directory, acquisition)
    h.require(summary['controlled_handoffs']['material_correctness_stop'] is False,
              'full-cohort wrapper hides an actual material correctness stop')
    same(read(directory/'provenance/main-reconciliation.json'),
         wrapper_summary_expected(summary, main_directory, wrapper_python), 'saved full-cohort main reconciliation differs')
    gate = full_attempt_gate(summary['controlled_handoffs'])
    saved_gate = read(directory/'provenance/all-selected-attempted.json')
    expected_gate = {'schema': 'zerorun.all-selected-attempted-gate.v4', 'selected_cases': 24,
        'dispositions': summary['controlled_handoffs']['dispositions'],
        'unattempted': gate['unattempted_dispositions'], 'all_selected_cases_attempted': gate['all_selected_cases_attempted'],
        'compatibility_failure_is_not_test_success': True}
    same(saved_gate, expected_gate, 'saved full-cohort attempt gate differs from actual outcomes')
    same(completion['attempt_gate'], expected_gate, 'completion full-cohort attempt gate differs')
    h.require(gate['all_selected_cases_attempted'] is True, 'full-cohort pass leaves selected cases unattempted')
    h.require(list(rows).index('image-existing-inspect') < list(rows).index('main-full')
              and datetime.fromisoformat(inspect['completed_utc']) <= datetime.fromisoformat(rows['main-full']['started_utc']),
              'existing image was not inspected before the full-cohort command')
    return {**base, 'state': 'RECONCILED_COMPLETED_SEQUENCE', 'sequence_completed': True,
            'checkout_execution_binding_confirmed': True, 'completed_workload_outcomes_reconciled': True,
            'full_cohort_reproduction_confirmed': True, 'all_selected_cases_attempted': True,
            'material_correctness_stop': False, 'wrapper_python_version': wrapper_python,
            'image_existing': image, 'main': summary, 'prior_no_cache_build': no_cache,
            'full_attempt_gate': gate, 'main_command': rows['main-full'],
            'scope': 'All original selected cases attempted with the unchanged eligibility/oracle contract; incompatible or failed cases remain failures. Uses the existing v3 image; its earlier build cost is not a new v4 setup cost. No pooling or independent-human replication claim.'}


def host_continuity(directory, guest_directory, observer_sha, driver_sha, amendment_sha, main_command=None, *, version=3):
    """Reconcile sampled continuity; retain honest gaps instead of hiding outcomes."""
    directory, guest_directory = Path(directory), Path(guest_directory)
    missing = {'state': 'NOT_AVAILABLE', 'sampled_continuity_checks_passed': False,
               'uninterrupted_timing_certified': False,
               'basis': 'No sealed, independently reconciled host observations are available.'}
    if not directory.exists():
        return missing
    if not (directory/'completion.json').is_file():
        return {**missing, 'state': 'INCOMPLETE_RECORD'}
    h.require(isinstance(observer_sha, str) and re.fullmatch('[0-9a-f]{64}', observer_sha),
              'independent host observer pin required')
    completion, protocol = read(directory/'completion.json'), read(directory/'protocol.json')
    h.require(type(version) is int and version in (3, 4), 'unsupported host observation version')
    h.require(completion['schema'] == f'zerorun.host-continuity-completion.v{version}'
              and protocol['schema'] == f'zerorun.host-continuity-protocol.v{version}', 'host observation schema differs')
    h.require(protocol['observer']['path'] == 'observer.py'
              and h.sha(h.bound(directory, protocol['observer'])) == observer_sha,
              'independently pinned host observer differs')
    for key, value in {'sample_interval_seconds': 10, 'maximum_sample_start_gap_seconds': 45,
                       'elapsed_bracket_slack_seconds': 2}.items():
        h.require(type(protocol[key]) is int and protocol[key] == value, 'prospective host threshold differs')
    h.require(protocol['expected_vm_state'] == 'running'
              and protocol['expected_delta_uuid'] == 'a32b0595-4e14-4fd2-89af-d6ec6094b9a1'
              and protocol['endpoint'] == '127.0.0.1:2222'
              and protocol['expected_host_key'] == 'SHA256:5rUVqDOVpFiVuEmQuKFaavxhvu1BWFt7PKtGgs9YNfY'
              and all(protocol[key] is True for key in ('stable_guest_boot_id_required',
                  'guest_command_span_must_be_covered', 'monitoring_is_part_of_recorded_environment'))
              and all(protocol[key] is False for key in ('sample_errors_qualify_as_uninterrupted',
                  'source_edits', 'other_benchmarks_permitted')), 'host observation scope differs')
    expected_inputs = {'revision_application_vm_v3.py': driver_sha,
                       'build_image_no_cache_v3.py': CLEAN_ADAPTER_SHA,
                       'APPLICATION_REVISION_V3_AMENDMENT.md': amendment_sha,
                       'OUTCOME_CLASSIFICATION_V3.md': CLEAN_CLASSIFICATION_SHA}
    if version == 4:
        expected_inputs = {'revision_application_vm_v4.py': driver_sha,
                           'APPLICATION_REVISION_V4_AMENDMENT.md': amendment_sha}
    inputs = protocol['input_files']
    h.require(len(inputs) == len(expected_inputs) and {row['path'] for row in inputs} == set(expected_inputs),
              'host input source inventory differs')
    for row in inputs:
        h.require(type(row['bytes']) is int and row['bytes'] > 0
                  and row['sha256'] == expected_inputs[row['path']], 'host invocation source pin differs')
        guest_name = 'driver.py' if row['path'] == f'revision_application_vm_v{version}.py' else row['path']
        guest_path = guest_directory/'provenance'/guest_name
        if guest_path.is_file():
            raw_input = h.ordinary(guest_path)
            h.require(len(raw_input) == row['bytes'] and h.sha(raw_input) == row['sha256'],
                      'host input differs from actual guest invocation source')
    names = []
    for row in completion['files']:
        h.bound(directory, row)
        names.append(row['path'])
    h.require(len(names) == len(set(names)), 'duplicate host record path')
    actual = []
    for path in directory.iterdir():
        h.require(path.is_file() and not path.is_symlink()
                  and not getattr(path.lstat(), 'st_file_attributes', 0)&0x400,
                  'unexpected or linked host record')
        if path.name != 'completion.json':
            actual.append(path.name)
    same(sorted(names), sorted(actual), 'host record inventory is not exhaustive')
    h.require(set(actual) <= {'observer.py', 'protocol.json', 'continuity.log', 'launch.json',
                              'driver.stdout.log', 'driver.stderr.log', 'host-memory-observation.json'},
              'unexpected host record file')
    h.require(completion['uninterrupted_claim_requires_independent_reconciliation'] is True
              and completion['vm_stop_or_savestate_requested'] is False,
              'host completion changes continuity/preservation scope')
    if completion['error'] is not None or completion['guest_record_manifest'] is None:
        return {**missing, 'state': 'RECONCILED_FAILED_HOST_OBSERVATION',
                'error': completion['error'], 'driver_exit_code': completion['driver_exit_code'],
                'completion_sha256': h.sha(h.ordinary(directory/'completion.json'))}
    manifest = completion['guest_record_manifest']
    h.require(manifest['path'] == 'record-only/RECORD_MANIFEST.json', 'host guest-manifest path differs')
    raw_manifest = h.ordinary(guest_directory/'RECORD_MANIFEST.json')
    h.require(manifest['bytes'] == len(raw_manifest) and manifest['sha256'] == h.sha(raw_manifest),
              'host observations refer to another guest bundle')
    h.require(completion['archive_downloaded_and_verified'] is True and completion['host_key_verified'] is True,
              'host transfer verification absent')
    launch = read(directory/'launch.json')
    prefix = f'/home/floxy/zerorun-v{version}-inputs-20260907/'
    expected_launch = ['python3', '-B', prefix+f'revision_application_vm_v{version}.py', '--amendment',
                       prefix+f'APPLICATION_REVISION_V{version}_AMENDMENT.md']
    if version == 3:
        expected_launch.extend(['--no-cache-wrapper', prefix+'build_image_no_cache_v3.py'])
    else:
        expected_launch.extend(['--existing-image',
            '/home/floxy/zerorun-application-revision-20260907-v3/handoff-image-build-no-cache-v1'])
    same(launch['argv'], expected_launch, 'host launch arguments differ')
    h.require(launch['host_key_verified'] is True and launch['credentials_logged'] is False,
              'host launch transport scope differs')
    transfer = completion['guest_transfer']
    output_lines = h.ordinary(directory/'driver.stdout.log').splitlines()
    h.require(output_lines, 'host driver output absent')
    same(h.strict(output_lines[-1]), transfer, 'host transfer differs from actual driver output')
    samples = [h.strict(line) for line in h.ordinary(directory/'continuity.log').splitlines()]
    h.require(type(completion['sample_count']) is int and completion['sample_count'] == len(samples)
              and all(type(row['sample_index']) is int for row in samples)
              and [row['sample_index'] for row in samples] == list(range(len(samples))),
              'host samples omitted, duplicated or reordered')
    same(completion['sample_errors'], sum(row['error'] is not None for row in samples),
         'host summary hides sample errors')
    reasons = []
    if len(samples) < 2:
        reasons.append('Fewer than two host samples.')
    if completion['sample_errors']:
        reasons.append('One or more host/guest samples failed.')
    if completion['driver_exit_code'] != 0:
        reasons.append('Guest sequence did not return success.')
    if main_command is None:
        reasons.append('No reconciled main command is available for coverage.')
    good = []
    slack = protocol['elapsed_bracket_slack_seconds']
    def finite(value):
        h.require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                  'invalid observation clock/headroom value')
        return value
    def instant(value):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        h.require(result.tzinfo is not None, 'observation timestamp lacks timezone')
        return result
    for row in samples:
        start, end = finite(row['host_monotonic_start']), finite(row['host_monotonic_end'])
        h.require(end >= start, 'host sample has negative monotonic duration')
        wall = (instant(row['host_utc_end']) - instant(row['host_utc_start'])).total_seconds()
        if abs(wall - (end-start)) > slack:
            reasons.append('Host wall clock changed within a sample.')
        if row['error'] is not None:
            h.require(isinstance(row['error'], dict), 'invalid retained sample error')
            continue
        if row['vm_state'] != 'running' or row['delta_uuid'] != protocol['expected_delta_uuid']:
            reasons.append('VM was not running on the expected disk at a sample.')
        if not isinstance(row.get('vm_state_change_time'), str) or not row['vm_state_change_time']:
            reasons.append('A VM state-change marker was unavailable.')
        finite(row['host_c_free_bytes']); finite(row['host_d_free_bytes'])
        guest = row['guest']
        finite(guest['monotonic_seconds']); finite(guest['uptime_seconds']); instant(guest['utc'])
        h.require(isinstance(guest['boot_id'], str) and re.fullmatch(
            '[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', guest['boot_id']), 'invalid guest boot ID')
        for key in ('image_started', 'main_started', 'main_finished'):
            h.require(type(guest[key]) is bool, 'invalid guest phase observation')
        if guest['main_started'] and main_command is not None:
            same(guest['main_started_utc'], main_command['started_utc'], 'host observed another main command')
        good.append(row)
    for earlier, later in zip(samples, samples[1:]):
        gap = later['host_monotonic_start'] - earlier['host_monotonic_start']
        h.require(gap >= 0 and later['host_monotonic_start'] >= earlier['host_monotonic_end'],
                  'host samples overlap or monotonic order differs')
        if gap > protocol['maximum_sample_start_gap_seconds']:
            reasons.append('Host observation gap exceeded the prospective limit.')
        host_wall = (instant(later['host_utc_start']) - instant(earlier['host_utc_start'])).total_seconds()
        if abs(host_wall-gap) > slack:
            reasons.append('Host wall-clock and monotonic elapsed time diverged.')
    if len(samples) > 2:
        span = samples[-1]['host_monotonic_start'] - samples[0]['host_monotonic_start']
        wall_span = (instant(samples[-1]['host_utc_start'])-instant(samples[0]['host_utc_start'])).total_seconds()
        if abs(wall_span-span) > slack:
            reasons.append('Host wall-clock drift accumulated across the observation span.')
    guest_clock_pairs = list(zip(good, good[1:]))
    if len(good) > 2:
        # Small losses must not accumulate unnoticed across individually allowed brackets.
        guest_clock_pairs.append((good[0], good[-1]))
    for earlier, later in guest_clock_pairs:
        old, new = earlier['guest'], later['guest']
        if old['boot_id'] != new['boot_id'] or earlier.get('vm_state_change_time') != later.get('vm_state_change_time'):
            reasons.append('Guest reboot or VM state transition was observed.')
        elapsed = new['monotonic_seconds'] - old['monotonic_seconds']
        lower = later['host_monotonic_start'] - earlier['host_monotonic_end'] - slack
        upper = later['host_monotonic_end'] - earlier['host_monotonic_start'] + slack
        if not lower <= elapsed <= upper:
            reasons.append('Guest monotonic elapsed time fell outside host sampling brackets.')
        guest_wall = (instant(new['utc']) - instant(old['utc'])).total_seconds()
        if abs(guest_wall-elapsed) > slack or abs(new['uptime_seconds']-old['uptime_seconds']-elapsed) > slack:
            reasons.append('Guest wall clock or uptime diverged from guest monotonic elapsed time.')
    covered = False
    if main_command is not None and good:
        before = [row for row in good if instant(row['guest']['utc']) <= instant(main_command['started_utc'])
                  and row['guest']['main_started'] is False]
        after = [row for row in good if instant(row['guest']['utc']) >= instant(main_command['completed_utc'])
                 and row['guest']['main_finished'] is True]
        covered = bool(before and after)
        if not covered:
            reasons.append('Successful observations do not bracket the entire main command.')
        command_wall = (instant(main_command['completed_utc'])-instant(main_command['started_utc'])).total_seconds()
        if abs(command_wall-main_command['elapsed_seconds']) > slack:
            reasons.append('Main command wall-clock and monotonic duration diverged.')
    memory = None
    if (directory/'host-memory-observation.json').is_file():
        observed = read(directory/'host-memory-observation.json')
        h.require(observed['schema'] == 'zerorun-host-memory-observation-v1'
                  and observed['origin'] == 'direct Win32_OperatingSystem observation',
                  'supplemental memory observation scope differs')
        for key in ('free_physical_kib', 'free_virtual_kib', 'total_virtual_kib'):
            h.require(type(observed[key]) is int and observed[key] >= 0,
                      'invalid supplemental memory amount')
        h.require(observed['free_virtual_kib'] <= observed['total_virtual_kib'],
                  'supplemental memory amounts disagree')
        first, last = instant(observed['observed_started_utc']), instant(observed['observed_completed_utc'])
        h.require(first <= last, 'supplemental memory observation time is reversed')
        memory = {'record': observed, 'record_sha256': h.sha(h.ordinary(directory/'host-memory-observation.json')),
                  'continuous_memory_monitoring': False, 'used_to_qualify_continuity': False,
                  'phase': 'separate host-clock observation; not a pre-main certificate'}
    passed = not reasons
    return {'state': 'RECONCILED_HOST_OBSERVATIONS', 'sample_count': len(samples),
            'sample_errors': completion['sample_errors'], 'main_command_span_covered': covered,
            'sampled_continuity_checks_passed': passed, 'uninterrupted_timing_certified': passed,
            'qualification_reasons': sorted(set(reasons)), 'observer_sha256': observer_sha,
            'supplemental_memory_observation': memory,
            'completion_sha256': h.sha(h.ordinary(directory/'completion.json')),
            'guest_record_manifest_sha256': h.sha(raw_manifest),
            'maximum_sample_start_gap_seconds': max((b['host_monotonic_start']-a['host_monotonic_start']
                for a, b in zip(samples, samples[1:])), default=None),
            'basis': 'No interruption observed within prospectively bounded host/guest samples.' if passed else
                     'Retained observations do not qualify the main timing as uninterrupted.',
            'scope': 'Sampled host/guest continuity supports this recorded run only; it cannot rule out every pause shorter than the observation resolution. Monitoring is part of the measured environment.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--driver-sha',required=True)
    parser.add_argument('--amendment-sha',required=True)
    version = parser.add_mutually_exclusive_group()
    version.add_argument('--clean-v3',action='store_true')
    version.add_argument('--full-v4',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    function = verify_full_v4 if args.full_v4 else verify_clean_v3 if args.clean_v3 else verify
    result=function(args.root,args.directory,args.driver_sha,args.amendment_sha)
    raw=json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n'
    if args.output:
        with args.output.open('x',encoding='utf-8') as output_stream: output_stream.write(raw)
    else: print(raw,end='')

if __name__=='__main__':
    main()
