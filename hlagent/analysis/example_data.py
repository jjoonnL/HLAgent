"""Materialize checked example files from a local image without starting it."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

from hlagent.paths import ROOT


def expected_files(kind):
    directory = ROOT / 'examples/manifests' / kind
    expected = {}
    for manifest in sorted(directory.rglob('sources.json')):
        expected[str(manifest.relative_to(directory))] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        for item in json.loads(manifest.read_text())['files']:
            relative = PurePosixPath(item['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe example manifest path.')
            name = str((manifest.parent / str(relative)).relative_to(directory))
            expected[name] = item['sha256']
    if not expected:
        raise ValueError('Missing example source manifests: ' + kind)
    return expected


def verify_files(directory, kind):
    directory = Path(directory)
    expected = expected_files(kind)
    actual = {str(path.relative_to(directory)) for path in directory.rglob('*') if path.is_file()}
    if actual != set(expected) or any(path.is_symlink() for path in directory.rglob('*')):
        raise ValueError('Example image payload has missing, linked or unexpected files.')
    for name, checksum in expected.items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != checksum:
            raise ValueError('Example file differs from its recorded source: ' + name)


def ensure_examples(kind, image, destination):
    """Copy the image's fixed payload once. Never replace existing local inputs."""
    destination = Path(destination)
    if destination.exists():
        if destination.is_symlink():
            raise ValueError('The example cache must not be a symbolic link.')
        verify_files(destination, kind)
        return
    if not shutil.which('docker'):
        raise RuntimeError('Docker is required to prepare bundled examples from the analysis image.')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    container = None
    try:
        with tempfile.TemporaryDirectory(prefix='.' + kind + '-', dir=destination.parent) as temporary:
            payload = Path(temporary) / kind
            payload.mkdir(mode=0o700)
            created = subprocess.run(['docker', 'create', '--platform', 'linux/amd64', '--pull', 'never',
                                      '--network', 'none', '--entrypoint', '/bin/true', image],
                                     capture_output=True, text=True, timeout=30, check=True)
            container = created.stdout.strip()
            subprocess.run(['docker', 'cp', container + ':/opt/hlagent/examples/' + kind + '/.', str(payload)],
                           capture_output=True, text=True, timeout=120, check=True)
            verify_files(payload, kind)
            for path in payload.rglob('*'):
                path.chmod(0o700 if path.is_dir() else 0o600)
            # Concurrent sessions may both prepare a cache. Neither may overwrite it.
            try:
                payload.rename(destination)
            except OSError:
                if not destination.exists():
                    raise
                verify_files(destination, kind)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('Could not prepare ' + kind + ' examples from local image ' + image + '. '
                           'Start Docker and prepare the example-enabled images. See docker/examples/README.md. '
                           'No analysis was started.') from error
    finally:
        if container:
            subprocess.run(['docker', 'rm', container], capture_output=True, timeout=30)
