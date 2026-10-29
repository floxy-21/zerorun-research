"""Exercise the unchanged qualification probe against the installed server.

The coordinator can import research helpers from the checkout; the actual MCP
child has no PYTHONPATH and imports the wheel installed in this interpreter.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.q1q2 import qualification_docker_probe as probe


def main():
    original = probe.invoke

    def installed(repo, env, **kwargs):
        env = dict(env)
        env.pop('PYTHONPATH', None)
        env['PYTHONNOUSERSITE'] = '1'
        return original(repo, env, **kwargs)

    probe.invoke = installed
    return probe.main()


if __name__ == '__main__':
    raise SystemExit(main())
