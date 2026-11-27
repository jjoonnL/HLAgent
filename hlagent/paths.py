"""Repository and image paths independent of the caller's working directory."""

import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def new_run_id(prefix, parent=None, suffix=''):
    """Reserve a readable UTC identity without creating the planned run directory."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', prefix) or '/' in suffix or '\\' in suffix:
        raise ValueError('Use a filename-safe run prefix and suffix.')
    base = prefix + '-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
    reservations = ROOT / 'runs/.run-ids'
    reservations.mkdir(parents=True, exist_ok=True, mode=0o700)
    index = 1
    while True:
        name = base + (f'-{index:02d}' if index > 1 else '')
        index += 1
        if parent is not None and (Path(parent) / (name + suffix)).exists():
            continue
        try:
            descriptor = os.open(reservations / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        return name


def image_source_hashes(root=ROOT):
    """Hash exactly the package and vendored sources copied into the HATK image."""
    root = Path(root)
    vendor = root / 'vendor/hatk'
    paths = [root / 'hlagent/__init__.py', root / 'hlagent/paths.py', vendor / 'sources.json']
    paths += sorted((root / 'hlagent/analysis').glob('*.py'))
    paths += sorted(vendor.rglob('*.py')) + sorted(vendor.rglob('*.R'))
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
