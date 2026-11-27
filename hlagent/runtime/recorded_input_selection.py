"""Offer clinical sources and matching dictionaries from completed local records."""

import hashlib
import json
from pathlib import Path

from hlagent.analysis.hatk_custom_markers import dictionary_names, marker_settings
from hlagent.runtime.run_visibility import is_archived


def recorded_candidates(root, kind, build=None, imgt=None):
    candidates = []
    for record_path in Path(root).expanduser().resolve().glob('*/run.json'):
        if is_archived(record_path.parent):
            continue
        try:
            record = json.loads(record_path.read_text())
            if record.get('status') != 'completed':
                continue
            plan = record['plan']
            if kind == 'clinical':
                if plan.get('stage') != 'phenotype_preparation':
                    continue
                item = next(item for item in plan['files'] if item['path'] == 'input/clinical.table')
                path = Path(item['source'])
                files = [{'source': str(path), 'sha256': item['sha256']}]
                detail = 'Recorded delimiter: ' + plan['settings']['delimiter']
            elif kind == 'dictionary':
                if plan.get('example') not in ('hatk_node1', 'hatk_node1_custom', 'hatk_imgt2seq'):
                    continue
                if (plan.get('genome_build'), plan.get('imgt')) != (build, imgt):
                    continue
                names = dictionary_names(marker_settings(build, imgt))
                data = plan['data']
                path = (record_path.parent / 'output' if plan.get('example') == 'hatk_imgt2seq' else
                        Path(data['dictionary_dir']) if data.get('dictionary_dir') else record_path.parent / 'dictionary')
                files = []
                for name in names:
                    item = (next(item for item in record['outputs'] if item['path'] == 'output/' + name)
                            if plan.get('example') == 'hatk_imgt2seq' else
                            next(item for item in data['files'] if item['path'] == 'dictionary/' + name))
                    files.append({'source': str(path / name), 'sha256': item['sha256']})
                detail = f'Recorded hg{build} / IMGT {imgt}'
            else:
                raise ValueError('Unknown recorded input kind.')
            if not path.is_absolute() or not all(Path(item['source']).is_file() for item in files):
                continue
            candidates.append({'path': str(path.resolve()), 'files': files, 'detail': detail,
                               'run': record_path.parent.name, 'record_path': str(record_path),
                               'started': record.get('started_at') if isinstance(record.get('started_at'), str) else ''})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            continue
    unique = {}
    for item in sorted(candidates, key=lambda item: (item['started'], item['run']), reverse=True):
        unique.setdefault(item['path'], item)
    return list(unique.values())


def recorded_listing(candidates, label, details=False):
    from hlagent.runtime.run_metadata import read_metadata, run_entry, selection_listing
    entries = []
    for item in candidates:
        record = read_metadata(item.get('record_path', ''))
        entry = run_entry(item.get('record_path', item['path']), record)
        entry.update(title='HATK dictionary' if label == 'dictionary' else Path(item['path']).name,
                     run=item['run'], path=item['path'], started=item.get('started'))
        if label == 'dictionary':
            entry['metadata'] = [part for part in entry['metadata'] if part.startswith(('Build:', 'IMGT:'))] or [item['detail']]
        else:
            entry['metadata'] = [part for part in entry['metadata'] if not part.startswith(('Data:', 'Input:'))]
            entry['metadata'].append(item['detail'])
        entries.append(entry)
    return ('Select a candidate by number, or enter its path.\n'
            'No settings or mappings are selected automatically.\n\n'
            f'Recorded {label} candidates (most recent first, inputs will be rechecked):\n\n' +
            selection_listing(entries, details) + '\n\ndetails: timestamps, run IDs and paths')


def selected_recorded_path(text, candidates):
    if not text.isdigit():
        return str(Path(text).expanduser().resolve())
    index = int(text) - 1
    if not 0 <= index < len(candidates):
        raise ValueError('Choose a listed candidate number or enter a path.')
    item = candidates[index]
    for entry in item['files']:
        if hashlib.sha256(Path(entry['source']).read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('A candidate file changed since its recorded use. Choose another candidate or enter the path explicitly to review the changed input.')
    return item['path']
