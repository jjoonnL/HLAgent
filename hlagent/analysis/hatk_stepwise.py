"""Explicit forward selection using PLINK 1.9 ordinary logistic scans."""

import csv
import io
import json
import math
from pathlib import Path
import subprocess

from hlagent.analysis.hatk_association import association_rows, marker_table
from hlagent.analysis.hatk_custom_association import aligned_text, association_command, effect_text, settings_from_run
from hlagent.analysis.stepwise_diagnostics import diagnose


def selection_settings(threshold=0.05, max_iterations=100):
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError('Selection threshold must be greater than zero and at most one.')
    if type(max_iterations) is not int or not 1 <= max_iterations <= 100:
        raise ValueError('Maximum scans must be an integer from 1 to 100.')
    return {'threshold': float(threshold), 'max_iterations': max_iterations}


def read_settings(work):
    settings = settings_from_run(work, 'hatk_stepwise')
    plan = json.loads((work / 'run.json').read_text())['plan']
    options = json.loads((work / 'input/stepwise-settings.json').read_text())
    if (options != selection_settings(**options) or plan.get('selection') != options
            or plan['data'].get('selection') != options or plan.get('conditional_mode') != 'stepwise'):
        raise ValueError('Stepwise settings differ from the approved plan.')
    return settings, options


def scan_command(work, settings, iteration, selected):
    work = Path(work).resolve()
    command = association_command(work, settings)[:-2]
    if selected:
        command += ['--condition-list', str(work / f'output/stepwise.iter{iteration:03d}.conditions.txt')]
    return command + ['--out', str(work / f'output/stepwise.iter{iteration:03d}')]


def scan_entry(work, samples, options, iteration, selected, version=2):
    prefix = f'output/stepwise.iter{iteration:03d}'
    rows, counts = association_rows(work / (prefix + '.assoc.logistic'), work / 'input/markers.bim',
                                   samples['samples'], selected, exclude_invalid_stats=True, allow_no_estimable=version == 2)
    candidates = [row for row in rows if row['ESTIMABLE']]
    if not candidates and version == 1:
        raise ValueError('No estimable remaining marker tests. Selection cannot continue.')
    best = min(candidates, key=lambda row: (float(row['P']), row['SNP'])) if candidates else None
    entry = {'iteration': iteration, 'conditioning_markers': selected.copy(),
            'best_marker': best['SNP'] if best else None, 'best_p': float(best['P']) if best else None,
            'accepted': best is not None and float(best['P']) < options['threshold'], 'counts': counts,
            'association_file': prefix + '.assoc.logistic'}
    if version == 2:
        settings, _ = read_settings(work)
        aligned = [row.split() for row in aligned_text(work, settings)[0].splitlines()[1:]]
        entry['diagnostics'] = diagnose(work, rows, selected, aligned)
        if entry['diagnostics']['unresolved_marker_ids']:
            entry['accepted'] = False
    return entry


def stop_reason(entry, selected, marker_count, options):
    if entry.get('diagnostics', {}).get('unresolved_marker_ids'):
        return 'unresolved_marker_tests'
    if entry['best_marker'] is None:
        return 'no_estimable_remaining_tests'
    if not entry['accepted']:
        return 'threshold_not_met'
    if len(selected) == marker_count:
        return 'all_markers_selected'
    if entry['iteration'] == options['max_iterations']:
        return 'iteration_limit'
    return None


def result_summary(samples, settings, options, history, selected, reason, version=2):
    summary = {**samples, **options, 'mode': 'stepwise', 'analysis_version': version,
            'phenotype_name': settings['phenotype_name'], 'genome_build': settings['genome_build'],
            'iterations': len(history), 'selected_markers': selected, 'selected_marker_count': len(selected), 'stop_reason': reason,
            'threshold_reached': reason == 'threshold_not_met',
            'final_unestimable_markers': history[-1]['counts']['unestimable_markers'],
            'selection_scope': 'estimable marker tests only',
            'regression_engine': 'PLINK 1.9 additive logistic', 'covariates': []}
    if version == 2:
        summary.update(selection_status='complete' if reason in ('threshold_not_met', 'all_markers_selected') else 'incomplete',
                       unresolved_marker_count=len(history[-1]['diagnostics']['unresolved_marker_ids']),
                       exclusion_counts=history[-1]['diagnostics']['counts'],
                       selection_scope='finite tests after exact structural exclusions. Stop on unclassified failures.')
    return summary


def selection_text(history):
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
    writer.writerow(['ITERATION', 'MARKER', 'P', 'ACCEPTED', 'CONDITIONING_MARKERS', 'UNESTIMABLE'])
    for entry in history:
        writer.writerow([entry['iteration'], entry['best_marker'], entry['best_p'], entry['accepted'],
                         ','.join(entry['conditioning_markers']), entry['counts']['unestimable_markers']])
    return stream.getvalue()


def run_stepwise(work):
    work = Path(work).resolve()
    if json.loads((work / 'run.json').read_text())['plan'].get('stepwise_analysis_version') == 3:
        from hlagent.analysis.stepwise_firth import run_stepwise as run_firth
        return run_firth(work)
    settings, options = read_settings(work)
    if json.loads((work / 'run.json').read_text())['plan'].get('stepwise_analysis_version') != 2:
        raise ValueError('Review a new stepwise plan with the unresolved-test stopping policy.')
    aligned, samples = aligned_text(work, settings)
    (work / 'output/association.aligned.phe').write_text(aligned)
    (work / 'output/association.effect-alleles.txt').write_text(effect_text(work))
    marker_count = len(marker_table(work / 'input/markers.bim'))
    history, selected = [], []
    for iteration in range(1, options['max_iterations'] + 1):
        prefix = work / f'output/stepwise.iter{iteration:03d}'
        if selected:
            Path(str(prefix) + '.conditions.txt').write_text('\n'.join(selected) + '\n')
        command = scan_command(work, settings, iteration, selected)
        Path(str(prefix) + '.command.json').write_text(json.dumps(command, indent=2) + '\n')
        print(f'Stepwise scan {iteration}. Conditioning markers: {len(selected)}.', flush=True)
        subprocess.run(command, check=True)
        entry = scan_entry(work, samples, options, iteration, selected)
        history.append(entry)
        (work / 'output/stepwise.iterations.json').write_text(json.dumps(history, indent=2) + '\n')
        if entry['accepted']:
            selected.append(entry['best_marker'])
        reason = stop_reason(entry, selected, marker_count, options)
        if reason:
            break
    summary = result_summary(samples, settings, options, history, selected, reason)
    (work / 'output/stepwise.selected-markers.txt').write_text(''.join(name + '\n' for name in selected))
    (work / 'output/stepwise.selection.tsv').write_text(selection_text(history))
    (work / 'output/stepwise.summary.json').write_text(json.dumps(summary, indent=2) + '\n')


def validate_stepwise_outputs(work):
    work = Path(work).resolve()
    if json.loads((work / 'run.json').read_text())['plan'].get('stepwise_analysis_version') == 3:
        from hlagent.analysis.stepwise_firth import validate_outputs
        return validate_outputs(work)
    settings, options = read_settings(work)
    aligned, samples = aligned_text(work, settings)
    if ((work / 'output/association.aligned.phe').read_text() != aligned
            or (work / 'output/association.effect-alleles.txt').read_text() != effect_text(work)):
        raise ValueError('Stepwise sample alignment or effect alleles changed.')
    saved = json.loads((work / 'output/stepwise.summary.json').read_text())
    version = saved.get('analysis_version', 1)
    if version not in (1, 2):
        raise ValueError('Unsupported stepwise analysis version.')
    plan_version = json.loads((work / 'run.json').read_text())['plan'].get('stepwise_analysis_version', 1)
    if version != plan_version:
        raise ValueError('Stepwise result version differs from its approved plan.')
    history = json.loads((work / 'output/stepwise.iterations.json').read_text())
    if not isinstance(history, list) or not 1 <= len(history) <= options['max_iterations']:
        raise ValueError('Invalid stepwise iteration history.')
    marker_count = len(marker_table(work / 'input/markers.bim'))
    selected, reason = [], None
    for iteration, entry in enumerate(history, 1):
        if reason is not None:
            raise ValueError('Stepwise history continues after its stopping point.')
        prefix = work / f'output/stepwise.iter{iteration:03d}'
        command = json.loads(Path(str(prefix) + '.command.json').read_text())
        if command not in (scan_command(work, settings, iteration, selected),
                           scan_command('/work', settings, iteration, selected)):
            raise ValueError('Stepwise command differs from the reviewed model.')
        if selected and Path(str(prefix) + '.conditions.txt').read_text().splitlines() != selected:
            raise ValueError('Stepwise conditions differ from selection history.')
        if entry != scan_entry(work, samples, options, iteration, selected, version):
            raise ValueError('Stepwise selection differs from the raw scan results.')
        if entry['accepted']:
            selected.append(entry['best_marker'])
        reason = stop_reason(entry, selected, marker_count, options)
    if reason is None:
        raise ValueError('Stepwise history ends before a valid stopping point.')
    summary = result_summary(samples, settings, options, history, selected, reason, version)
    if (json.loads((work / 'output/stepwise.summary.json').read_text()) != summary
            or (work / 'output/stepwise.selected-markers.txt').read_text() != ''.join(n + '\n' for n in selected)
            or (work / 'output/stepwise.selection.tsv').read_text() != selection_text(history)):
        raise ValueError('Stepwise summary or selected marker files differ from the checked history.')
    if version == 1:
        summary = {**summary, 'selection_status': 'unverified_legacy',
                   'assessment': 'Legacy selection skipped unclassified tests. A reliable threshold stop is not established.'}
    return summary
