"""List recorded completed runs; selected files are checked by each analysis role."""

import json
from pathlib import Path
from hlagent.runtime.run_visibility import is_archived


def completed_runs(root, kind):
    names = []
    for directory in sorted(Path(root).glob('*')):
        if not directory.is_dir() or is_archived(directory):
            continue
        try:
            record = json.loads((directory / 'run.json').read_text())
            plan = record.get('plan', {})
            if kind == 'cookhla':
                matches = plan.get('example') == 'cookhla' or plan.get('inputs') is not None
            elif kind == 'conversion':
                matches = plan.get('stage') == 'cookhla_to_hped'
            elif kind == 'phasing':
                matches = plan.get('example') == 'hatk_phasing'
            elif kind == 'node1':
                matches = plan.get('example') in ('hatk_node1', 'hatk_node1_custom')
            elif kind == 'summary':
                matches = plan.get('stage') == 'summary_preparation'
            elif kind == 'phenotype':
                matches = plan.get('stage') == 'phenotype_preparation'
            else:
                raise ValueError('Unknown run kind.')
            if record.get('status') == 'completed' and matches:
                names.append(directory.name)
        except (OSError, ValueError, AttributeError):
            continue
    return names


def selected_run(text, names, root):
    if text.isdigit():
        index = int(text) - 1
        if not 0 <= index < len(names):
            raise ValueError('Choose a listed run number or enter a run directory.')
        return (Path(root) / names[index]).resolve()
    if text in names:
        return (Path(root) / text).resolve()
    return Path(text).expanduser().resolve()


def run_listing(names, label, root, details=False):
    from hlagent.runtime.run_metadata import saved_run_listing
    listing = saved_run_listing([Path(root) / name / 'run.json' for name in names], details)
    return ('Select a run by number. Known run names and directories are also accepted.\n'
            'The selected files will be rechecked.\n\n'
            f'Recorded completed {label} runs:\n\n' + listing +
            '\n\ndetails: timestamps, run IDs and paths')
