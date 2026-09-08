from pathlib import Path
expected = 'zerorun synthetic lifecycle fixture v1\n'
actual = Path('fixture.txt').read_text(encoding='utf-8')
if actual != expected:
    raise SystemExit(73)
