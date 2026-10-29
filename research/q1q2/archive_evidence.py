"""Copy exact selected Actions artifacts into the research branch for retention.

Run with standard GitHub Actions GH_TOKEN, actions:read permission. No scientific
data is transformed. A conflicting previously archived byte aborts the archive.
"""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
REPO = 'floxy-21/zerorun-research'


def write_exact(path, content):
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('existing evidence differs: ' + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def main():
    for entry in json.loads((HERE / 'ARTIFACTS.json').read_text()):
        name = entry['directory']
        assert PurePosixPath(name).name == name and name not in ('.', '..')
        folder = HERE / 'evidence' / name
        api = f'repos/{REPO}/actions/artifacts/{entry["artifact_id"]}'
        metadata = json.loads(subprocess.check_output(['gh', 'api', api]))
        assert metadata['digest'] == 'sha256:' + entry['zip_sha256']
        assert metadata['workflow_run']['id'] == entry['run_id']
        assert metadata['workflow_run']['head_sha'] == entry['execution_commit']
        data = subprocess.check_output(['gh', 'api', api + '/zip'])
        assert hashlib.sha256(data).hexdigest() == entry['zip_sha256']
        assert len(data) == metadata['size_in_bytes']
        z = zipfile.ZipFile(io.BytesIO(data))
        assert len(z.namelist()) == len(set(z.namelist()))
        for info in z.infolist():
            path = PurePosixPath(info.filename)
            assert not path.is_absolute() and '..' not in path.parts
            assert not any(x in {'authority', 'receipt-authority', 'zerorun-authority', 'oracle-authority'} for x in path.parts)
            assert path.name != 'receipt.key'
            if not info.is_dir():
                write_exact(folder / str(path), z.read(info))
        provenance = {**entry, 'zip_bytes': len(data), 'files': len(z.namelist()),
                      'archive_method': 'verified GitHub Actions artifact ZIP; exact extracted file bytes'}
        write_exact(folder / 'ARCHIVE_PROVENANCE.json', (json.dumps(provenance, indent=2) + '\n').encode())
        hashes = {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(folder.rglob('*')) if p.is_file() and p.name != 'ARCHIVE_FILES.json'}
        write_exact(folder / 'ARCHIVE_FILES.json', (json.dumps(hashes, indent=2) + '\n').encode())
        print(json.dumps({'directory': name, 'artifact_id': entry['artifact_id'], 'verified_files': len(hashes)}), flush=True)


if __name__ == '__main__':
    main()
