"""Host-only display summaries; never change plans, records or analysis values."""

from pathlib import Path
import re

from hlagent.paths import ROOT


TITLES = {
    'hatk_imgt2seq': 'HATK dictionary preparation',
    'hatk_stepwise': 'Stepwise conditional association (HATK Node3)',
    'cookhla': 'HLA imputation', 'cookhla_to_hped': 'HPED conversion',
    'phenotype_preparation': 'Phenotype preparation', 'summary_preparation': 'Study summary preparation',
    'hatk_node1': 'HLA marker generation (HATK Node1)', 'hatk_node2': 'Marker association (HATK Node2)',
    'hatk_node3': 'Conditional association (HATK Node3)', 'hatk_node4': 'Amino-acid omnibus test (HATK Node4)',
    'hatk_node5': 'Meta-analysis (HATK Node5)', 'hatk_phasing': 'Genotype phasing',
}

METRICS = {
    'hatk_imgt2seq': ('imgt', 'genome_build', 'field_format', 'dictionary_files'),
    'hatk_stepwise': ('samples', 'iterations', 'selected_marker_count', 'selection_status', 'stop_reason', 'unresolved_marker_count', 'final_unestimable_markers'),
    'cookhla': ('input_samples', 'output_samples', 'result_rows'),
    'cookhla_to_hped': ('samples', 'predicted_genes', 'absent_genes_filled_with_zero'),
    'phenotype_preparation': ('samples', 'controls', 'cases', 'clinical_rows_outside_fam'),
    'summary_preparation': ('markers', 'estimable_by_input_checks', 'unestimable_markers'),
    'hatk_node1': ('input_samples', 'output_samples', 'removed_samples', 'markers'),
    'hatk_node2': ('samples', 'controls', 'cases', 'tested_markers', 'estimable_markers', 'unestimable_markers', 'minimum_p'),
    'hatk_node3': ('samples', 'controls', 'cases', 'tested_markers', 'estimable_markers', 'unestimable_markers', 'minimum_p'),
    'hatk_node4': ('samples', 'controls', 'cases', 'aa_positions', 'insertion_tests', 'total_tests', 'positions', 'estimable_positions', 'unestimable_positions', 'minimum_log10_p'),
    'hatk_node5': ('samples', 'study_count', 'meta_markers', 'excluded_markers', 'minimum_p'),
    'hatk_phasing': ('samples', 'markers', 'aa_markers', 'imputed_missing_genotypes'),
}

LIMITS = {
    'hatk_imgt2seq': 'Dictionary checks assess format and consistency, not biological accuracy.',
    'hatk_stepwise': 'Selection covers estimable tests only. Selected markers do not establish causal independence.',
    'cookhla': 'File checks do not measure imputation accuracy.',
    'cookhla_to_hped': 'Unphased calls. No confidence filter. Missing genes remain missing.',
    'phenotype_preparation': 'Sample matching does not verify clinical labels.',
    'summary_preparation': 'Format checks do not verify statistical validity or study independence.',
    'hatk_node1': 'Marker/QC checks do not assess biological accuracy.',
    'hatk_node2': 'OR refers to A1. Review multiple testing. Unestimable tests are not negative findings.',
    'hatk_node3': 'Residual association does not establish causal independence. Unestimable tests are not negative findings.',
    'hatk_node4': 'Each person contributes two haplotype rows. The model does not account for their dependence. N counts haplotype rows, not people. The table uses log10(P).',
    'hatk_node5': 'Fixed-effect meta-analysis. No sample-overlap correction.',
    'hatk_phasing': 'Observed genotypes were checked for preservation. Phase accuracy was not assessed.',
}


def base_stage(stage):
    return stage.removesuffix('_custom')


def stage_title(stage):
    return TITLES.get(base_stage(stage), stage.replace('_', ' '))


def key_metrics(stage, summary):
    return {key: summary[key] for key in METRICS.get(base_stage(stage), ()) if key in summary
            and not (key == 'positions' and 'total_tests' in summary)}


def display_value(value):
    if isinstance(value, float):
        return f'{value:.6g}'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (list, tuple)):
        return ', '.join(map(str, value)) or 'None'
    return str(value)


def short_paths(text):
    """Keep project paths usable from the workspace; external paths stay complete."""
    if Path.cwd().resolve() != ROOT:
        return text
    return text.replace(str(ROOT) + '/', '')


def concise_guidance(text):
    redundant = ('Inputs are collected locally.', 'Inputs stay in local prompts.',
                 'No parent run is required for direct input.')
    return short_paths('\n'.join(line for line in text.splitlines()
                                 if line.strip() not in redundant))


def align_labels(text):
    """Align short display labels within each paragraph without changing values."""
    blocks = []
    pattern = re.compile(r'^( *)([A-Z][A-Za-z0-9 /()_-]{0,34}?)\s*:\s+(\S.*)$')
    for block in text.split('\n\n'):
        lines = block.split('\n')
        matches = [pattern.match(line) for line in lines]
        labels = [match for match in matches if match]
        if len(labels) > 1:
            width = max(len(match[1]) + len(match[2].rstrip()) for match in labels)
            lines = [(match[1] + match[2].rstrip()).ljust(width) + ' : ' + match[3]
                     if match else line for line, match in zip(lines, matches)]
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)


def compact_input(text):
    """Keep what to supply visible; retain format internals in input details."""
    hidden = ('Format: six pedigree fields,', 'Gene order:',
              'Format: an I id paired-IID header,', 'Marker IDs must encode ', 'Insertion IDs use ',
              'Reusing a run keeps its checked panel and recorded build.')
    lines = [line for line in text.splitlines() if not line.startswith(hidden)]
    text = '\n'.join(lines)
    text = text.replace('Input: enter a headerless 22-column HPED path.', 'Input: enter an HPED file path.')
    text = text.replace('No new phasing is performed. Inputs are collected locally.', 'No new phasing is performed.')
    return concise_guidance(text)


def compact_plan(text):
    hidden = ('Image:', 'Command:', 'Reviewed proposal method:',
              'G/C coding and unique phasing coordinates are temporary',
              'These settings do not cap total container memory or CPU use.',
              'Gene order:', 'Exclusions:',
              'Node1-4 are not rerun. Input files are copied into a new run directory.',
              'Node1 and Node2 are not rerun. Input files are copied into a new run directory.',
              'Node1 is not rerun. Input files are copied into a new run directory.',
              'ESTIMABLE=True means finite positive OR/SE and NMISS>0,')
    stepwise = text.startswith('Stepwise conditional association (HATK Node3)')
    bonferroni = 'Threshold rule: Bonferroni' in text
    lines = []
    for line in text.splitlines():
        if line.startswith(hidden):
            continue
        if stepwise:
            if line.startswith(('Threshold rule:', 'Each scan adjusts', 'Low-MAC and PLINK',
                                'Uses PLINK Firth', 'Exact structural exclusions',
                                'Selected markers do not establish')):
                continue
            if line.startswith('Model:'):
                line = 'Method: Stepwise conditional association'
            elif line.startswith('Candidates:'):
                counts = re.match(r'Candidates: (\d+) of (\d+)', line)
                if counts:
                    line = f'Candidates: {int(counts[1]):,} of {int(counts[2]):,}'
            elif line.startswith(('Minimum MAC:', 'Maximum scans:')):
                line = line.split('. ', 1)[0]
            elif line.startswith('Selection: add the lowest-P estimable marker when P < '):
                cutoff = float(line.rsplit(' ', 1)[1])
                line = f'P threshold: {cutoff:.3g}' + (' (Bonferroni)' if bonferroni else ' (user supplied)')
        if line.startswith('The marker/BGL rows are sorted'):
            line = 'Phase accuracy is not assessed.'
        if line.startswith('Missing dictionary sequences use the original HATK missing-genotype behavior.'):
            line = 'Missing dictionary sequences remain missing. Coverage details are recorded.'
        if line == 'Steps: NomenCleaner, dictionary coverage report, bMarkerGenerator. No IMGT2Seq or association.':
            line = 'Steps: create HLA, amino-acid and DNA markers. Association is not run.'
        if line == 'Steps: NomenCleaner, then bMarkerGenerator (HLA, AA, intragenic and supplied SNP markers)':
            line = 'Steps: create HLA, amino-acid, DNA and supplied SNP markers.'
        if line == 'Steps: decode residues, original HATK R omnibus tests, Manhattan PNG/PDF':
            line = 'Steps: amino-acid position omnibus tests and Manhattan PNG/PDF'
        lines.append(line)
    return concise_guidance('\n'.join(lines))


def result_view(result):
    record = result if isinstance(result, dict) else result.to_dict()
    plan = record.get('plan', {})
    stage = plan.get('stage') or plan.get('example') or 'cookhla'
    summary = record.get('summary') or {}
    run = record.get('run_dir') or plan.get('run_dir')
    lines = [stage_title(stage), 'Status: ' + record.get('status', 'unknown'), 'Saved result checks passed.']
    if stage == 'hatk_stepwise':
        if summary.get('analysis_version', 1) == 1:
            lines.append('Selection status: unverified legacy result. Unclassified tests were skipped.')
        elif summary.get('selection_status') == 'incomplete':
            lines.append('Selection is incomplete. Do not interpret this as no remaining significant markers.')
    covariates = plan.get('covariates') or summary.get('covariates')
    if covariates:
        lines.append('Covariates: ' + ', '.join(covariates))
    conditions = plan.get('data', {}).get('conditions') or summary.get('conditions')
    if conditions:
        lines.append('Condition markers: ' + ', '.join(conditions))
    if base_stage(stage) == 'hatk_phasing':
        lines.append('BEAGLE 3.3.2. Missing genotypes were inferred.')
    lines.append('Limits: ' + LIMITS.get(base_stage(stage), 'File checks do not establish biological accuracy.'))
    if stage == 'hatk_node5':
        lines.append('RA example subsets are not independent replication cohorts.')
    elif stage == 'hatk_node5_custom':
        lines.append('Study independence and compatible trait/build/coding are user declarations.')
    if run:
        lines.extend(('', 'Results: ' + str(Path(run) / 'output')))
    return short_paths('\n'.join(lines)), key_metrics(stage, summary)
