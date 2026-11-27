"""Read checked run artifacts for explicit continuation and recovery."""

import json
from pathlib import Path

from hlagent.analysis.hatk import digest
from hlagent.analysis.hatk_custom_association import validate_custom_association_outputs
from hlagent.analysis.hatk_association import validate_association_outputs


def read_run(run):
    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    if not isinstance(record, dict) or not isinstance(record.get('plan'), dict):
        raise ValueError('Select a recorded analysis or preparation run.')
    return run, record


def local_artifact(run, relative):
    """Reject paths outside the selected run, including escaping symlinks."""
    path = (run / relative).resolve()
    if path == run or not path.is_relative_to(run):
        raise ValueError('A recorded artifact path leaves the selected run.')
    return path


def checked_files(run, files):
    for item in files:
        path = local_artifact(run, item['path'])
        if not path.is_file() or digest(path) != item['sha256']:
            raise ValueError('A recorded artifact is missing or differs from its hash: ' + item['path'])


def association_inputs(run):
    """Reuse staged Node2 inputs, never choose a condition from its P values."""
    run, record = read_run(run)
    plan = record['plan']
    if record.get('status') != 'completed' or plan.get('example') not in ('hatk_node2', 'hatk_node2_custom'):
        raise ValueError('Select a completed Node2 run.')
    checked_files(run, plan['data']['files'])
    checked_files(run, record['outputs'])
    custom = plan['example'] == 'hatk_node2_custom'
    (validate_custom_association_outputs if custom else validate_association_outputs)(run)
    inputs = {'input_prefix': str(run / 'input/markers'),
            'phenotype_file': str(run / ('input/phenotype.txt' if custom else 'input/RA.phe')),
            'phenotype_name': plan['phenotype'], 'genome_build': plan['genome_build']}
    if plan.get('covariates'):
        inputs.update(covariates=plan['covariates'], covariate_file=str(run / 'input/covariates.txt'))
    return inputs


def continuation(record):
    """Only supported completed artifacts establish a next input."""
    if record.get('status') != 'completed':
        return None
    plan = record['plan']
    stage = plan.get('stage') or plan.get('example')
    return {'cookhla': 'conversion', 'cookhla_to_hped': 'finemapping',
            'hatk_node1': 'node1', 'hatk_node1_custom': 'node1',
            'hatk_node2': 'node3', 'hatk_node2_custom': 'node3',
            'hatk_phasing': 'node4', 'phenotype_preparation': 'phenotype',
            'summary_preparation': 'summary'}.get(stage, 'conversion' if plan.get('inputs') is not None else None)
