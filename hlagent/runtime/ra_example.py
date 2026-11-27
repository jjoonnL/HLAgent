"""Run the fixed RA example after one explicit approval of the whole workflow."""

from hlagent.paths import new_run_id

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from hlagent.analysis import hatk
from hlagent.analysis.hatk_phasing import completed_phasing_inputs
from hlagent.runtime.html_report import generate_report, inspect_run
from hlagent.runtime.presentation import stage_title


STEPS = (stage_title('hatk_node1'), stage_title('hatk_node2'),
         'Genotype phasing: BEAGLE 3.3.2', stage_title('hatk_node4'))


@dataclass(frozen=True)
class ExamplePlan:
    node1: hatk.Node1Plan
    association_sources: dict
    directory: Path


def prepare_example(run_root=None, seccomp_unconfined=False):
    """Check the fixed source files without creating directories or running tools."""
    node1 = hatk.prepare_example(run_root, seccomp_unconfined)
    sources = hatk.checked_manifest(hatk.DATA / 'node2')
    hatk.phenotype_rows(hatk.DATA / 'input/genotypes.fam', hatk.DATA / 'node2/RA.phe')
    return ExamplePlan(node1, sources,
                       node1.run_dir.parent / new_run_id('ra-example', node1.run_dir.parent))


def check_sources(plan):
    current = hatk.prepare_example(plan.node1.run_dir.parent, plan.node1.seccomp_unconfined)
    if (current.data != plan.node1.data
            or hatk.checked_manifest(hatk.DATA / 'node2') != plan.association_sources):
        raise ValueError('RA example inputs changed after approval. Review a new plan.')
    changed = sorted(path for path in current.source_hashes.keys() | plan.node1.source_hashes.keys()
                     if current.source_hashes.get(path) != plan.node1.source_hashes.get(path))
    if changed:
        examples = ', '.join(changed[:3])
        raise ValueError(f'RA example source changed after approval (changed files: {len(changed)}). '
                         'This check includes interface files as well as analysis code. '
                         f'Changed files include: {examples}. '
                         'Restart HLAgent and review a new plan. Completed stages are preserved.')


def run_example(plan, execute_stage=None):
    """Execute only after approval. Preserve completed stages and stop on any failure."""
    execute_stage = execute_stage or (lambda label, stage: hatk.run_example(stage))
    plan.directory.mkdir(parents=True, mode=0o700)
    record_path = plan.directory / 'workflow.json'
    record = {'version': 1, 'workflow': 'ra_example', 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(), 'steps': [],
              'approved_plan': {'node1': plan.node1.to_dict(),
                                'association_sources': plan.association_sources,
                                'stages': list(STEPS), 'phenotype': 'RA', 'genome_build': 18,
                                'phasing': {'memory': '4g', 'seed': 1},
                                'report_directory': str(plan.directory / 'report')}}

    def save():
        record_path.write_text(json.dumps(record, indent=2) + '\n')
        record_path.chmod(0o600)

    try:
        save()
        for index, label in enumerate(STEPS):
            record['current_stage'] = label
            save()
            check_sources(plan)
            options = {'run_root': plan.node1.run_dir.parent,
                       'seccomp_unconfined': plan.node1.seccomp_unconfined}
            if index == 0:
                stage = plan.node1
            elif index == 1:
                stage = hatk.prepare_association(plan.node1.run_dir, **options)
            elif index == 2:
                # Recheck the exact Node1 outputs before reusing its panel.
                if not inspect_run(plan.node1.run_dir)['checked']:
                    raise ValueError('Node1 saved-result checks failed before phasing.')
                stage = hatk.prepare_phasing(plan.node1.run_dir / 'output/markers', 18,
                                             memory='4g', seed=1, **options)
            else:
                phased, fam, build = completed_phasing_inputs(phasing_run)
                if build != 18:
                    raise ValueError('The phasing result does not match the approved hg18 example.')
                stage = hatk.prepare_custom_omnibus(phased, fam, hatk.DATA / 'node2/RA.phe',
                                                    'RA', 18, **options)
            if stage.source_hashes != plan.node1.source_hashes:
                raise ValueError('Analysis source changed after approval. Review a new plan.')
            entry = {'stage': label, 'run_dir': str(stage.run_dir), 'status': 'running'}
            record['steps'].append(entry)
            save()
            result = execute_stage(f'{index + 1}/4 {label}', stage)
            entry['status'] = result['status']
            if result['status'] != 'completed':
                record['status'] = result['status']
                record['error'] = result.get('error', 'The stage did not complete. Later stages were not started.')
                break
            checked = inspect_run(stage.run_dir)
            if not checked['checked']:
                entry['status'] = 'failed'
                raise ValueError('Saved-result checks failed: ' + ' '.join(checked['issues']))
            entry['run_record_sha256'] = checked['record_sha256']
            if index == 2:
                phasing_run = stage.run_dir
            save()
        else:
            record['current_stage'] = 'HTML report'
            save()
            check_sources(plan)
            for entry in record['steps']:
                checked = inspect_run(entry['run_dir'])
                if not checked['checked'] or checked['record_sha256'] != entry['run_record_sha256']:
                    raise ValueError('A completed stage changed before report creation.')
            report = generate_report([step['run_dir'] for step in record['steps']],
                                     plan.directory / 'report')
            record.update(status='completed', report=str(report))
    except KeyboardInterrupt:
        record.update(status='interrupted', error='Workflow interrupted. Later stages were not started.')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        record.update(status='failed', error=str(error))
    finally:
        if record['steps'] and record['steps'][-1]['status'] == 'running':
            record['steps'][-1]['status'] = record['status']
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    return record
