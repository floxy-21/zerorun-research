"""Explicit no-cache invocation adapter; frozen builder and runtime stay unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--harness', type=Path, required=True)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--registry-image', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--approve-loopback-registry', action='store_true', required=True)
    args = parser.parse_args()
    harness = args.harness.resolve(strict=True)
    builder_path = harness / 'research/softwarex/handoff_image_v2/build_image.py'
    assert hashlib.sha256(builder_path.read_bytes()).hexdigest() == '21e1b2490b6f4d6649032e14011e5c8c6f457b905b366c4f1f239dd10547fc2c'
    sys.path.insert(0, str(harness))
    from research.softwarex.handoff_image_v2 import build_image as builder
    original = builder.command
    observed = []
    def command(directory, label, argv, timeout=300, stdin=None):
        if label == 'build':
            assert not observed and len(argv) == 9
            assert argv[1:7] == ['build', '--pull=false', '--network=none', '--build-arg', 'SOURCE_DATE_EPOCH=0', '--tag']
            assert argv[7].startswith('127.0.0.1:' + str(args.port) + '/zerorun-handoff-v2:')
            assert Path(argv[8]).resolve() == args.output.resolve() / 'context'
            actual = [*argv[:2], '--no-cache', *argv[2:]]
            record = {'schema':'zerorun.docker-no-cache-invocation.v3',
                      'requested_argv':argv, 'actual_argv':actual,
                      'adapter_file':'provenance/build_image_no_cache_v3.py',
                      'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'frozen_builder_sha256':hashlib.sha256(builder_path.read_bytes()).hexdigest(),
                      'only_added_argument':'--no-cache', 'runtime_source_modified':False}
            builder.h.save(directory / 'no-cache-adapter.json', record)
            observed.append(record)
            return original(directory, label, actual, timeout=timeout, stdin=stdin)
        return original(directory, label, argv, timeout=timeout, stdin=stdin)
    builder.command = command
    result = builder.build(args.engine, args.output, args.registry_image, args.port)
    if result['passed']:
        assert len(observed) == 1
    print(json.dumps(result, sort_keys=True))
    return int(not result['passed'])

if __name__ == '__main__':
    raise SystemExit(main())
