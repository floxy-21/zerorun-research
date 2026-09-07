"""Launch two exhaustive, disjoint regression shards with retained transport provenance."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess

root = Path('/home/floxy/zerorun-five-hour-20260907')
commit = 'a4ac905985a38308000a586140aeb4b259a7960e'
base = '0e59813c21cb023a9f490bda160420c95988b322'
driver = root / 'full_product_shard_vm.py'
assert hashlib.sha256(driver.read_bytes()).hexdigest() == '0625881ed64b90173229cecc803ec9e17561f47d7924acb1aca625eac835da96'
mirror = root / 'private-git-transport-0e59813'
bundle = root / 'fixture-fix.bundle'
assert hashlib.sha256(bundle.read_bytes()).hexdigest() == 'bd5aeb9c39390fd984213df8edaf54e99b985c218ff516f6d4969099dd1b769d'
assert subprocess.check_output(['git', '-C', str(mirror), 'rev-parse', 'fixture-fix']).decode().strip() == commit
changed = subprocess.check_output(['git', '-C', str(mirror), 'diff', '--name-only', base, commit]).decode().splitlines()
assert changed == ['tests/test_release_prerequisites.py']
fixture = subprocess.check_output(['git', '-C', str(mirror), 'show', commit + ':tests/test_release_prerequisites.py'])
amendment = json.loads((root / 'full-product-fixture-amendment-20260907/receipt.json').read_text())
assert amendment['passed'] is True
assert hashlib.sha256(fixture).hexdigest() == amendment['amendment']['after_sha256']
transport = {'base_commit': base, 'commit': commit, 'changed_files': changed,
             'original_repository_pointer': 'https://github.com/floxy-21/zerorun-mvp.git',
             'bundle_bytes': bundle.stat().st_size,
             'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
             'git_source': mirror.as_uri() + '/.git', 'repository_credentials_forwarded': False,
             'fixture_amendment_receipt_sha256': hashlib.sha256((root / 'full-product-fixture-amendment-20260907/receipt.json').read_bytes()).hexdigest(),
             'product_runtime_changed': False}
(root / 'full-shard-git-transport.json').write_text(json.dumps(transport, sort_keys=True, indent=2) + '\n')
for key, shard in [('a', 'confirmatory'), ('b', 'other90')]:
    name = 'full-product-vm-shard-' + key + '-20260907'
    output = root / name
    log = root / (name + '.launch.log')
    assert not output.exists() and not log.exists()
    argv = ['python3', str(driver), '--expected-commit', commit,
            '--git-source', transport['git_source'], '--shard', shard, '--output', str(output)]
    with log.open('xb') as stream:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    record = {'command': argv, 'pid': process.pid, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'driver_sha256': hashlib.sha256(driver.read_bytes()).hexdigest(), 'log': log.name}
    (root / (name + '.launch.json')).write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    print(json.dumps(record))
