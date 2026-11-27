"""Prepare a binary phenotype from explicit local CSV/TSV column and value mappings."""

from hlagent.paths import new_run_id

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil

from hlagent.analysis.hatk_custom_association import check_settings
from hlagent.runtime.phenotype_inputs import review_phenotype


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_table(path, delimiter):
    if delimiter not in ('csv', 'tsv'):
        raise ValueError('Choose csv or tsv explicitly.')
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream, delimiter=',' if delimiter == 'csv' else '\t', strict=True))
    except csv.Error as error:
        raise ValueError('Malformed CSV/TSV quoting. Correct the clinical table.') from error
    if not rows or not rows[0] or any(not name for name in rows[0]) or len(set(rows[0])) != len(rows[0]):
        raise ValueError('Provide a header with nonempty, unique column names.')
    if len(rows) < 2:
        raise ValueError('The clinical table has no data rows.')
    for number, row in enumerate(rows[1:], 2):
        if len(row) != len(rows[0]):
            raise ValueError(f'Clinical row {number} has a different column count from its header.')
    return rows[0], rows[1:]


def converted_text(source, fam, settings):
    if set(settings) != {'delimiter', 'fid_column', 'iid_column', 'trait_column', 'control_value', 'case_value', 'phenotype_name'}:
        raise ValueError('Supply exactly the delimiter, three column choices, control/case values and output phenotype name.')
    header, rows = read_table(source, settings['delimiter'])
    check_settings(settings['phenotype_name'], 18)
    if settings['phenotype_name'] in ('FID', 'IID'):
        raise ValueError('The output phenotype name must differ from FID and IID.')
    columns = [settings[key] for key in ('fid_column', 'iid_column', 'trait_column')]
    if any(name not in header for name in columns):
        raise ValueError('Select existing FID, IID and trait columns.')
    if settings['trait_column'] in columns[:2]:
        raise ValueError('The trait column must differ from the ID columns.')
    control, case = settings['control_value'], settings['case_value']
    if not isinstance(control, str) or not isinstance(case, str) or not control.strip() or not case.strip() or control == case:
        raise ValueError('Specify distinct, nonempty control and case values. Matching is exact and case-sensitive.')
    indices = [header.index(name) for name in columns]
    values = {}
    for number, row in enumerate(rows, 2):
        fid, iid, trait = [row[index] for index in indices]
        if not fid or not iid or any(c.isspace() for c in fid + iid):
            raise ValueError(f'Clinical row {number} has an empty ID or whitespace within an ID. IDs are not rewritten.')
        if (fid, iid) in values:
            raise ValueError(f'Duplicate FID/IID pair at clinical row {number}; resolve duplicates before conversion.')
        if trait not in (control, case):
            raise ValueError(f'Clinical row {number} is outside the explicit control/case mapping. No rows are silently recoded or dropped.')
        values[fid, iid] = '1' if trait == control else '2'
    people = [line.split() for line in Path(fam).read_text().splitlines()]
    if not people or any(len(row) != 6 for row in people):
        raise ValueError('Supply a nonempty six-column FAM.')
    ids = [tuple(row[:2]) for row in people]
    if len(set(ids)) != len(ids):
        raise ValueError('FAM has duplicate FID/IID pairs.')
    missing = sum(key not in values for key in ids)
    if missing:
        raise ValueError(f'{missing} FAM samples have no matching clinical record. No FAM samples are dropped automatically.')
    codes = [values[key] for key in ids]
    if not all(code in codes for code in ('1', '2')):
        raise ValueError('Matched FAM samples must include both controls and cases.')
    output = io.StringIO()
    writer = csv.writer(output, delimiter='\t', lineterminator='\n')
    writer.writerow(['FID', 'IID', settings['phenotype_name']])
    writer.writerows([*key, values[key]] for key in ids)
    summary = {'samples': len(ids), 'controls': codes.count('1'), 'cases': codes.count('2'),
               'source_rows': len(rows), 'clinical_rows_outside_fam': len(values) - len(ids)}
    return output.getvalue(), summary


def prepare(source, fam, settings, run_root):
    source, fam = [Path(p).expanduser().resolve() for p in (source, fam)]
    _, summary = converted_text(source, fam, settings)
    return {'stage': 'phenotype_preparation', 'input': str(source),
            'run_dir': str(Path(run_root).resolve() / new_run_id('phenotype', run_root)),
            'settings': dict(settings), 'summary': summary,
            'files': [{'source': str(path), 'path': relative, 'sha256': digest(path)}
                      for path, relative in ((source, 'input/clinical.table'), (fam, 'input/cohort.fam'))],
            'source_sha256': digest(__file__)}


def validate(run):
    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    plan = record['plan']
    if plan.get('stage') != 'phenotype_preparation':
        raise ValueError('Select a phenotype preparation run.')
    if len(plan['files']) != 2 or {item['path'] for item in plan['files']} != {'input/clinical.table', 'input/cohort.fam'}:
        raise ValueError('Invalid preparation input record.')
    for item in plan['files']:
        if digest(run / item['path']) != item['sha256']:
            raise ValueError('A recorded preparation input changed.')
    text, summary = converted_text(run / 'input/clinical.table', run / 'input/cohort.fam', plan['settings'])
    output = run / 'output/phenotype.phe'
    if output.read_text() != text or summary != plan['summary']:
        raise ValueError('Prepared phenotype differs from its explicit input mappings.')
    review_phenotype(run / 'input/cohort.fam', output, plan['settings']['phenotype_name'])
    return summary


def prepared_phenotype(run):
    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    if record.get('status') != 'completed':
        raise ValueError('Select a completed phenotype preparation run.')
    validate(run)
    output = run / 'output/phenotype.phe'
    if record['outputs'] != [{'path': 'output/phenotype.phe', 'sha256': digest(output)}]:
        raise ValueError('Prepared phenotype differs from its recorded output hash.')
    return output


def execute(plan):
    fresh = prepare(*(item['source'] for item in plan['files']), plan['settings'], Path(plan['run_dir']).parent)
    if any(fresh[key] != plan[key] for key in ('settings', 'summary', 'files', 'source_sha256')):
        raise ValueError('Clinical input, FAM, mappings or source changed after review. Prepare a new plan.')
    run = Path(plan['run_dir'])
    run.mkdir(parents=True)
    record = {'status': 'running', 'plan': plan, 'started_at': datetime.now(timezone.utc).isoformat()}
    path = run / 'run.json'
    path.write_text(json.dumps(record, indent=2) + '\n')
    try:
        (run / 'input').mkdir()
        (run / 'output').mkdir()
        for item in plan['files']:
            shutil.copy2(item['source'], run / item['path'])
            if digest(run / item['path']) != item['sha256']:
                raise ValueError('Preparation input changed while copying.')
        text, _ = converted_text(run / 'input/clinical.table', run / 'input/cohort.fam', plan['settings'])
        (run / 'output/phenotype.phe').write_text(text)
        record['summary'] = validate(run)
        record['outputs'] = [{'path': 'output/phenotype.phe', 'sha256': digest(run / 'output/phenotype.phe')}]
        record['status'] = 'completed'
    except KeyboardInterrupt:
        record['status'] = 'cancelled'
    except (OSError, ValueError) as error:
        record.update(status='failed', error=str(error))
    finally:
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(record, indent=2) + '\n')
    return record


def show_plan(plan):
    print('Clinical table to binary phenotype: explicit mappings. Source files are preserved.')
    if plan.get('mapping_proposals'):
        print('Reviewed proposal method: ' + plan['mapping_proposals']['method'])
    for item in plan['files']:
        print('Input: ' + item['source'])
    print('Column and value mappings:')
    print(json.dumps(plan['settings'], indent=2))
    print('Matched sample summary:')
    print(json.dumps(plan['summary'], indent=2))
    print('Only FAM samples are written, in FAM order. Clinical rows outside FAM are counted above.')
    print('This checks format and matching, not clinical validity. No diagnosis, coding, IDs or build are inferred.')
    print('Results will be saved in: ' + plan['run_dir'])


def show_result(record):
    print('Status: ' + record['status'])
    run = Path(record['plan']['run_dir'])
    if record['status'] == 'completed':
        print('Phenotype: ' + str(run / 'output/phenotype.phe'))
        print('Prepared format and sample matching checked. This does not verify clinical labels.')
        print('In node2-custom/node3-custom/node4-custom, enter run at the phenotype file prompt to reuse this result.')
    if record.get('error'):
        print(record['error'])
    print('Run record: ' + str(run / 'run.json'))
    return 0 if record['status'] == 'completed' else 1
