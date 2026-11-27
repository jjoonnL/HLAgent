"""Find phenotype candidates in completed local run records only."""

import hashlib
import json
from pathlib import Path

from hlagent.runtime.phenotype_inputs import phenotype_columns
from hlagent.runtime.phenotype_preparation import prepared_phenotype
from hlagent.runtime.run_visibility import is_archived


def phenotype_candidates(root):
    candidates = []
    for record_path in Path(root).expanduser().resolve().glob('*/run.json'):
        if is_archived(record_path.parent):
            continue
        try:
            record = json.loads(record_path.read_text())
            if record.get('status') != 'completed':
                continue
            plan = record['plan']
            prepared = plan.get('stage') == 'phenotype_preparation'
            if prepared:
                path = record_path.parent / 'output/phenotype.phe'
                expected = next(item['sha256'] for item in record['outputs']
                                if item['path'] == 'output/phenotype.phe')
                label = 'Prepared phenotype'
            else:
                if plan.get('example') not in ('hatk_node2', 'hatk_node3', 'hatk_node4',
                                                'hatk_node2_custom', 'hatk_node3_custom', 'hatk_node4_custom'):
                    continue
                data = plan.get('data', {})
                item = next(item for item in data.get('files', [])
                            if item['path'] in ('input/phenotype.txt', 'input/RA.phe'))
                path = Path(item['source'])
                expected = item['sha256']
                label = 'Previously used phenotype'
            if not path.is_absolute() or not path.is_file():
                continue
            columns = phenotype_columns(path)
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            summary = record.get('summary') or plan.get('summary') or {}
            candidates.append({'path': str(path.resolve()), 'sha256': expected,
                               'prepared_run': str(record_path.parent.resolve()) if prepared else None,
                               'run': record_path.parent.name, 'label': label, 'columns': columns,
                               'samples': summary.get('samples', 'unknown'),
                               'started': record.get('started_at') if isinstance(record.get('started_at'), str) else '',
                               'content_key': actual_hash if actual_hash == expected else str(path.resolve())})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            continue
    unique = {}
    def preferred(candidate):
        return (bool(candidate['prepared_run']),
                not Path(candidate['path']).is_relative_to(Path(root).expanduser().resolve()))

    for item in sorted(candidates, key=lambda item: (item['started'], item['run']), reverse=True):
        previous = unique.get(item['content_key'])
        # Prefer a checked prepared result, then an original source outside runs.
        if previous is None or preferred(item) > preferred(previous):
            unique[item['content_key']] = item
    return sorted(unique.values(), key=lambda item: (item['started'], item['run']), reverse=True)


def candidate_listing(candidates, details=False):
    from hlagent.runtime.run_metadata import selection_listing
    entries = [{'title': Path(item['path']).name,
                'metadata': ['Columns: ' + ', '.join(item['columns']),
                             'Samples: ' + str(item['samples']), item['label']],
                'started': item.get('started'), 'run': item['run'], 'path': item['path']}
               for item in candidates]
    return ('Recorded phenotype files (most recent first, compatibility is not yet checked):\n\n' +
            selection_listing(entries, details) + '\n\ndetails: timestamps, run IDs and paths')


def select_candidate(text, candidates):
    index = int(text) - 1
    if not 0 <= index < len(candidates):
        raise ValueError('Choose a listed phenotype number or enter a file path.')
    item = candidates[index]
    path = Path(item['path'])
    if item['prepared_run']:
        if prepared_phenotype(item['prepared_run']) != path:
            raise ValueError('Prepared phenotype path differs from the listed candidate.')
    if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('Phenotype file changed since its recorded use. Choose another candidate or enter the path explicitly to review the changed file.')
    phenotype_columns(path)
    return str(path)
