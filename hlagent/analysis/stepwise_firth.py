"""Version 3 stepwise selection with PLINK2 always-Firth and reviewed eligibility."""

from collections import Counter
import csv
import json
import math
from pathlib import Path
import subprocess

from hlagent.analysis.hatk_association import marker_table
from hlagent.analysis.hatk_custom_association import aligned_text, effect_text, settings_from_run, covariate_text, write_covariates, check_covariates
from hlagent.analysis.stepwise_diagnostics import read_columns
from hlagent.analysis.hatk_stepwise import selection_settings, selection_text

SCREENED = {'CONST_OMITTED_ALLELE', 'CONST_ALLELE', 'CORR_TOO_HIGH',
            'VIF_INFINITE', 'VIF_TOO_HIGH', 'RANK_DEFICIENT'}

def numeric(text):
    try:
        value = float(text)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None

def minor_count(values):
    observed = [g for g in values if g is not None]
    copies = sum(observed)
    return min(copies, 2 * len(observed) - copies)

def reviewed_selection(columns, threshold=None, max_iterations=100, min_mac=20):
    if type(min_mac) is not int or min_mac < 0:
        raise ValueError('Minimum MAC must be a nonnegative integer. Zero disables the MAC filter.')
    eligible = sum(minor_count(values) >= min_mac for values in columns.values())
    if not eligible:
        raise ValueError('No candidate markers pass the minimum MAC.')
    options = selection_settings(0.05 / eligible if threshold is None else threshold, max_iterations)
    return {**options, 'threshold_mode': 'bonferroni_initial_candidates' if threshold is None else 'explicit',
            'min_mac': min_mac, 'input_marker_count': len(columns), 'initial_candidate_count': eligible}

def parse_scan(path, variants, columns, conditions, sample_count, min_mac):
    with path.open() as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        required = {'ID', 'A1', 'POS', 'TEST', 'OBS_CT', 'P', 'OR', 'LOG(OR)_SE', 'L95', 'U95', 'ERRCODE'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Missing Firth result columns.')
        rows = [r for r in reader if r['TEST'] == 'ADD' and r['ID'] not in conditions]
    expected = set(variants) - set(conditions)
    if len(rows) != len(expected) or {r['ID'] for r in rows} != expected:
        raise ValueError('Missing or duplicate marker rows in Firth scan.')
    keep = [i for i in range(sample_count) if all(columns[n][i] is not None for n in conditions)]
    candidates, unresolved, below_mac, screened = [], [], [], 0
    for row in rows:
        marker = row['ID']
        if row['A1'] != variants[marker][4] or row['POS'] != variants[marker][3]:
            raise ValueError('Effect allele or coordinate mismatch: ' + marker)
        n = numeric(row['OBS_CT'])
        if n != sum(columns[marker][i] is not None for i in keep):
            raise ValueError('Complete-case sample count mismatch: ' + marker)
        if minor_count([columns[marker][i] for i in keep]) < min_mac:
            below_mac.append(marker)
            continue
        if row['ERRCODE'] in SCREENED:
            screened += 1
            continue
        p, odds, se, lower, upper = [numeric(row[k]) for k in ('P', 'OR', 'LOG(OR)_SE', 'L95', 'U95')]
        if (row['ERRCODE'] != '.' or p is None or not 0 <= p <= 1
                or odds is None or odds <= 0 or se is None or se <= 0
                or lower is None or upper is None or not 0 < lower <= odds <= upper):
            unresolved.append(marker)
        else:
            candidates.append(row)
    best = min(candidates, key=lambda r: (float(r['P']), r['ID'])) if candidates else None
    return {'tested_markers': len(rows), 'finite_candidates': len(candidates),
            'below_min_mac_count': len(below_mac), 'below_min_mac_marker_ids': below_mac,
            'screened_eligible_count': screened,
            'error_counts': dict(Counter(r['ERRCODE'] for r in rows)),
            'unresolved_marker_ids': unresolved, 'best': best}


def selection_arguments(options):
    """Recover user choices, rather than passing derived counts back as inputs."""
    return {'threshold': None if options.get('threshold_mode') == 'bonferroni_initial_candidates' else options.get('threshold'),
            'max_iterations': options.get('max_iterations', 100), 'min_mac': options.get('min_mac', 20)}


def panel_selection(prefix, samples, threshold=None, max_iterations=100, min_mac=20):
    names = list(marker_table(str(prefix) + '.bim'))
    return reviewed_selection(read_columns(None, names, samples, prefix), threshold, max_iterations, min_mac)


def context(work):
    settings = settings_from_run(work, 'hatk_stepwise')
    plan = json.loads((work / 'run.json').read_text())['plan']
    options = json.loads((work / 'input/stepwise-settings.json').read_text())
    aligned, samples = aligned_text(work, settings)
    covariate_text(work, settings)
    variants = {r[1]: r for r in [line.split() for line in (work / 'input/markers.bim').read_text().splitlines()]}
    columns = read_columns(work, list(variants), samples['samples'])
    expected = reviewed_selection(columns, **selection_arguments(options))
    if (plan.get('stepwise_analysis_version') != 3 or plan['data'].get('stepwise_analysis_version') != 3
            or options != expected or plan.get('selection') != options or plan['data'].get('selection') != options
            or plan.get('conditional_mode') != 'stepwise'):
        raise ValueError('Firth stepwise settings differ from the reviewed version 3 plan.')
    return settings, options, aligned, samples, variants, columns


def scan_command(work, settings, iteration, selected):
    work = Path(work).resolve()
    command = ['plink2', '--bfile', str(work / 'input/markers'),
               '--pheno', str(work / 'output/association.aligned.phe'), '--pheno-name', settings['phenotype_name'],
               '--glm', 'hide-covar', 'firth', 'omit-ref', 'allow-no-covars', '--ci', '0.95', '--threads', '1']
    if settings.get('covariates'):
        command.remove('allow-no-covars')
        command += ['--covar', str(work / 'output/association.aligned.cov'),
                    '--covar-name', ','.join(settings['covariates'])]
    if selected:
        command += ['--condition-list', str(work / f'output/stepwise.iter{iteration:03d}.conditions.txt')]
    return command + ['--out', str(work / f'output/stepwise.iter{iteration:03d}')]


def scan_entry(work, settings, samples, options, variants, columns, iteration, selected):
    filename = f'output/stepwise.iter{iteration:03d}.{settings["phenotype_name"]}.glm.firth'
    checked = parse_scan(work / filename, variants, columns, selected, samples['samples'], options['min_mac'])
    best = checked['best']
    accepted = best is not None and float(best['P']) < options['threshold'] and not checked['unresolved_marker_ids']
    low_conditions = []
    if accepted:
        proposed = [*selected, best['ID']]
        keep = [i for i in range(samples['samples']) if all(columns[n][i] is not None for n in proposed)]
        low_conditions = [n for n in proposed if minor_count([columns[n][i] for i in keep]) < options['min_mac']]
        accepted = not low_conditions
    return {'iteration': iteration, 'conditioning_markers': selected.copy(),
            'best_marker': best['ID'] if best else None, 'best_p': float(best['P']) if best else None,
            'accepted': accepted, 'association_file': filename,
            'counts': {'tested_markers': checked['tested_markers'], 'estimable_markers': checked['finite_candidates'],
                       'unestimable_markers': checked['tested_markers'] - checked['finite_candidates'] - checked['below_min_mac_count'],
                       'below_min_mac': checked['below_min_mac_count']},
            'diagnostics': {'unresolved_marker_ids': checked['unresolved_marker_ids'],
                            'below_min_mac_marker_ids': checked['below_min_mac_marker_ids'],
                            'conditioning_mac_marker_ids': low_conditions,
                            'screened_eligible_count': checked['screened_eligible_count'], 'error_counts': checked['error_counts']}}


def stop_reason(entry, selected, marker_count, options):
    if entry['diagnostics']['unresolved_marker_ids']:
        return 'unresolved_marker_tests'
    if entry['diagnostics']['conditioning_mac_marker_ids']:
        return 'conditioning_marker_below_min_mac'
    if entry['best_marker'] is None:
        return 'no_eligible_remaining_tests'
    if not entry['accepted']:
        return 'threshold_not_met'
    if len(selected) == marker_count:
        return 'all_markers_selected'
    if entry['iteration'] == options['max_iterations']:
        return 'iteration_limit'
    return None


def summary(samples, settings, options, history, selected, reason):
    last = history[-1]
    return {**samples, **options, 'analysis_version': 3, 'mode': 'stepwise',
            'phenotype_name': settings['phenotype_name'], 'genome_build': settings['genome_build'],
            'regression_engine': 'PLINK 2 always-Firth additive logistic', 'covariates': settings.get('covariates', []),
            'wald_covariance': 'PLINK leverage-weighted working information',
            'iterations': len(history), 'selected_markers': selected, 'selected_marker_count': len(selected),
            'stop_reason': reason, 'threshold_reached': reason == 'threshold_not_met',
            'selection_status': 'complete' if reason in ('threshold_not_met', 'all_markers_selected') else 'incomplete',
            'initial_excluded_by_mac': options['input_marker_count'] - options['initial_candidate_count'],
            'final_unestimable_markers': last['counts']['unestimable_markers'],
            'final_below_min_mac': last['counts']['below_min_mac'],
            'final_screening_exclusions': last['diagnostics']['screened_eligible_count'],
            'unresolved_marker_count': len(last['diagnostics']['unresolved_marker_ids']),
            'exclusion_counts': {k: v for k, v in last['diagnostics']['error_counts'].items() if k != '.'},
            'selection_scope': 'MAC-eligible finite tests after PLINK screening. This is not post-selection inference.'}


def run_stepwise(work):
    work = Path(work).resolve()
    settings, options, aligned, samples, variants, columns = context(work)
    write_covariates(work, settings)
    (work / 'output/association.aligned.phe').write_text(aligned)
    (work / 'output/association.effect-alleles.txt').write_text(effect_text(work))
    history, selected = [], []
    for iteration in range(1, options['max_iterations'] + 1):
        prefix = work / f'output/stepwise.iter{iteration:03d}'
        if selected:
            Path(str(prefix) + '.conditions.txt').write_text('\n'.join(selected) + '\n')
        command = scan_command(work, settings, iteration, selected)
        Path(str(prefix) + '.command.json').write_text(json.dumps(command, indent=2) + '\n')
        print(f'Stepwise scan {iteration}. Conditioning markers: {len(selected)}.', flush=True)
        subprocess.run(command, check=True)
        entry = scan_entry(work, settings, samples, options, variants, columns, iteration, selected)
        history.append(entry)
        (work / 'output/stepwise.iterations.json').write_text(json.dumps(history, indent=2) + '\n')
        if entry['accepted']:
            selected.append(entry['best_marker'])
        reason = stop_reason(entry, selected, len(variants), options)
        if reason:
            break
    result = summary(samples, settings, options, history, selected, reason)
    (work / 'output/stepwise.selected-markers.txt').write_text(''.join(n + '\n' for n in selected))
    (work / 'output/stepwise.selection.tsv').write_text(selection_text(history))
    (work / 'output/stepwise.summary.json').write_text(json.dumps(result, indent=2) + '\n')


def validate_outputs(work):
    work = Path(work).resolve()
    settings, options, aligned, samples, variants, columns = context(work)
    check_covariates(work, settings)
    if ((work / 'output/association.aligned.phe').read_text() != aligned
            or (work / 'output/association.effect-alleles.txt').read_text() != effect_text(work)):
        raise ValueError('Firth stepwise sample alignment or effect alleles changed.')
    history = json.loads((work / 'output/stepwise.iterations.json').read_text())
    if not isinstance(history, list) or not 1 <= len(history) <= options['max_iterations']:
        raise ValueError('Invalid Firth stepwise scan history.')
    selected, reason = [], None
    for iteration, entry in enumerate(history, 1):
        if reason is not None:
            raise ValueError('Firth stepwise history continues after a stopping point.')
        prefix = work / f'output/stepwise.iter{iteration:03d}'
        command = json.loads(Path(str(prefix) + '.command.json').read_text())
        if command not in (scan_command(work, settings, iteration, selected), scan_command('/work', settings, iteration, selected)):
            raise ValueError('Firth stepwise command differs from the approved model.')
        if selected and Path(str(prefix) + '.conditions.txt').read_text().splitlines() != selected:
            raise ValueError('Firth conditioning markers differ from selection history.')
        if entry != scan_entry(work, settings, samples, options, variants, columns, iteration, selected):
            raise ValueError('Firth selection differs from checked raw results.')
        if entry['accepted']:
            selected.append(entry['best_marker'])
        reason = stop_reason(entry, selected, len(variants), options)
    if reason is None:
        raise ValueError('Firth stepwise stopped without a recorded stopping reason.')
    result = summary(samples, settings, options, history, selected, reason)
    if ((work / 'output/stepwise.selected-markers.txt').read_text() != ''.join(n + '\n' for n in selected)
            or (work / 'output/stepwise.selection.tsv').read_text() != selection_text(history)
            or json.loads((work / 'output/stepwise.summary.json').read_text()) != result):
        raise ValueError('Firth summary differs from checked scan history.')
    return result
