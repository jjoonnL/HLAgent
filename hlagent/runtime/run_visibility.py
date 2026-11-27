"""Archive list entries without moving or changing any analysis artifact."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path


def archive_marker(path):
    path = Path(path)
    return path / '.hlagent-archived.json' if path.is_dir() else path.with_name(path.name + '.archived')


def is_archived(path):
    return archive_marker(path).is_file()


def set_archived(path, archived):
    marker = archive_marker(path)
    if not archived:
        marker.unlink(missing_ok=True)
        return
    if marker.exists():
        return
    with marker.open('x', encoding='utf-8') as stream:
        os.chmod(marker, 0o600)
        json.dump({'version': 1, 'archived_at': datetime.now(timezone.utc).isoformat(),
                   'scope': 'Hidden from default lists only; original files are preserved.'}, stream)
        stream.write('\n')


def inventory(root):
    root = Path(root).expanduser().resolve()
    paths = list(root.glob('*/run.json')) + list((root / 'checkpoints').glob('checkpoint-*.json'))
    entries = []
    for path in paths:
        try:
            if not path.resolve().is_relative_to(root):
                continue
            record = json.loads(path.read_text())
            plan = record['plan']
            stage = plan.get('stage') or plan.get('example') or ('cookhla' if plan.get('inputs') else None)
            if stage not in ('cookhla', 'cookhla_to_hped', 'phenotype_preparation', 'summary_preparation') and not (
                    isinstance(stage, str) and stage.startswith('hatk_')):
                continue
            checkpoint = record.get('checkpoint') is True
            target = path if checkpoint else path.parent
            stamp = record.get('finished_at') or record.get('updated_at') or record.get('started_at')
            try:
                timestamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
            except (ValueError, AttributeError, TypeError):
                timestamp = path.stat().st_mtime
            entries.append({'path': target, 'key': str(target.relative_to(root)), 'stage': stage,
                            'status': record.get('status'), 'checkpoint': checkpoint,
                            'listed': not (checkpoint and record.get('status') in ('completed', 'superseded', 'discarded')),
                            'category': (stage, bool(plan.get('inputs')), checkpoint),
                            'timestamp': timestamp, 'archived': is_archived(target)})
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(entries, key=lambda item: (item['timestamp'], item['key']), reverse=True)


def cleanup_choices(entries, keep=()):
    """Keep explicit representatives, active records and the newest completed category."""
    keep = set(keep)
    available = {entry['key'] for entry in entries}
    if keep - available:
        raise ValueError('Requested retained records were not found: ' + ', '.join(sorted(keep - available)))
    chosen = set(keep)
    categories = set()
    for entry in entries:
        if entry['status'] in ('running', 'cancellation_unconfirmed', 'execution_requested'):
            chosen.add(entry['key'])
        if ((not entry['checkpoint'] and entry['status'] == 'completed') or
                (entry['checkpoint'] and entry['status'] == 'waiting_for_approval')):
            if entry['category'] not in categories:
                chosen.add(entry['key'])
                categories.add(entry['category'])
    return chosen
