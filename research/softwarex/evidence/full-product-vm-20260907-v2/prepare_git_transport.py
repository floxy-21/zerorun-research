"""Private credential-free Git object transport, with exact archive binding."""
from pathlib import Path
import hashlib
import json
import subprocess
import tarfile

root = Path('/home/floxy/zerorun-five-hour-20260907')
archive = root / 'git-transport-0e59813.tar'
expected = '7c407fb210c561acd3d32bf74d1f0ece32949c36caed1fe3740c055cc59d3bd3'
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
assert actual == expected
mirror = root / 'private-git-transport-0e59813'
assert not mirror.exists()
mirror.mkdir(mode=0o700)
with tarfile.open(archive) as bundle:
    for member in bundle.getmembers():
        relative = Path(member.name)
        assert not relative.is_absolute() and '..' not in relative.parts
        assert relative.parts[0] == '.git' and (member.isfile() or member.isdir())
    bundle.extractall(mirror)
head = subprocess.run(['git', '-C', str(mirror), 'rev-parse', 'HEAD'],
                      check=True, capture_output=True, text=True).stdout.strip()
assert head == '0e59813c21cb023a9f490bda160420c95988b322'
record = {'archive': archive.name, 'archive_bytes': archive.stat().st_size,
          'archive_sha256': actual, 'commit': head,
          'method': 'Depth-one no-checkout clone of local main with no local object shortcuts; only .git transported by private SFTP',
          'original_repository_pointer': 'https://github.com/floxy-21/zerorun-mvp.git',
          'repository_credentials_forwarded': False,
          'git_source': mirror.as_uri() + '/.git'}
(root / 'full-product-git-transport.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
original = root / 'full-product-vm-20260907'
receipt = json.loads((original / 'receipt.json').read_text())
assert receipt['driver']['sha256'] == hashlib.sha256((original / 'driver.py').read_bytes()).hexdigest()
assert receipt['driver']['bytes'] == (original / 'driver.py').stat().st_size
print(json.dumps(record))
