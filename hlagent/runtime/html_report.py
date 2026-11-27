"""Produce an offline report from explicit saved runs without fitting or LLM calls."""

from hlagent.paths import new_run_id

from datetime import datetime, timezone
import base64
from html import escape
import json
import os
from pathlib import Path
import shutil
from urllib.parse import quote

from hlagent.analysis import hatk, cookhla, cookhla_to_hped
from hlagent.analysis.hatk_association import validate_association_outputs
from hlagent.analysis.hatk_custom_association import validate_custom_association_outputs
from hlagent.analysis.hatk_conditional import validate_conditional_outputs
from hlagent.analysis.hatk_stepwise import validate_stepwise_outputs
from hlagent.analysis.hatk_custom_conditional import validate_custom_conditional_outputs
from hlagent.analysis.hatk_omnibus import validate_omnibus_outputs
from hlagent.analysis.hatk_custom_omnibus import validate_custom_omnibus_outputs
from hlagent.analysis.hatk_meta import validate_meta_outputs
from hlagent.analysis.hatk_custom_meta import validate_custom_meta_outputs
from hlagent.analysis.hatk_custom_markers import validate_custom_marker_outputs
from hlagent.analysis.hatk_phasing import validate_phasing_outputs
from hlagent.analysis.imgt_dictionary import validate_dictionary_outputs
from hlagent.runtime import phenotype_preparation, summary_preparation
from hlagent.runtime.workflow_runs import checked_files, local_artifact, read_run
from hlagent.runtime.presentation import display_value, key_metrics, stage_title
from hlagent.runtime.presentation import LIMITS as SUMMARY_LIMITS


VALIDATORS = {
    'hatk_imgt2seq': validate_dictionary_outputs,
    'hatk_stepwise': validate_stepwise_outputs,
    'cookhla': cookhla.check_run_results, 'cookhla_to_hped': cookhla_to_hped.validate_conversion,
    'phenotype_preparation': phenotype_preparation.validate, 'summary_preparation': summary_preparation.validate,
    'hatk_node1': hatk.validate_results, 'hatk_node1_custom': validate_custom_marker_outputs,
    'hatk_node2': validate_association_outputs, 'hatk_node2_custom': validate_custom_association_outputs,
    'hatk_node3': validate_conditional_outputs, 'hatk_node3_custom': validate_custom_conditional_outputs,
    'hatk_node4': validate_omnibus_outputs, 'hatk_node4_custom': validate_custom_omnibus_outputs,
    'hatk_node5': validate_meta_outputs, 'hatk_node5_custom': validate_custom_meta_outputs,
    'hatk_phasing': validate_phasing_outputs,
}

LIMITS = {
    'cookhla': 'Allele-call structure checks do not measure imputation accuracy. Reference ancestry and genome-build suitability require study-specific assessment.',
    'cookhla_to_hped': 'Unphased two-field calls; no confidence filter. Missing genes stay missing and are not newly imputed.',
    'phenotype_preparation': 'Explicit clinical mappings and sample matching do not validate clinical labels. Disease status is not generated from genotypes.',
    'summary_preparation': 'Format and effect-scale checks do not establish convergence, compatible clinical coding or independent studies.',
    'hatk_node1': 'Marker/QC checks do not assess biological accuracy. Dictionary build/version suitability is caller supplied.',
    'hatk_node2': 'Binary additive logistic regression. Adjustment columns, if selected, are listed in the analysis settings. OR refers to A1. P is not an effect size. Review multiple testing; unestimable tests are not negative findings.',
    'hatk_stepwise': 'Forward selection on estimable marker tests only. The threshold is not a multiple-testing correction. Selected markers do not establish causal independence. Review stop reason and unestimable tests.',
    'hatk_node3': 'One explicit conditional scan, not automatic stepwise selection. Residual association does not establish causal independence. Review the matched baseline and unestimable tests.',
    'hatk_node4': 'Original HATK haplotype-row model: each person contributes two rows with the same case/control label. The model does not account for the two rows coming from one person. N counts haplotype rows, not independent people. This is not validated individual-level inference. log10_P is log10(P), not -log10(P); N counts haplotype rows. A position association does not identify a causal residue.',
    'hatk_node5': 'Two-study fixed-effect inverse-variance weighting of log odds. No random effects or overlap correction. Trait, build, case coding and study nonoverlap require source evidence. Split subsets of the RA example are NOT independent replication cohorts.',
    'hatk_phasing': 'BEAGLE 3.3.2 phases supplied markers and infers missing genotypes. Observed-genotype preservation is not a phase-accuracy assessment.',
}

CSS = '''
:root{color-scheme:light;--ink:#202020;--muted:#606060;--accent:#245780;--line:#d0d0d0}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);font:15px/1.65 Arial,sans-serif}
main{max-width:980px;margin:48px auto;padding:0 32px 56px}
header{padding:0 0 22px;border-bottom:2px solid #333;margin-bottom:24px}
h1{font-size:28px;font-weight:600;line-height:1.25;margin:8px 0 12px}
h2{font-size:21px;font-weight:600;line-height:1.4;margin:0 0 14px}
h3{font-size:15px;font-weight:600;margin:25px 0 10px}
p{margin:10px 0}a{color:var(--accent);text-decoration:underline;text-underline-offset:2px;overflow-wrap:anywhere}
nav{display:flex;flex-wrap:wrap;gap:6px 20px;margin:20px 0 30px;font-size:13px}
nav a{color:#454545}
section{padding:30px 0 36px;margin:0;border:0;border-bottom:1px solid var(--line);background:transparent;border-radius:0}
.muted{color:var(--muted);font-size:13px}
.status{display:inline;color:var(--muted);font-size:13px;padding:0;margin:0;border:0;border-radius:0}
.status+.status:before{content:' / ';white-space:pre}
.notice{padding:0;margin:14px 0;background:transparent;border:0;font-size:inherit}
.verified,.bad{background:transparent;border:0}
.bad{font-weight:600}
table{width:100%;border-collapse:collapse;table-layout:fixed;margin:8px 0 18px;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:9px 10px;border:0;border-bottom:1px solid #e0e0e0;overflow-wrap:anywhere}
th{width:34%;font-weight:600;background:transparent}
thead{border-top:1px solid #999;border-bottom:1px solid #999}
thead th,thead td{font-weight:600;border-bottom:0}
pre{white-space:pre-wrap;margin:0;font:12px/1.6 monospace;overflow-wrap:anywhere}
.plots{display:block}
img{display:block;width:100%;height:auto}
figure{padding:0;margin:18px 0 24px;border:0;border-radius:0}
figcaption{margin:8px 0;font-size:12px;color:var(--muted)}
details{margin:16px 0}summary{cursor:pointer;font-size:14px;color:#454545}
ul{padding-left:20px;font-size:14px}li{margin:4px 0}
footer{margin-top:26px;padding-top:0;font-size:12px}
@media(max-width:600px){main{margin:24px auto;padding:0 18px 36px}h1{font-size:25px}h2{font-size:19px}th{width:40%}}
'''


def table(values):
    def value_text(value):
        if isinstance(value, (dict, list, tuple)):
            return '<pre>' + escape(json.dumps(value, indent=2, ensure_ascii=False)) + '</pre>'
        return escape(str(value))
    return '<table>' + ''.join('<tr><th>' + escape(str(key).replace('_', ' ')) + '</th><td>' +
                              value_text(value) + '</td></tr>' for key, value in values.items()) + '</table>'


def summary_table(values):
    return table({key: display_value(value) for key, value in values.items()})


def conditional_comparison(summary):
    baseline = summary.get('baseline_counts')
    if not isinstance(baseline, dict):
        return ''
    rows = []
    for key in ('tested_markers', 'estimable_markers', 'unestimable_markers', 'minimum_p'):
        if key in baseline and key in summary:
            rows.append('<tr><th>' + escape(key.replace('_', ' ')) + '</th><td>' +
                        escape(display_value(baseline[key])) + '</td><td>' +
                        escape(display_value(summary[key])) + '</td></tr>')
    return ('<h3>Before and after conditioning</h3><table><thead><tr><th>Metric</th>'
            '<td>Baseline</td><td>Conditional</td></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>')


def inspect_run(run):
    run, record = read_run(run)
    plan = record['plan']
    stage = plan.get('stage') or plan.get('example') or ('cookhla' if plan.get('inputs') else 'unknown')
    result = {'run': run, 'record': record, 'stage': stage, 'checked': False, 'summary': {}, 'issues': [],
              'record_sha256': hatk.digest(run / 'run.json'), 'files': []}
    if record.get('status') not in ('completed', 'native_validation') and not (stage == 'hatk_stepwise' and record.get('status') == 'incomplete'):
        result['issues'].append('This task is not completed. Partial files are not presented as completed analysis results.')
        return result
    try:
        if stage not in VALIDATORS:
            raise ValueError('Unsupported saved stage; no result validation was performed.')
        if stage == 'hatk_node3' and plan.get('conditional_mode') != 'single':
            raise ValueError('Archived automatic-stepwise output is outside the current single-scan Node3 report scope.')
        inputs = plan.get('data', {}).get('files', plan.get('files', plan.get('sources', {}).get('files', [])))
        checked_files(run, inputs)
        checked_files(run, record.get('derived_inputs', []))
        outputs = record.get('outputs', [])
        if record.get('status') in ('completed', 'incomplete') and not outputs:
            raise ValueError('Completed run has no output hash manifest.')
        checked_files(run, outputs)
        result['summary'] = VALIDATORS[stage](run)
        if stage == 'cookhla' and not result['summary'].get('valid'):
            raise ValueError('CookHLA result checks did not pass.')
        result['checked'] = True
        if record.get('status') == 'native_validation':
            result['issues'].append('Preserved native-validation output, not a newly completed Docker run. Output hashes may be unavailable.')
        # Only summary tables and figures are packaged. Sample-level genotype/phenotype files remain at source.
        recorded_outputs = {f['path'] for f in outputs}
        tables = {'association.results.tsv', 'RA.results.tsv', 'conditional.comparison.tsv',
                  'RA.comparison.tsv', 'stepwise.selection.tsv', 'stepwise.selected-markers.txt', 'omnibus', 'meta.tsv', 'RA.meta.tsv'}
        for path in sorted((run / 'output').iterdir()):
            if not path.is_file() or not (path.suffix.lower() in ('.png', '.pdf') or path.name in tables):
                continue
            path = local_artifact(run, str(path.relative_to(run)))
            if record.get('status') in ('completed', 'incomplete') and str(path.relative_to(run)) not in recorded_outputs:
                continue
            result['files'].append({'path': str(path.relative_to(run)), 'sha256': hatk.digest(path)})
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['checked'] = False
        result['summary'] = {}
        result['files'] = []
        result['issues'].append('Saved-result checks failed: ' + str(error))
    return result


def generate_report(runs, output):
    """Create a new private directory; preserve every selected source run."""
    runs = list(dict.fromkeys(str(Path(p).expanduser().resolve()) for p in runs))
    if not runs:
        raise ValueError('Select at least one run.')
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError('Choose a new report directory; existing reports are not overwritten.')
    if any(output.is_relative_to(Path(run)) or Path(run).is_relative_to(output) for run in runs):
        raise ValueError('The report directory must be separate from the selected source runs.')
    inspected = [inspect_run(run) for run in runs]
    output.mkdir(parents=True, mode=0o700)
    sections, manifest = [], []
    try:
        for index, item in enumerate(inspected, 1):
            run, record, stage = item['run'], item['record'], item['stage']
            plan, data = record['plan'], record['plan'].get('data', {})
            section = [f'<section id="run-{index}"><h2>{index}. {escape(stage_title(stage))}</h2>',
                       '<p class="muted">' + escape(run.name) + '</p>',
                       '<span class="status">' + escape(record.get('status', 'unknown')) + '</span>',
                       '<span class="status">' + ('Saved files checked' if item['checked'] else 'Result checks incomplete') + '</span>']
            for issue in item['issues']:
                section.append('<p class="notice' + ('' if item['checked'] else ' bad') + '">' + escape(issue) + '</p>')
            settings = {
                key: value for key, value in {
                    'genome_build_declared': plan.get('genome_build'), 'phenotype': plan.get('phenotype'),
                    'settings': data.get('settings', plan.get('settings')), 'conditions': data.get('conditions'),
                    'covariates': plan.get('covariates'), 'qc': plan.get('qc'), 'engine': plan.get('engine'),
                    'image': plan.get('image'), 'model': plan.get('model'),
                    'regression_engine': plan.get('regression_engine'), 'selection': plan.get('selection'),
                    'memory': plan.get('memory'), 'processes': plan.get('processes'),
                }.items() if value is not None}
            nested_settings = data.get('settings', plan.get('settings')) or {}
            essential = {
                'phenotype': plan.get('phenotype') or nested_settings.get('phenotype_name'),
                'genome_build_declared': plan.get('genome_build') or nested_settings.get('genome_build'),
                'conditions': data.get('conditions') or item['summary'].get('conditions'),
            }
            essential = {key: value for key, value in essential.items() if value is not None and value != []}
            if essential:
                section.append('<h3>Analysis</h3>' + summary_table(essential))
            limit = LIMITS.get(stage.replace('_custom', ''), 'Saved file checks do not establish biological or statistical validity.')
            short_limit = SUMMARY_LIMITS.get(stage.replace('_custom', ''), limit)
            if stage == 'hatk_stepwise' and plan.get('stepwise_analysis_version') == 3:
                short_limit = ('Always-Firth with PLINK working-information Wald statistics. Selection covers MAC-eligible tests after PLINK screening. '
                               'The recorded threshold does not establish full post-selection error control or causal independence.')
            if stage == 'hatk_stepwise' and item['summary'].get('selection_status') in ('incomplete', 'unverified_legacy'):
                short_limit = 'Selection is incomplete or unverified. Do not interpret this as no remaining significant markers. ' + short_limit
            if stage == 'hatk_node5':
                short_limit += ' RA example subsets are not independent replication cohorts.'
            elif stage == 'hatk_node5_custom':
                short_limit += ' Trait, build, clinical coding and study independence are user declarations.'
            section.append('<p class="notice">' + escape(short_limit) + '</p>')
            if item['checked']:
                section.append('<h3>Checked result summary</h3>' + summary_table(key_metrics(stage, item['summary'])))
                section.append(conditional_comparison(item['summary']))
            links, plots, heatmaps = [], [], []
            for artifact in item['files']:
                source = local_artifact(run, artifact['path'])
                relative = Path('assets') / str(index) / source.name
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source, destination)
                os.chmod(destination, 0o600)
                if hatk.digest(destination) != artifact['sha256'] or hatk.digest(source) != artifact['sha256']:
                    raise ValueError('An artifact changed during report generation.')
                href = quote(relative.as_posix())
                if source.suffix.lower() != '.png':
                    name = 'omnibus.results.tsv' if source.name == 'omnibus' else source.name
                    links.append(f'<li><a href="{href}" download="{escape(name, quote=True)}">{escape(name)}</a></li>')
                if source.suffix.lower() == '.png':
                    embedded = 'data:image/png;base64,' + base64.b64encode(destination.read_bytes()).decode('ascii')
                    target = heatmaps if 'heatmap' in source.name.lower() else plots
                    target.append(f'<figure><img src="{embedded}" alt="{escape(source.name, quote=True)}" loading="lazy">'
                                  f'<figcaption>{escape(source.stem)}</figcaption></figure>')
            if plots:
                section.append('<h3>Saved figures</h3><div class="plots">' + ''.join(plots) + '</div>')
            if heatmaps:
                section.append('<details><summary>Gene heatmaps (' + str(len(heatmaps)) + ')</summary>'
                               '<div class="plots">' + ''.join(heatmaps) + '</div></details>')
            if links:
                section.append('<h3>Result files</h3><ul>' + ''.join(links) + '</ul>')
            files = data.get('files', plan.get('files', plan.get('sources', {}).get('files', [])))
            provenance = {'run_directory': str(run), 'record_sha256': item['record_sha256'],
                          'started_at': record.get('started_at'), 'finished_at': record.get('finished_at'),
                          'input_files': [{key: f[key] for key in ('path', 'source', 'source_path', 'sha256') if key in f} for f in files],
                          'source_sha256': plan.get('source_sha256'), 'environment': record.get('environment')}
            section.append('<details><summary>Analysis details</summary><h3>Settings</h3>' + table(settings) +
                           '<h3>Full checked metrics</h3>' + table(item['summary']) +
                           '<h3>Interpretation</h3><p>' + escape(limit) + '</p></details>')
            section.append('<details><summary>Inputs and provenance</summary>'
                           '<p>Source files, software environment and file hashes for reproducibility.</p>' + table(provenance) +
                           '<p><a href="' + escape((run / 'run.json').as_uri(), quote=True) + '">Original run record (source machine)</a></p></details></section>')
            sections.append(''.join(section))
            manifest.append({'run_directory': str(run), 'record_sha256': item['record_sha256'], 'stage': stage,
                             'status': record.get('status'), 'checked': item['checked'], 'issues': item['issues'],
                             'artifacts': item['files']})
            if hatk.digest(run / 'run.json') != item['record_sha256']:
                raise ValueError('A run record changed during report generation.')
        created = datetime.now(timezone.utc).isoformat()
        displayed_created = datetime.fromisoformat(created).strftime('%Y-%m-%d %H:%M UTC')
        nav = '<nav>' + ''.join(f'<a href="#run-{i}">{i}. {escape(stage_title(item["stage"]))}</a>' for i, item in enumerate(inspected, 1)) + '</nav>'
        overview = '<section><h2>Selected runs</h2><table><thead><tr><th>Analysis</th><td>Checks</td><td>Summary</td></tr></thead><tbody>'
        for i, item in enumerate(inspected, 1):
            metrics = key_metrics(item['stage'], item['summary'])
            brief = '<br>'.join(escape(key.replace('_', ' ') + ': ' + display_value(value))
                              for key, value in metrics.items() if key in (
                                  'samples', 'output_samples', 'markers', 'tested_markers', 'aa_positions',
                                  'insertion_tests', 'total_tests', 'positions', 'meta_markers'))
            overview += (f'<tr><th><a href="#run-{i}">' + escape(stage_title(item['stage'])) + '</a></th><td>' +
                         ('Passed' if item['checked'] else 'Incomplete') + '</td><td>' + (brief or 'See details') + '</td></tr>')
        overview += '</tbody></table></section>'
        html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">'
                '<title>HLAgent analysis report</title><style>' + CSS + '</style></head><body><main><header>'
                '<p class="muted">HLAgent</p><h1>Analysis report</h1>'
                '<p class="muted">Generated: ' + escape(displayed_created) + '</p></header>'
                '<p class="notice verified">This report reads saved files only. No analysis tool or LLM was called. '
                'File and structure checks do not establish biological accuracy or statistical calibration.</p>'
                '<p>These runs were selected explicitly. Their order does not establish a shared cohort, compatible traits, '
                'a verified end-to-end pipeline, or independent replication.</p>' + nav + overview + ''.join(sections) +
                '<footer class="muted">PNG figures are embedded for offline viewing. PNG, PDF and table downloads are copied to the assets folder. Source run-record links work only on the source machine. '
                'Reports can include local paths and study results. Review their contents before sharing.</footer></main></body></html>')
        for filename, text in [('index.html', html), ('report.json', json.dumps({'version': 1, 'created_at': created,
                                                                              'runs': manifest}, indent=2) + '\n')]:
            path = output / filename
            path.write_text(text, encoding='utf-8')
            os.chmod(path, 0o600)
    except BaseException:
        shutil.rmtree(output)
        raise
    return output / 'index.html'


def report_directory(root):
    return Path(root) / 'reports' / new_run_id('report', Path(root) / 'reports')
