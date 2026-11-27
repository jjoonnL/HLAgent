"""Normalize explicitly mapped binary-trait association summaries for Node5."""

from hlagent.paths import new_run_id

from collections import Counter
import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import shutil

from hlagent.analysis.hatk_custom_association import check_settings
from hlagent.analysis.hatk_custom_meta import INPUT_FIELDS, study_table
from hlagent.runtime.phenotype_preparation import digest, read_table

MAPPED_FIELDS = ('CHR', 'SNP', 'BP', 'A1', 'A2', 'NMISS', 'EFFECT', 'SE')
OUTPUT_FIELDS = INPUT_FIELDS + ('SOURCE_EFFECT', 'PREPARATION_EXCLUSION')


def normalized_text(source, settings):
    if set(settings) != {'delimiter', 'columns', 'effect_scale', 'se_scale', 'phenotype_name', 'genome_build'}:
        raise ValueError('Supply delimiter, columns, effect/SE scales, binary trait name and genome build.')
    check_settings(settings['phenotype_name'], settings['genome_build'])
    if settings['effect_scale'] not in ('or', 'log_or') or settings['se_scale'] != 'log_or':
        raise ValueError('Only binary-trait OR or log(OR) effects with SE of log(OR) are supported.')
    header, rows = read_table(source, settings['delimiter'])
    mapping = settings['columns']
    if (not isinstance(mapping, dict) or set(mapping) != set(MAPPED_FIELDS) | {'ESTIMABLE'}
            or any(mapping[key] not in header for key in MAPPED_FIELDS)
            or (mapping['ESTIMABLE'] is not None and mapping['ESTIMABLE'] not in header)):
        raise ValueError('Map all eight required columns. ESTIMABLE must be a column name or null.')
    chosen = [name for name in mapping.values() if name is not None]
    if len(set(chosen)) != len(chosen):
        raise ValueError('Each mapped role must use a different source column.')
    stream = io.StringIO()
    writer = csv.DictWriter(stream, OUTPUT_FIELDS, delimiter='\t', lineterminator='\n')
    writer.writeheader()
    seen, reasons, usable = set(), Counter(), 0
    for number, raw in enumerate(rows, 2):
        source_row = dict(zip(header, raw))
        row = {key: source_row[mapping[key]] for key in MAPPED_FIELDS}
        if (row['CHR'] not in ('6', 'chr6') or not row['SNP'] or row['SNP'] in seen
                or any(c.isspace() for c in row['SNP'])):
            raise ValueError(f'Row {number}: require unique chromosome 6 marker IDs; no rows are dropped automatically.')
        seen.add(row['SNP'])
        try:
            bp, n = int(row['BP']), int(row['NMISS'])
        except ValueError:
            raise ValueError(f'Row {number}: BP and NMISS must be integers.') from None
        if (bp <= 0 or n < 0 or row['A1'] == row['A2']
                or any(not row[key] or row[key] in ('0', 'NA') or any(c.isspace() for c in row[key])
                       for key in ('A1', 'A2'))):
            raise ValueError(f'Row {number}: require positive BP, nonnegative NMISS and distinct explicit allele labels.')
        flag = source_row[mapping['ESTIMABLE']] if mapping['ESTIMABLE'] is not None else None
        if flag not in (None, 'True', 'False'):
            raise ValueError(f'Row {number}: ESTIMABLE must use literal True/False; recode explicitly before preparation.')
        problems = []
        numbers = {}
        for key in ('EFFECT', 'SE'):
            value = row[key]
            if value in ('', 'NA', 'N/A', '.'):
                numbers[key] = math.nan
            else:
                try:
                    numbers[key] = float(value)
                except ValueError:
                    raise ValueError(f'Row {number}: {key} must be numeric or an explicit missing token (empty, NA, N/A, .).') from None
        effect = numbers['EFFECT']
        output_effect = row['EFFECT']
        if settings['effect_scale'] == 'log_or':
            try:
                effect = math.exp(effect) if math.isfinite(effect) else math.nan
            except OverflowError:
                effect = math.nan
            output_effect = format(effect, '.17g') if math.isfinite(effect) and effect > 0 else 'NA'
        if not math.isfinite(effect) or effect <= 0:
            problems.append('invalid_or_missing_effect')
        if not math.isfinite(numbers['SE']) or numbers['SE'] <= 0:
            problems.append('invalid_or_missing_log_or_se')
        if n == 0:
            problems.append('zero_nmiss')
        if flag == 'False':
            problems.append('source_unestimable')
        usable += not problems
        reasons.update(problems)
        writer.writerow({**{key: row[key] for key in ('CHR', 'SNP', 'BP', 'A1', 'A2', 'NMISS', 'SE')},
                         'OR': output_effect, 'ESTIMABLE': str(not problems),
                         'SOURCE_EFFECT': row['EFFECT'], 'PREPARATION_EXCLUSION': ';'.join(problems)})
    summary = {'markers': len(rows), 'estimable_by_input_checks': usable,
               'unestimable_markers': len(rows) - usable, 'exclusion_counts': dict(reasons),
               'effect_scale': settings['effect_scale'], 'se_scale': 'log_or',
               'source_estimable_column': mapping['ESTIMABLE'], 'rows_dropped': 0}
    return stream.getvalue(), summary


def prepare(source, settings, run_root):
    source = Path(source).expanduser().resolve()
    _, summary = normalized_text(source, settings)
    return {'stage': 'summary_preparation', 'input': str(source),
            'run_dir': str(Path(run_root).resolve() / new_run_id('summary', run_root)),
            'settings': json.loads(json.dumps(settings)), 'summary': summary,
            'files': [{'source': str(source), 'path': 'input/summary.table', 'sha256': digest(source)}],
            'source_sha256': digest(__file__)}


def validate(run):
    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    plan = record['plan']
    if (plan.get('stage') != 'summary_preparation' or len(plan['files']) != 1
            or plan['files'][0]['path'] != 'input/summary.table'):
        raise ValueError('Select a summary preparation run with its original input record.')
    if digest(run / 'input/summary.table') != plan['files'][0]['sha256']:
        raise ValueError('Recorded summary input changed.')
    text, summary = normalized_text(run / 'input/summary.table', plan['settings'])
    output = run / 'output/study.tsv'
    if output.read_text() != text or summary != plan['summary']:
        raise ValueError('Prepared summary differs from its explicit mapping or summary.')
    study_table(output)
    return summary


def prepared_summary(run):
    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    if record.get('status') != 'completed':
        raise ValueError('Select a completed summary preparation run.')
    validate(run)
    output = run / 'output/study.tsv'
    if record['outputs'] != [{'path': 'output/study.tsv', 'sha256': digest(output)}]:
        raise ValueError('Prepared summary output differs from its recorded hash.')
    return output, record['plan']['settings']


def execute(plan):
    fresh = prepare(plan['files'][0]['source'], plan['settings'], Path(plan['run_dir']).parent)
    if any(fresh[key] != plan[key] for key in ('settings', 'summary', 'files', 'source_sha256')):
        raise ValueError('Summary input, mappings or source changed after review. Prepare a new plan.')
    run = Path(plan['run_dir'])
    run.mkdir(parents=True)
    record = {'status': 'running', 'plan': plan, 'started_at': datetime.now(timezone.utc).isoformat()}
    record_path = run / 'run.json'
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    try:
        (run / 'input').mkdir()
        (run / 'output').mkdir()
        shutil.copy2(plan['files'][0]['source'], run / 'input/summary.table')
        if digest(run / 'input/summary.table') != plan['files'][0]['sha256']:
            raise ValueError('Summary input changed while copying.')
        text, _ = normalized_text(run / 'input/summary.table', plan['settings'])
        (run / 'output/study.tsv').write_text(text)
        record['summary'] = validate(run)
        record['outputs'] = [{'path': 'output/study.tsv', 'sha256': digest(run / 'output/study.tsv')}]
        record['status'] = 'completed'
    except KeyboardInterrupt:
        record['status'] = 'cancelled'
    except (OSError, ValueError) as error:
        record.update(status='failed', error=str(error))
    finally:
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        record_path.write_text(json.dumps(record, indent=2) + '\n')
    return record


def show_plan(plan):
    print('Association summary to Node5 TSV: explicit column mapping. Original source is preserved.')
    print('Input: ' + plan['input'])
    if plan.get('mapping_proposals'):
        print('Reviewed proposal method: ' + plan['mapping_proposals']['method'])
    print('Mappings and declared metadata:')
    print(json.dumps(plan['settings'], indent=2))
    print('Preparation checks:')
    print(json.dumps(plan['summary'], indent=2))
    print('A1 is the supplied effect allele. OR = exp(log(OR)) when selected. SE must already be on the log(OR) scale.')
    print('All rows are retained. Invalid/missing statistics and source False flags remain unestimable with recorded reasons.')
    print('ESTIMABLE=True means finite positive OR/SE and NMISS>0, plus any supplied flag. It does not establish model convergence.')
    print('Binary trait, case coding, build, allele meaning and study independence require source evidence. Preparation does not verify them.')
    print('Results will be saved in: ' + plan['run_dir'])


def show_result(record):
    print('Status: ' + record['status'])
    run = Path(record['plan']['run_dir'])
    if record['status'] == 'completed':
        print('Prepared summary: ' + str(run / 'output/study.tsv'))
        print('Format and mapping replay checked. Statistical validity is not established.')
        print('In node5-custom, enter run at either study prompt to select a prepared summary. Two independent compatible studies are required.')
    if record.get('error'):
        print(record['error'])
    print('Run record: ' + str(run / 'run.json'))
    return 0 if record['status'] == 'completed' else 1
