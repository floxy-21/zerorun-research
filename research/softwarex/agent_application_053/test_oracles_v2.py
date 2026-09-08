import io
import json
from pathlib import Path
import tarfile

from research.softwarex.agent_application_053 import oracles_v2 as audit


def test_reconstructed_final_gets_only_inert_git_marker_and_preserves_model_bytes(tmp_path, monkeypatch):
    prepared = tmp_path/'prepared'; case_dir = prepared/'case-00'; case_dir.mkdir(parents=True)
    work = tmp_path/'work'; work.mkdir()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w:gz') as archive:
        member = tarfile.TarInfo('agent-final/code.py'); member.size = len(b'actual model output\n')
        archive.addfile(member, io.BytesIO(b'actual model output\n'))
    (case_dir/'base.tar.gz').write_bytes(b'original base archive')
    (case_dir/'final.tar.gz').write_bytes(raw.getvalue()); (case_dir/'tests.patch').write_bytes(b'')
    base_inventory = [{'path': 'code.py', 'kind': 'file', 'bytes': len(b'original source\n'), 'sha256': audit.h.sha(b'original source\n')}]
    final_inventory = audit.pv.source_archive_inventory(raw.getvalue())
    case = {'case_id': 'artificial-case', 'base_commit': '0'*40,
            'source_archive': audit.record(case_dir/'base.tar.gz', 'base.tar.gz')}
    prep = {'source_archive': case['source_archive'], 'test_patch': audit.record(case_dir/'tests.patch', 'tests.patch'),
            'test_paths': [], 'before': base_inventory}
    session = {'final_source': audit.record(case_dir/'final.tar.gz', 'final.tar.gz'), 'after': final_inventory}
    for path, obj in [(prepared/'freeze.json', {'cases': [case]}), (case_dir/'preparation.json', prep), (case_dir/'session.json', session)]:
        path.write_text(json.dumps(obj))
    def extract(data, destination, commit):
        assert data == b'original base archive'; destination.mkdir(); (destination/'code.py').write_bytes(b'original source\n')
        return {'artificial': True}
    commands = []
    def git(root, argv, raw=None):
        commands.append((root, argv, raw))
        if argv[0] == 'init':
            (root/'.git').mkdir(); (root/'.git'/'config').write_bytes(b'artificial metadata')
        return {'argv': ['git', *argv], 'returncode': 0, 'stdout': '', 'stderr': ''}
    monkeypatch.setattr(audit.h, 'extract_source', extract); monkeypatch.setattr(audit.h, 'git', git)
    base, final, result = audit.reconstruct(prepared, 0, work)
    assert (final/'.git').is_dir()
    assert (final/'code.py').read_bytes() == b'actual model output\n'
    assert (base/'code.py').read_bytes() == b'original source\n'
    assert commands[-1] == (final, ['init', '--template=', '--initial-branch=main', '.'], None)
    assert result['reference_source_patch_applied'] is False
    assert result['final_inventory'] == final_inventory
    assert all(command[2] is None or command[2] == b'' for command in commands)


def test_v2_requires_real_regression_and_success_even_after_preparation_correction():
    producer = {'producer_completed': True, 'boundary_pass': True, 'source_patch_present': True}
    def oracle(exit_code, call):
        return {'result': {'verdict': {'exit_code': exit_code, 'nodes': {'test': {
            'setup': 'passed', 'call': call, 'teardown': 'passed', 'wasxfail': False}}}}}
    assert audit.classify(producer, oracle(1, 'failed'), oracle(0, 'passed'))['completed_verified_fix'] is True
    assert audit.classify(producer, oracle(1, 'failed'), oracle(1, 'failed'))['completed_verified_fix'] is False
    assert audit.classify(producer, oracle(1, 'failed'), None)['completed_verified_fix'] is False
