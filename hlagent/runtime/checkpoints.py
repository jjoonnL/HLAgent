"""Private plan checkpoints; recovery always creates an unapproved fresh plan."""

from hlagent.paths import new_run_id

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from hlagent.analysis import cookhla, hatk, cookhla_to_hped
from hlagent.runtime import phenotype_preparation, summary_preparation
from hlagent.runtime.workflow_runs import read_run
from hlagent.runtime.run_visibility import is_archived


def save_checkpoint(root, plan, previous=None, status='waiting_for_approval', result_run=None, workflow_goal=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = Path(previous) if previous else root / (new_run_id('checkpoint', root, '.json') + '.json')
    record = {'version': 1, 'checkpoint': True, 'status': status,
              'updated_at': datetime.now(timezone.utc).isoformat(),
              'plan': plan if isinstance(plan, dict) else plan.to_dict()}
    if result_run is not None:
        record['result_run'] = str(result_run)
    if workflow_goal in ('node2', 'node3', 'node4', 'node5'):
        record['workflow_goal'] = workflow_goal
    temporary = root / ('.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(record, stream, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def recovery_candidates(run_root, checkpoint_root=None):
    paths = list(Path(run_root).glob('*/run.json'))
    if checkpoint_root is not None:
        paths += list(Path(checkpoint_root).glob('checkpoint-*.json'))
    result = []
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        if is_archived(path.parent if path.name == 'run.json' else path):
            continue
        try:
            record = json.loads(path.read_text())
            plan = record['plan']
            stage = plan.get('stage') or plan.get('example') or ('cookhla' if plan.get('inputs') else None)
            if stage in ('cookhla', 'cookhla_to_hped', 'phenotype_preparation', 'summary_preparation') or (
                    isinstance(stage, str) and stage.startswith('hatk_')):
                if record.get('checkpoint') and record.get('status') in ('completed', 'superseded', 'discarded'):
                    continue
                result.append((path, record))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def recovery_record(path):
    path = Path(path).expanduser().resolve()
    record = json.loads((path / 'run.json' if path.is_dir() else path).read_text())
    if not isinstance(record, dict) or not isinstance(record.get('plan'), dict):
        raise ValueError('Select a run or checkpoint with a saved plan.')
    if record.get('checkpoint'):
        actual = Path(record.get('result_run') or record['plan']['run_dir'])
        if (actual / 'run.json').is_file():
            run, actual_record = read_run(actual)
            if record.get('workflow_goal') in ('node2', 'node3', 'node4', 'node5'):
                actual_record = dict(actual_record, workflow_goal=record['workflow_goal'])
            return run, actual_record
    return (path if path.is_dir() else path.parent), record


def rebuild_plan(saved, run_root, seccomp_unconfined=False):
    """Use current constructors, not a saved command or saved approval."""
    stage = saved.get('stage')
    if stage == 'cookhla_to_hped':
        by_path = {item['path']: item['source'] for item in saved['files']}
        fresh = cookhla_to_hped.prepare_conversion(by_path['input/calls.alleles'], by_path['input/cohort.fam'], run_root)
    elif stage == 'phenotype_preparation':
        by_path = {item['path']: item['source'] for item in saved['files']}
        fresh = phenotype_preparation.prepare(by_path['input/clinical.table'], by_path['input/cohort.fam'],
                                              saved['settings'], run_root)
    elif stage == 'summary_preparation':
        fresh = summary_preparation.prepare(saved['files'][0]['source'], saved['settings'], run_root)
    elif saved.get('example') == 'cookhla' or saved.get('inputs') is not None:
        kwargs = dict(memory=saved['memory'], processes=saved['processes'], run_root=run_root,
                      seccomp_unconfined=seccomp_unconfined)
        inputs = saved.get('inputs')
        fresh = (cookhla.prepare_custom(inputs['target']['prefix'], inputs['reference']['prefix'],
                                       inputs['target_genome_build'], inputs.get('genetic_map'),
                                       inputs.get('error_rate'), **kwargs) if inputs else cookhla.prepare_example(**kwargs))
        if fresh.sources != saved['sources']:
            raise ValueError('CookHLA inputs changed since the checkpoint. Select inputs again.')
        return fresh
    elif saved.get('example') in ('hatk_node1', 'hatk_node1_custom', 'hatk_node2', 'hatk_node2_custom',
                                  'hatk_node3', 'hatk_node3_custom', 'hatk_node4', 'hatk_node4_custom',
                                  'hatk_node5', 'hatk_node5_custom', 'hatk_phasing', 'hatk_stepwise', 'hatk_imgt2seq'):
        if saved.get('example') == 'hatk_node3' and saved.get('conditional_mode') != 'single':
            raise ValueError('Archived automatic-stepwise plans are not recoverable. Use the single-scan Node3 menu.')
        old = hatk.Node1Plan(Path(run_root) / 'recovery-preview', saved['data'], {},
                            seccomp_unconfined, saved.get('parent_run'))
        data = old.data
        settings = data.get('settings', {})
        common = dict(run_root=run_root, seccomp_unconfined=seccomp_unconfined)
        if old.dictionary:
            fresh = hatk.prepare_dictionary(data['source_dir'], **common)
        elif old.phasing:
            fresh = hatk.prepare_phasing(data['panel']['prefix'], **settings, **common)
        elif old.stepwise:
            from hlagent.analysis.stepwise_firth import selection_arguments
            fresh = hatk.prepare_stepwise(data['panel']['prefix'], data['phenotype_file'],
                                          **settings, **selection_arguments(data['selection']), **common,
                                          analysis_version=data.get('stepwise_analysis_version', 2),
                                          **({'covariate_file': data['covariate_file']} if settings.get('covariates') else {}))
        elif old.custom_association or old.custom_conditional:
            prepare = hatk.prepare_custom_conditional if old.custom_conditional else hatk.prepare_custom_association
            fresh = prepare(data['panel']['prefix'], data['phenotype_file'], **settings, **common,
                            **({'covariate_file': data['covariate_file']} if settings.get('covariates') else {}),
                            **({'condition_markers': data['conditions']} if old.custom_conditional else {}))
        elif old.custom_markers:
            fresh = hatk.prepare_custom_markers(data['hped_file'], data['dictionary_dir'],
                                                settings['genome_build'], settings['imgt'], data['snp_prefix'], **common)
        elif old.custom_omnibus:
            fresh = hatk.prepare_custom_omnibus(data['phased_file'], data['fam_file'], data['phenotype_file'],
                                                **settings, **common)
        elif old.custom_meta:
            fresh = hatk.prepare_custom_meta(data['study1_file'], data['study2_file'], **settings, **common)
        elif old.omnibus:
            fresh = hatk.prepare_omnibus(**common)
        elif old.meta:
            fresh = hatk.prepare_meta(old.parent_run, **common)
        elif old.conditional:
            if data.get('condition_selection') == 'suggested_baseline_lead':
                raise ValueError('Choose explicit condition markers in the normal Node3 menu.')
            fresh = hatk.prepare_conditional(old.parent_run, condition_markers=data['conditions'], **common)
        elif old.parent_run:
            fresh = hatk.prepare_association(old.parent_run, **common)
        else:
            fresh = hatk.prepare_example(**common)
        if fresh.data != saved['data']:
            raise ValueError('HATK inputs or settings changed since the checkpoint. Select inputs again.')
        return fresh
    else:
        raise ValueError('This saved plan is outside the supported recovery scope.')
    if any(fresh[key] != saved[key] for key in ('files', 'settings') if key in fresh):
        raise ValueError('Preparation inputs or settings changed since the checkpoint. Select inputs again.')
    return fresh


def install_plan(session, plan):
    """Populate the normal role state so edit/cancel/approval keep their contracts."""
    session.discard_plan()
    session.next_run = None
    session.choosing_next = False
    session.prepared_phenotype_run = None
    if isinstance(plan, cookhla.RunPlan):
        role = 'imputation'
        agent = session.imputation
        agent.mode = 'custom' if plan.inputs else 'example'
        agent.memory, agent.processes = plan.memory, plan.processes
        agent.custom_inputs = (dict(input_prefix=plan.inputs['target']['prefix'],
                                    reference_prefix=plan.inputs['reference']['prefix'],
                                    genome_build=plan.inputs['target_genome_build'],
                                    genetic_map=plan.inputs.get('genetic_map'),
                                    error_rate=plan.inputs.get('error_rate')) if plan.inputs else {})
    elif isinstance(plan, dict):
        role, agent = 'conversion', session.conversion
        stage = plan['stage']
        by_path = {item['path']: item['source'] for item in plan['files']}
        agent.mode = {'summary_preparation': 'summary', 'phenotype_preparation': 'phenotype'}.get(stage, 'files')
        agent.inputs = ({'source': plan['input'], **plan['settings']} if agent.mode != 'files' else
                        {'alleles': by_path['input/calls.alleles'], 'fam': by_path['input/cohort.fam']})
        if agent.mode == 'phenotype':
            agent.inputs['fam'] = by_path['input/cohort.fam']
        agent.auto_report = agent.summary_report = None
    elif plan.phasing:
        role, agent = 'phasing', session.phasing
        agent.choose_inputs('panel')
        agent.inputs = {'prefix': plan.data['panel']['prefix'], 'build': plan.data['settings']['genome_build']}
        agent.memory, agent.seed = plan.data['settings']['memory'], plan.data['settings']['seed']
    else:
        role, agent = 'finemapping', session.finemapping
        agent.choose_stage()
        agent.association_run = agent.conversion_run = agent.phasing_run = None
        data = plan.data
        agent.step = plan.to_dict()['example'].replace('hatk_', '').replace('_custom', '')
        agent.parent_run = plan.parent_run
        if plan.custom_association or plan.custom_conditional or plan.stepwise:
            agent.association_inputs = {'input_prefix': data['panel']['prefix'],
                                        'phenotype_file': data['phenotype_file'], **data['settings']}
            if data['settings'].get('covariates'):
                agent.association_inputs['covariate_file'] = data['covariate_file']
        if plan.stepwise:
            from hlagent.analysis.stepwise_firth import selection_arguments
            agent.stepwise_selection = selection_arguments(data['selection'])
        if plan.custom_markers:
            agent.marker_inputs = {key: data[key] for key in ('hped_file', 'dictionary_dir', 'snp_prefix')}
            agent.marker_inputs.update(data['settings'])
        if plan.custom_omnibus:
            agent.omnibus_inputs = {key: data[key] for key in ('phased_file', 'fam_file', 'phenotype_file')}
            agent.omnibus_inputs.update(data['settings'])
        if plan.custom_meta:
            agent.meta_inputs = {key: data[key] for key in ('study1_file', 'study2_file')}
            agent.meta_inputs.update(data['settings'])
        if plan.conditional or plan.custom_conditional:
            agent.condition_markers = data['conditions']
    session.active_agent = session.last_agent = role
    agent.closed = False
    agent.input_field = None
    agent.pending_plan = plan
