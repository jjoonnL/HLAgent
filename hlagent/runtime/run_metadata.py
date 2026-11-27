"""Compact saved-run labels and explicit UTC details. Never change saved records."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from hlagent.runtime.presentation import stage_title


def utc_timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def utc_text(value, date_only=False):
    stamp = utc_timestamp(value)
    if stamp is None:
        return 'not recorded with a time zone'
    return stamp.strftime('%Y-%m-%d (UTC)' if date_only else '%Y-%m-%d %H:%M:%S UTC')


def read_metadata(path):
    try:
        record = json.loads(Path(path).read_text())
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def run_entry(path, record=None, show_status=False):
    path = Path(path)
    record = read_metadata(path) if record is None else record
    plan = record.get('plan', {})
    data = plan.get('data', {})
    settings = data.get('settings', plan.get('settings', {}))
    summary = record.get('summary') or plan.get('summary') or {}
    stage = plan.get('stage') or plan.get('example') or ('cookhla' if plan.get('inputs') is not None else None)
    source = (plan.get('input') or data.get('hped_file') or data.get('input_prefix')
              or data.get('panel', {}).get('prefix') or data.get('source_dir'))
    samples = summary.get('output_samples', summary.get('samples', summary.get('input_samples', data.get('input_samples', data.get('panel', {}).get('samples')))))
    trait = summary.get('phenotype_name') or settings.get('phenotype_name') or plan.get('phenotype')
    build = summary.get('genome_build') or settings.get('genome_build') or plan.get('genome_build')
    imgt = summary.get('imgt') or settings.get('imgt') or plan.get('imgt')
    metadata = []
    if source:
        metadata.append('Data: ' + Path(source).stem)
    elif stage:
        mode = {'cookhla_to_hped': 'conversion', 'phenotype_preparation': 'clinical table',
                'summary_preparation': 'association summary', 'hatk_imgt2seq': 'IMGT snapshot'}.get(stage)
        if mode is None:
            mode = 'custom' if plan.get('inputs') is not None or plan.get('input_mode') or str(stage).endswith('_custom') else 'example'
        metadata.append('Input: ' + mode)
    if plan.get('dataset'):
        metadata.append('Dataset: ' + str(plan['dataset']))
    if trait:
        metadata.append('Trait: ' + str(trait))
    if plan.get('covariates') or settings.get('covariates'):
        metadata.append('Covariates: ' + ', '.join(plan.get('covariates') or settings['covariates']))
    if samples is not None:
        metadata.append('Samples: ' + str(samples))
    if build:
        metadata.append('Build: hg' + str(build))
    if imgt:
        tag = str(imgt)
        version = f'{tag[0]}.{tag[1:-1]}.{tag[-1]}' if tag.startswith('3') and len(tag) in (3, 4) and tag.isdigit() else tag
        metadata.append('IMGT: ' + version)
    for key, label in (('markers', 'Markers'), ('tested_markers', 'Markers'), ('total_tests', 'Tests'), ('positions', 'Positions')):
        if key in summary:
            metadata.append(label + ': ' + str(summary[key]))
            break
    selection = data.get('selection', {})
    if stage == 'hatk_stepwise':
        if selection.get('threshold') is not None:
            metadata.append(f"P threshold: {selection['threshold']:.3g}")
        if selection.get('min_mac') is not None:
            metadata.append('Minimum MAC: ' + str(selection['min_mac']))
    if stage == 'hatk_phasing' and settings.get('seed') is not None:
        metadata.append('Seed: ' + str(settings['seed']))
    if summary.get('input_samples') is not None and samples != summary['input_samples']:
        metadata.append('Input samples: ' + str(summary['input_samples']))
    if show_status:
        metadata.append('Status: ' + record.get('status', 'unknown'))
    return {'title': stage_title(stage) if stage else 'Saved analysis',
            'metadata': metadata or ['Metadata unavailable'], 'started': record.get('started_at'),
            'run': path.parent.name if path.name == 'run.json' else path.stem, 'path': str(path.resolve())}


def selection_listing(entries, details=False):
    """Preserve selection numbering. Dates appear only for repeated display contexts."""
    keys = [(entry['title'], tuple(entry['metadata'])) for entry in entries]
    counts = Counter(keys)
    latest = {}
    for key, entry in zip(keys, entries):
        stamp = utc_timestamp(entry.get('started'))
        if stamp is not None:
            latest[key] = max(stamp, latest.get(key, stamp))
    lines = []
    for number, (key, entry) in enumerate(zip(keys, entries), 1):
        lines.append(f"{number}. {entry['title']}")
        metadata = entry['metadata']
        for start in range(0, len(metadata), 3):
            lines.append('   ' + ' | '.join(metadata[start:start + 3]))
        if details:
            lines += ['   Started: ' + utc_text(entry.get('started')),
                      '   Run: ' + entry.get('run', 'unknown'), '   Path: ' + entry['path']]
        elif counts[key] > 1:
            stamp = utc_timestamp(entry.get('started'))
            newest = ' | Latest' if stamp is not None and stamp == latest.get(key) else ''
            lines.append('   Date: ' + utc_text(entry.get('started'), date_only=True) + newest)
        lines.append('')
    if not entries:
        lines.append('(none found)')
    return '\n'.join(lines).rstrip()


def saved_run_listing(paths, details=False, show_status=False):
    return selection_listing([run_entry(path, show_status=show_status) for path in paths], details)
