"""Versioned final-Git-marker correction for six actual producer oracles.

Run only the explicitly reviewed laboratory. Verification is offline and does
not execute Git, containers, application code, models, or ZeroRun consumers.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
from unittest.mock import patch

from research.softwarex.agent_handoff_v1 import run as producer
from research.softwarex.agent_handoff_v1 import validate as pv
from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_v1 import validate as hv
from research.softwarex.handoff_image_v2 import build_image as ib

HERE = Path(__file__).resolve().parent
IMAGE = '127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465'
IMAGE_CONFIG = 'b3a8632a1f903b463526c0f59377f8a447b2f90bba920871d1da816fb198a198'
FREEZE_SHA = '7416d9eef01057ea2a1694b27ea7f552d64b1ebf96f7a720ddae756b3bd8a5f9'
LEDGER_SHA = '4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997'
CASES = ['eliben__pycparser-236', 'joke2k__django-environ-174', 'tobymao__sqlglot-3182',
         'terryyin__lizard-241', 'eyeseast__python-frontmatter-56', 'joshtemple__lkml-87']
FILES = ('preparation.json', 'started.json', 'session.json', 'prompt.txt', 'metadata-hf.json',
         'provided-tests.patch', 'base-source.tar.gz', 'events.log', 'stderr.log',
         'proposed-tracked.patch', 'final-source.tar.gz')
MAX_BYTES = 192 * 1024 * 1024


def read(path):
    return h.strict(h.ordinary(path, MAX_BYTES))


def record(path, relative):
    raw = h.ordinary(path, MAX_BYTES)
    return {'path': relative, 'bytes': len(raw), 'sha256': h.sha(raw)}


def input_inventory(prepared):
    rows = [record(prepared/'freeze.json', 'freeze.json')]
    for index in range(6):
        for name in FILES:
            path = prepared/f'case-{index:02d}'/name
            h.require(path.is_file(), 'required producer sidecar missing: '+str(index)+'/'+name)
            rows.append(record(path, f'case-{index:02d}/'+name))
    return rows


def producer_summaries(prepared):
    frozen = read(prepared/'freeze.json')
    h.require(h.sha(h.ordinary(prepared/'freeze.json')) == FREEZE_SHA, 'different actual producer freeze')
    h.require(frozen['phase'] == 'main' and frozen['ledger_binding']['sha256'] == LEDGER_SHA
              and [case['case_id'] for case in frozen['cases']] == CASES
              and producer.c.selected(frozen['ledger']) == frozen['cases'], 'original six-case selection differs')
    summaries = [pv.validate_receipt(prepared/f'case-{i:02d}'/'session.json', directory=prepared) for i in range(6)]
    h.require(all(row['model_invocation_attempted'] is True and row['boundary_pass'] is True for row in summaries),
              'actual producer boundary not established')
    return frozen, summaries


def classify(producer_summary, baseline, final):
    """A fix needs a non-xfail baseline failure repaired over the same node set."""
    base = baseline['result']['verdict'] if baseline else None
    new = final['result']['verdict'] if final else None
    failed = [node for node, value in base['nodes'].items()
              if value['call'] == 'failed' and value['wasxfail'] is False] if base and base['exit_code'] == 1 else []
    final_pass = bool(new and new['exit_code'] == 0 and new['nodes']
        and any(value['call'] == 'passed' for value in new['nodes'].values())
        and all(value[phase] != 'failed' for value in new['nodes'].values() for phase in ('setup', 'call', 'teardown')))
    same = bool(base and new and set(base['nodes']) == set(new['nodes']))
    repaired = bool(final_pass and failed and same and all(
        new['nodes'][node]['call'] == 'passed' and new['nodes'][node]['wasxfail'] is False for node in failed))
    fixed = bool(repaired and producer_summary.get('producer_completed') is True
                 and producer_summary.get('boundary_pass') is True and producer_summary.get('source_patch_present') is True)
    return {'baseline_available': base is not None, 'final_available': new is not None,
            'baseline_exit_code': base['exit_code'] if base else None,
            'final_exit_code': new['exit_code'] if new else None,
            'baseline_nonxfail_failed_calls': failed, 'same_collected_node_set': same,
            'final_fresh_pass': final_pass, 'fresh_regression_repaired': repaired,
            'completed_verified_fix': fixed,
            'classification': 'VERIFIED_FIX' if fixed else 'NOT_VERIFIED_FIXED'}


def reconstruct(prepared, index, directory):
    case_dir = prepared/f'case-{index:02d}'
    case = read(prepared/'freeze.json')['cases'][index]
    prep, session = read(case_dir/'preparation.json'), read(case_dir/'session.json')
    base_raw = h.bound(case_dir, prep['source_archive'], MAX_BYTES)
    h.require(h.sha(base_raw) == case['source_archive']['sha256'] and len(base_raw) == case['source_archive']['bytes'], 'base source differs')
    base, final = directory/'baseline', directory/'final'
    extraction = h.extract_source(base_raw, base, case['base_commit'])
    test_patch = h.bound(case_dir, prep['test_patch'])
    h.require(h.patch_paths(test_patch.decode()) == prep['test_paths'], 'provided test paths differ')
    operations = [h.git(base, ['init', '--template=', '--initial-branch=main', '.']),
                  h.git(base, ['apply', '--check', '--whitespace=nowarn', '-'], test_patch),
                  h.git(base, ['apply', '--whitespace=nowarn', '-'], test_patch)]
    normalized = lambda rows: sorted(rows, key=lambda row: row['path'])
    h.require(normalized(producer.inventory(base)) == normalized(prep['before']), 'baseline reconstruction differs from producer input')
    final_raw = h.bound(case_dir, session['final_source'], MAX_BYTES)
    archive_inventory = pv.source_archive_inventory(final_raw)
    h.require(archive_inventory == normalized(session['after']), 'actual final archive inventory differs')
    final.mkdir()
    with tarfile.open(fileobj=io.BytesIO(final_raw), mode='r:gz') as archive:
        for member in archive.getmembers():
            name = member.name[len('agent-final/'):].rstrip('/')
            path = final.joinpath(*name.split('/'))
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with path.open('xb') as stream:
                    stream.write(archive.extractfile(member).read())
                path.chmod(0o644)
    final_git = h.git(final, ['init', '--template=', '--initial-branch=main', '.'])
    h.require(normalized(producer.inventory(final)) == archive_inventory, 'final reconstruction differs from actual captured output')
    return base, final, {'case_id': case['case_id'], 'base_archive': prep['source_archive'],
        'final_archive': session['final_source'], 'base_inventory': prep['before'], 'final_inventory': session['after'],
        'provided_test_patch_sha256': h.sha(test_patch), 'extraction': extraction, 'inert_git_operations': operations,
        'reference_source_patch_applied': False, 'actual_final_snapshot_used': True, 'final_git_initialization': final_git}


def optional_oracle(directory, state):
    path = directory/(state+'-oracle.json')
    if not path.is_file():
        return None
    row = read(path); start = read(directory/(state+'-oracle.started.json'))
    h.require(row['operation'] == start['operation'] == state+'-oracle', 'oracle operation identity differs')
    if row['error'] is not None:
        h.require(row['result'] is None and isinstance(row['error'], dict), 'failed oracle contains successful result')
        return None
    return hv.oracle(directory, state+'-oracle', state+'-capture')


def log_plain_calls(bench, output):
    """Record full Docker command diagnostics without changing its arguments."""
    original = bench._plain_run
    counter = 0
    output.mkdir()
    def invoke(root, argv, *, timeout_seconds):
        nonlocal counter
        label = f'{counter:03d}'; counter += 1
        row = {'argv': list(argv), 'cwd': str(root), 'timeout_seconds': timeout_seconds,
               'started_utc': h.utc(), 'returncode': None, 'error': None}
        h.save(output/(label+'.started.json'), row)
        start = time.monotonic(); stdout, stderr = '', ''
        try:
            result = original(root, argv, timeout_seconds=timeout_seconds)
            row['returncode'] = result.returncode
            stdout, stderr = result.stdout, result.stderr
            return result
        except Exception as error:
            stdout, stderr = getattr(error, 'stdout', '') or '', getattr(error, 'stderr', '') or ''
            row['error'] = {'type': type(error).__name__, 'message': str(error)}
            raise
        finally:
            for name, raw in [('stdout', stdout), ('stderr', stderr)]:
                raw = raw.encode() if isinstance(raw, str) else raw
                path = output/(label+'.'+name+'.log')
                with path.open('xb') as stream: stream.write(raw)
                row[name] = record(path, path.name)
            row.update(completed_utc=h.utc(), outer_ms=(time.monotonic()-start)*1000)
            h.save(output/(label+'.json'), row)
    return invoke


def sources():
    paths = [Path(__file__), Path(h.__file__), Path(hv.__file__), Path(producer.__file__), Path(pv.__file__), Path(producer.c.__file__)]
    return [record(path, str(path)) for path in paths]


def run(prepared, engine, output, amendment):
    h.require(os.name == 'posix', 'Linux reviewed laboratory required')
    prepared, engine, amendment = (Path(value).resolve(strict=True) for value in (prepared, engine, amendment))
    output = Path(output).resolve(strict=False)
    h.require(not output.exists() and output.parent.is_dir() and not output.is_relative_to(prepared)
              and not output.is_relative_to(engine), 'new external oracle output required')
    frozen, summaries = producer_summaries(prepared)
    before = input_inventory(prepared); source_before = sources()
    output.mkdir(); records = output/'record-only'; records.mkdir(); work = output/'workspaces'; work.mkdir()
    provenance = records/'provenance'; provenance.mkdir()
    shutil.copyfile(__file__, provenance/'oracles.py')
    shutil.copyfile(amendment, provenance/'ORACLE_PROTOCOL.md')
    copied = records/'prepared'; copied.mkdir()
    for row in before:
        target = copied/row['path']; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream: stream.write(h.bound(prepared, row, MAX_BYTES))
    rows, error, components, image = [], None, None, None
    protocol = {'schema': 'zerorun.actual-agent-oracles-protocol.053.v2', 'started_utc': h.utc(),
        'freeze_sha256': FREEZE_SHA, 'ledger_sha256': LEDGER_SHA, 'cases': frozen['cases'], 'selected_cases': 6,
        'driver': record(provenance/'oracles.py', 'provenance/oracles.py'),
        'amendment': record(provenance/'ORACLE_PROTOCOL.md', 'provenance/ORACLE_PROTOCOL.md'),
        'producer_inputs': before, 'sources': source_before, 'runtime_image': IMAGE, 'execution_seconds': 120,
        'new_model_calls': 0, 'reference_source_patch_applied': False, 'cache_seed_created': False,
        'consumer_evaluated': False, 'new_image_built': False, 'automatic_retries': 0,
        'runtime_role': 'independent ordinary pytest oracle; does not certify the current 0.5.3 interface'}
    h.save(records/'protocol.json', protocol)
    try:
        components = h.load_engine(engine); bench = components[0]
        h.save(provenance/'engine-binding.json', components[-1])
        image = ib.image_metadata(ib.command(provenance, 'image-before', ['docker', 'image', 'inspect', IMAGE]), IMAGE)
        h.require(image['config_sha256'] == IMAGE_CONFIG, 'oracle runtime image configuration differs')
        h.save(provenance/'image-identity.json', image)
        version_raw = ib.command(provenance, 'runtime-versions', ['docker', 'run', '--rm', '--pull=never',
            '--network=none', '--read-only', IMAGE, '/usr/local/bin/python', '-B', '-c',
            "import sys,pytest,json;print(json.dumps({'python':sys.version,'pytest':pytest.__version__}))"], 60)
        versions = h.strict(version_raw)
        h.require(versions['python'].startswith('3.10.21 ') and versions['pytest'] == '8.4.2', 'oracle runtime versions differ')
        with ExitStack() as stack:
            stack.enter_context(patch.object(h, 'IMAGE', IMAGE))
            stack.enter_context(patch.object(bench, '_PYTEST_EXECUTION_TIMEOUT_SECONDS', 120))
            for index, case in enumerate(frozen['cases']):
                directory = records/'cases'/case['case_id']; directory.mkdir(parents=True)
                case_work = work/f'case-{index:02d}'; case_work.mkdir()
                row = {'case_id': case['case_id'], 'case_index': index, 'producer': summaries[index],
                       'states': {}, 'error': None, 'classification': None}
                try:
                    start = time.monotonic()
                    base, final, reconstruction = reconstruct(copied, index, case_work)
                    reconstruction['outer_ms'] = (time.monotonic()-start)*1000
                    h.save(directory/'reconstruction.json', reconstruction)
                    for state, source in [('baseline', base), ('final', final)]:
                        state_before = h.identity(source)
                        h.save(directory/(state+'-source-before.json'), state_before)
                        state_error = None
                        with patch.object(bench, '_plain_run', log_plain_calls(bench, directory/(state+'-docker-commands'))):
                            try:
                                h.operation(directory, state+'-oracle', lambda: h.fresh_oracle(bench, source, case['targets'], directory/(state+'-capture')))
                            except Exception as caught:
                                state_error = {'type': type(caught).__name__, 'message': str(caught)}
                        after = h.identity(source); h.save(directory/(state+'-source-after.json'), after)
                        h.require(state_before == after, 'source changed during independent fresh oracle')
                        row['states'][state] = {'attempted': True, 'error': state_error, 'source_unchanged': True,
                                               'source_sha256': state_before['sha256'], 'workspace': str(source)}
                except Exception as caught:
                    row['error'] = {'type': type(caught).__name__, 'message': str(caught)}
                row['classification'] = classify(summaries[index], optional_oracle(directory, 'baseline'), optional_oracle(directory, 'final'))
                h.save(directory/'completion.json', row); rows.append(row)
        after_image = ib.image_metadata(ib.command(provenance, 'image-after', ['docker', 'image', 'inspect', IMAGE]), IMAGE)
        h.require(after_image == image, 'oracle runtime image changed')
        runtime_after = [record(engine/row['path'], row['path']) for row in components[-1]['runtime_files']]
        h.validate_runtime_rows(runtime_after)
        h.save(provenance/'runtime-after.json', runtime_after)
    except Exception as caught:
        error = {'type': type(caught).__name__, 'message': str(caught)}
    finally:
        for index in range(len(rows), 6):
            case = frozen['cases'][index]; directory = records/'cases'/case['case_id']; directory.mkdir(parents=True, exist_ok=True)
            row = {'case_id': case['case_id'], 'case_index': index, 'producer': summaries[index], 'states': {},
                   'error': {'type': 'CampaignFailure', 'message': 'Prior campaign failure; no replacement attempt'},
                   'classification': classify(summaries[index], None, None)}
            h.save(directory/'completion.json', row); rows.append(row)
        inputs_after = input_inventory(prepared); source_after = sources()
        if inputs_after != before or source_before != source_after:
            error = error or {'type': 'SourceBindingError', 'message': 'Producer inputs or verifier helpers changed'}
        completion = {'schema': 'zerorun.actual-agent-oracles-completion.053.v2', 'completed_utc': h.utc(),
            'protocol_sha256': h.sha(h.ordinary(records/'protocol.json')), 'cases': rows, 'selected_cases': 6,
            'attempted_states': sum(len(row['states']) for row in rows),
            'completed_verified_fixes': sum(row['classification']['completed_verified_fix'] for row in rows),
            'final_fresh_passes': sum(row['classification']['final_fresh_pass'] for row in rows),
            'error': error, 'inputs_after': inputs_after, 'sources_after': source_after,
            'producer_inputs_unchanged': inputs_after == before, 'new_model_calls': 0, 'consumer_evaluated': False,
            'passed': error is None and all(len(row['states']) == 2 and row['error'] is None and all(state['error'] is None for state in row['states'].values()) for row in rows)}
        h.save(records/'completion.json', completion)
        ledger = [record(path, path.relative_to(records).as_posix()) for path in sorted(records.rglob('*'), key=lambda p: p.as_posix()) if path.is_file()]
        h.save(records/'RECORD_MANIFEST.json', {'schema': 'zerorun.actual-agent-oracle-export.053.v2', 'files': ledger,
            'workspaces_included': False, 'client_auth_homes_included': False})
        archive = output/'record-only.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for path in sorted(records.rglob('*'), key=lambda p: p.as_posix()):
                if path.is_file(): tar.add(path, arcname='record-only/'+path.relative_to(records).as_posix(), recursive=False)
        print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
            'sha256': h.sha(archive.read_bytes()), 'completed_verified_fixes': completion['completed_verified_fixes'],
            'final_fresh_passes': completion['final_fresh_passes'], 'attempted_states': completion['attempted_states'], 'error': error}), flush=True)
    return completion


def verify(directory):
    directory = Path(directory)
    manifest = read(directory/'RECORD_MANIFEST.json')
    h.require(manifest['schema'] == 'zerorun.actual-agent-oracle-export.053.v2'
              and manifest['workspaces_included'] is False and manifest['client_auth_homes_included'] is False, 'oracle export scope differs')
    names = []
    for row in manifest['files']:
        h.bound(directory, row, MAX_BYTES); names.append(row['path'])
    actual = []
    for path in directory.rglob('*'):
        h.require(not path.is_symlink() and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400, 'linked oracle evidence refused')
        if path.is_file() and path.name != 'RECORD_MANIFEST.json': actual.append(path.relative_to(directory).as_posix())
    h.require(len(names) == len(set(names)) and sorted(actual) == sorted(names), 'oracle record inventory is not exhaustive')
    protocol, completion = read(directory/'protocol.json'), read(directory/'completion.json')
    h.require(protocol['schema'] == 'zerorun.actual-agent-oracles-protocol.053.v2'
              and completion['schema'] == 'zerorun.actual-agent-oracles-completion.053.v2', 'oracle schema differs')
    h.require(completion['protocol_sha256'] == h.sha(h.ordinary(directory/'protocol.json'))
              and h.bound(directory, protocol['driver']) == h.ordinary(Path(__file__)), 'oracle execution source binding differs')
    h.bound(directory, protocol['amendment'])
    frozen, summaries = producer_summaries(directory/'prepared')
    hv.exact(protocol['cases'], frozen['cases'], 'oracle cases differ')
    hv.exact(input_inventory(directory/'prepared'), protocol['producer_inputs'], 'copied producer evidence differs')
    hv.exact(completion['inputs_after'], protocol['producer_inputs'], 'producer inputs changed during oracles')
    hv.exact(completion['sources_after'], protocol['sources'], 'oracle helper source changed')
    h.require(protocol['runtime_image'] == IMAGE and protocol['execution_seconds'] == 120
              and protocol['freeze_sha256'] == FREEZE_SHA and protocol['ledger_sha256'] == LEDGER_SHA
              and protocol['selected_cases'] == completion['selected_cases'] == 6
              and all(protocol[key] is False for key in ('reference_source_patch_applied', 'cache_seed_created', 'consumer_evaluated', 'new_image_built'))
              and protocol['automatic_retries'] == protocol['new_model_calls'] == completion['new_model_calls'] == 0,
              'oracle scope changed')
    if completion['error'] is None:
        binding = read(directory/'provenance/engine-binding.json')
        h.validate_runtime_rows(binding['runtime_files'])
        hv.exact(binding['runtime_files'], read(directory/'provenance/runtime-after.json'), 'historical oracle engine bytes changed')
        for phase in ('before', 'after'):
            command = read(directory/f'provenance/image-{phase}.json')
            start = read(directory/f'provenance/image-{phase}.started.json')
            h.require(command['argv'] == start['argv'] == ['docker', 'image', 'inspect', IMAGE]
                      and command['returncode'] == 0 and command['error'] is None
                      and command['stdout_truncated'] is False and command['stderr_truncated'] is False,
                      'oracle image inspection failed or changed')
            image = ib.image_metadata(command['stdout'].encode(), IMAGE)
            h.require(image['config_sha256'] == IMAGE_CONFIG, 'oracle image config differs')
            hv.exact(image, read(directory/'provenance/image-identity.json'), 'oracle image before/after differs')
        versions = read(directory/'provenance/runtime-versions.json')
        h.require(versions['returncode'] == 0 and versions['error'] is None, 'runtime version inspection failed')
        actual_versions = h.strict(versions['stdout'].encode())
        h.require(actual_versions['python'].startswith('3.10.21 ') and actual_versions['pytest'] == '8.4.2', 'fresh oracle runtime versions differ')
    rows = []
    for index, case in enumerate(frozen['cases']):
        root = directory/'cases'/case['case_id']; row = read(root/'completion.json')
        h.require(row['case_index'] == index and row['case_id'] == case['case_id'], 'case order changed')
        hv.exact(row['producer'], summaries[index], 'producer outcome summary differs')
        prep, session = (read(directory/'prepared'/f'case-{index:02d}'/name) for name in ('preparation.json', 'session.json'))
        if (root/'reconstruction.json').is_file():
            reconstruction = read(root/'reconstruction.json')
            h.require(reconstruction['case_id'] == case['case_id'] and reconstruction['reference_source_patch_applied'] is False
                      and reconstruction['actual_final_snapshot_used'] is True, 'source reconstruction claim differs')
            hv.exact(reconstruction['base_archive'], prep['source_archive'], 'baseline archive binding differs')
            hv.exact(reconstruction['final_archive'], session['final_source'], 'final archive binding differs')
            h.require(reconstruction['provided_test_patch_sha256'] == prep['test_patch']['sha256'], 'supplied test patch binding differs')
            for key, expected in [('base_inventory', prep['before']), ('final_inventory', session['after'])]:
                hv.exact(reconstruction[key], expected, 'reconstructed source differs from producer capture')
        baseline, final = optional_oracle(root, 'baseline'), optional_oracle(root, 'final')
        for state, oracle in [('baseline', baseline), ('final', final)]:
            if state in row['states']:
                before, after = read(root/(state+'-source-before.json')), read(root/(state+'-source-after.json'))
                hv.exact(before, after, 'fresh oracle source changed')
                expected = prep['before'] if state == 'baseline' else session['after']
                expected = sorted((item for item in expected if item['path'].split('/')[0] not in h.STATE), key=lambda item: item['path'])
                hv.exact(before['rows'], expected, 'executed source differs from immutable baseline/final inventory')
                h.require(before['sha256'] == h.sha(h.encoded(expected)), 'executed source digest differs')
                h.require(row['states'][state]['attempted'] is True and row['states'][state]['source_unchanged'] is True
                          and row['states'][state]['source_sha256'] == before['sha256'], 'state execution/source claim differs')
                logs = root/(state+'-docker-commands')
                commands = []
                for path in sorted(logs.glob('*.json')):
                    if path.name.endswith('.started.json'): continue
                    command = read(path); start = read(path.with_name(path.stem+'.started.json'))
                    hv.exact({key: command[key] for key in start if key not in ('returncode', 'error')},
                             {key: value for key, value in start.items() if key not in ('returncode', 'error')}, 'Docker start/final binding differs')
                    for stream in ('stdout', 'stderr'): h.bound(logs, command[stream], MAX_BYTES)
                    commands.append(command)
                creates = [command for command in commands if len(command['argv']) > 1 and command['argv'][1] == 'create']
                h.require(len(creates) <= 1 and (oracle is None or len(creates) == 1), 'independent oracle needs exactly one actual Docker create')
                if creates:
                    argv = creates[0]['argv']
                    h.require(IMAGE in argv and argv[argv.index(IMAGE)+1:] == ['/usr/local/bin/python', '-m', 'pytest', '-p', 'no:cacheprovider', '-p', 'benchmark_shadow_plugin', *case['targets']], 'actual oracle image or full target differs')
                    h.require('--read-only' in argv and argv[argv.index('--network')+1] == 'none'
                              and argv[argv.index('--pull')+1] == 'never'
                              and ('type=bind,src='+row['states'][state]['workspace']+',dst=/workspace,readonly') in argv,
                              'oracle isolation or source mount changed')
                if oracle is not None:
                    h.require(row['states'][state]['error'] is None, 'successful oracle also reports state error')
        hv.exact(row['classification'], classify(summaries[index], baseline, final), 'fix classification differs from raw fresh outcomes')
        rows.append(row)
    hv.exact(rows, completion['cases'], 'oracle case aggregation differs')
    h.require(completion['completed_verified_fixes'] == sum(row['classification']['completed_verified_fix'] for row in rows)
              and completion['final_fresh_passes'] == sum(row['classification']['final_fresh_pass'] for row in rows)
              and completion['attempted_states'] == sum(len(row['states']) for row in rows), 'oracle result denominator differs')
    return {'schema': 'zerorun.actual-agent-oracle-reconciliation.053.v2', 'reconciled': True,
        'selected_cases': 6, 'attempted_states': completion['attempted_states'],
        'completed_verified_fixes': completion['completed_verified_fixes'], 'final_fresh_passes': completion['final_fresh_passes'],
        'cases': rows, 'error': completion['error'], 'consumer_evaluated': False, 'new_model_calls': 0,
        'runtime_image': IMAGE, 'scope': 'Independent fresh ordinary pytest on actual model snapshots; not a ZeroRun consumer or population success-rate study.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    execute = sub.add_parser('run'); execute.add_argument('--prepared', type=Path, required=True)
    execute.add_argument('--engine', type=Path, required=True); execute.add_argument('--output', type=Path, required=True)
    execute.add_argument('--amendment', type=Path, required=True); execute.add_argument('--execute-reviewed-lab', action='store_true', required=True)
    check = sub.add_parser('verify'); check.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.mode == 'verify': print(json.dumps(verify(args.directory), sort_keys=True))
    else: run(args.prepared, args.engine, args.output, args.amendment)


if __name__ == '__main__':
    main()
