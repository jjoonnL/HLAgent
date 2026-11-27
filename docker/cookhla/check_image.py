"""Check the recorded example-enabled CookHLA image filesystem and runtime."""

import argparse
import hashlib
import json
import subprocess
import sys


EXPECTED_RUNTIME = 'bc7fa42c3762c94b1ceea27312c7edff78d70e9e262b999f09ede011e6085f07'


def runtime_digest(image):
    config = image['Config']
    runtime = {
        'os': image['Os'], 'architecture': image['Architecture'],
        'layers': image['RootFS']['Layers'],
        'config': {key: config.get(key) for key in
                   ('Entrypoint', 'Cmd', 'WorkingDir', 'Env', 'User', 'Volumes', 'ExposedPorts')},
    }
    return hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', nargs='?', default='jaejoon/cookhla:v1')
    args = parser.parse_args()
    try:
        result = subprocess.run(['docker', 'image', 'inspect', args.image],
                                capture_output=True, text=True, timeout=20, check=True)
        images = json.loads(result.stdout)
        if not isinstance(images, list) or len(images) != 1:
            raise ValueError('Unexpected image metadata.')
        if runtime_digest(images[0]) != EXPECTED_RUNTIME:
            raise ValueError('Filesystem or runtime settings differ from the recorded example-enabled CookHLA image.')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'CookHLA image check failed: {error}', file=sys.stderr)
        return 1
    print('CookHLA image verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
