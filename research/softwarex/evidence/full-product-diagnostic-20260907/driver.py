"""Preserve a bounded first-failure diagnostic after the full-suite timeout."""
from pathlib import Path
import importlib.util
import json
import sys

root = Path('/home/floxy/zerorun-five-hour-20260907')
spec = importlib.util.spec_from_file_location('full_driver', root / 'full_product_vm-v2.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
output = root / 'full-product-diagnostic-20260907'
assert not output.exists()
output.mkdir()
parent = json.loads((root / 'full-product-vm-20260907-v2/receipt.json').read_text())
checkout = Path(parent['checkout'])
env = driver.environment()
before = driver.inventory(checkout, env)
assert before == json.loads((root / 'full-product-vm-20260907-v2/source-after.json').read_text())
env.update(PYTHONPATH=str(checkout), ZERORUN_TRUST_ROOT=str(output / 'unit-fixture-authority'))
launcher = "import pathlib,sys;root=pathlib.Path(sys.argv[1]).resolve();sys.path.insert(0,str(root));import zerorun,pytest;assert pathlib.Path(zerorun.__file__).resolve()==root/'zerorun/__init__.py';raise SystemExit(pytest.main(sys.argv[2:]))"
calls = []
row, raw = driver.invoke(output, calls, 'first_failure', [parent['python'], '-B', '-c', launcher,
    str(checkout), 'tests/test_release_prerequisites.py', '-x', '-vv', '--tb=short', '-p', 'no:cacheprovider',
    '--basetemp=' + str(output / 'pytest'), '--junitxml=' + str(output / 'tests.xml')], checkout, env, timeout=240)
after = driver.inventory(checkout, driver.environment())
driver.save(output / 'receipt.json', {'schema': 'zerorun.full-product-diagnostic.v1',
    'commit': parent['commit'], 'full_attempt': 'full-product-vm-20260907-v2',
    'driver_sha256': driver.sha(Path(__file__).read_bytes()), 'commands': calls,
    'tracked_source_unchanged': before == after, 'meaning': 'First-failure localization, not a complete regression pass'})
print(raw.decode())
