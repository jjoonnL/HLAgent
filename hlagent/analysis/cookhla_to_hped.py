"""Convert CookHLA allele calls to standard eight-gene HPED without inventing calls."""


from hlagent.paths import ROOT, new_run_id

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

from hlagent.analysis.hatk_worker import GENES


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def converted_text(alleles, fam):
    people = [line.split() for line in Path(fam).read_text().splitlines()]
    keys = [tuple(row[:2]) for row in people]
    if (not people or any(len(row) != 6 or row[4] not in ('0', '1', '2') for row in people)
            or len(set(keys)) != len(keys)):
        raise ValueError('Expected nonempty FAM with unique FID/IID and six metadata columns.')
    samples = set(keys)
    calls = {}
    genes = set()
    for number, line in enumerate(Path(alleles).read_text().splitlines(), 1):
        row = line.split()
        if len(row) != 8:
            raise ValueError(f'CookHLA line {number} must have eight columns.')
        fid, iid, gene = row[:3]
        key = (fid, iid, gene)
        if (fid, iid) not in samples or gene not in GENES or key in calls:
            raise ValueError(f'Unexpected or duplicate sample/gene at CookHLA line {number}.')
        pair = row[4].split(',')
        if len(pair) != 2:
            raise ValueError(f'Expected a two-field allele pair at CookHLA line {number}.')
        result = []
        for value in pair:
            if value == '0':
                result.append('0')
                continue
            if re.fullmatch(r'\d{4}[NLSCAQ]?', value):
                value = value[:2] + ':' + value[2:]
            if not re.fullmatch(r'\d{2,3}:\d{2,3}[NLSCAQ]?', value):
                raise ValueError('Unsupported allele code; compact codes longer than four digits are ambiguous. '
                                 'Supply explicit colon-delimited two-field calls instead.')
            result.append(gene + '*' + value)
        calls[key] = result
        genes.add(gene)
    if not genes or any((fid, iid, gene) not in calls for fid, iid in keys for gene in genes):
        raise ValueError('CookHLA calls must cover the same genes for every FAM sample; no silent sample loss.')
    rows = [row + [allele for gene in GENES for allele in calls.get((*row[:2], gene), ('0', '0'))]
            for row in people]
    missing = Counter({gene: sum(value == '0' for row in rows for value in row[6 + 2 * i:8 + 2 * i])
                       for i, gene in enumerate(GENES)})
    summary = {'samples': len(rows), 'predicted_genes': [g for g in GENES if g in genes],
               'absent_genes_filled_with_zero': [g for g in GENES if g not in genes],
               'missing_allele_calls_by_gene': dict(missing), 'field_format': 2,
               'phased': False, 'confidence_filter_applied': False}
    return ''.join(' '.join(row) + '\n' for row in rows), summary


def cookhla_run_inputs(run):
    from hlagent.analysis.cookhla import TARGET, RESULT_NAME, check_run_results

    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    if record.get('status') != 'completed' or not check_run_results(run)['valid']:
        raise ValueError('Select a completed CookHLA run with passing result checks.')
    custom = record['plan'].get('inputs') is not None
    fam = run / ('input/genotypes.fam' if custom else f'input/{TARGET}.fam')
    alleles = run / 'output' / ('imputed.MHC.HLA_IMPUTATION_OUT.alleles' if custom else RESULT_NAME)
    inputs = record['plan']['sources']['files']
    expected = next(item['sha256'] for item in inputs if item['path'] == str(fam.relative_to(run)))
    if digest(fam) != expected:
        raise ValueError('CookHLA FAM differs from its recorded input hash.')
    return alleles, fam


def prepare_conversion(alleles, fam, run_root=None):
    alleles, fam = [Path(p).expanduser().resolve() for p in (alleles, fam)]
    _, summary = converted_text(alleles, fam)
    root = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / 'runs'
    return {'stage': 'cookhla_to_hped', 'run_dir': str(root / new_run_id('hped', root)),
            'files': [{'source': str(path), 'path': 'input/' + name, 'sha256': digest(path)}
                      for path, name in ((alleles, 'calls.alleles'), (fam, 'cohort.fam'))],
            'summary': summary, 'source_sha256': digest(__file__)}


def validate_conversion(run):
    run = Path(run)
    record = json.loads((run / 'run.json').read_text())
    plan = record['plan']
    if plan.get('stage') != 'cookhla_to_hped' or {item['path'] for item in plan['files']} != {'input/calls.alleles', 'input/cohort.fam'}:
        raise ValueError('Invalid conversion input record.')
    for item in plan['files']:
        if digest(run / item['path']) != item['sha256']:
            raise ValueError('Conversion input changed.')
    text, summary = converted_text(run / 'input/calls.alleles', run / 'input/cohort.fam')
    if text != (run / 'output/cohort.hped').read_text() or summary != plan['summary']:
        raise ValueError('HPED differs from the checked CookHLA calls/FAM.')
    return summary


def run_conversion(plan):
    fresh = prepare_conversion(*(item['source'] for item in plan['files']))
    if any(fresh[key] != plan[key] for key in ('files', 'summary', 'source_sha256')):
        raise ValueError('Conversion inputs/source changed after review.')
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
                raise ValueError('Conversion input changed while copying.')
        text, _ = converted_text(run / 'input/calls.alleles', run / 'input/cohort.fam')
        (run / 'output/cohort.hped').write_text(text)
        record['summary'] = validate_conversion(run)
        record['outputs'] = [{'path': 'output/cohort.hped', 'sha256': digest(run / 'output/cohort.hped')}]
        record['status'] = 'completed'
    except KeyboardInterrupt:
        record['status'] = 'cancelled'
    except (OSError, ValueError) as error:
        record.update(status='failed', error=str(error))
    finally:
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(record, indent=2) + '\n')
    return record


def conversion_hped(run):
    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    if record.get('status') != 'completed':
        raise ValueError('Select a completed CookHLA-to-HPED conversion run.')
    validate_conversion(run)
    saved = {item['path']: item['sha256'] for item in record['outputs']}
    hped = run / 'output/cohort.hped'
    if saved.get('output/cohort.hped') != digest(hped):
        raise ValueError('Converted HPED differs from its recorded output hash.')
    return hped


def show_plan(plan):
    print('CookHLA to HPED: FAM metadata/order and two-field allele calls are preserved.')
    print(json.dumps(plan['summary'], indent=2))
    print('Missing genes are not imputed. Allele pairs are unphased; no confidence threshold is applied.')
    print('Results will be saved in: ' + plan['run_dir'])


def show_result(result):
    print('Status: ' + result['status'])
    if result['status'] == 'completed':
        print('HPED: ' + str(Path(result['plan']['run_dir']) / 'output/cohort.hped'))
        print('Use this HPED with HLA Fine-mapping Node1 and a compatible dictionary.')
    elif result.get('error'):
        print(result['error'], file=sys.stderr)
    record_path = Path(result['plan']['run_dir']) / 'run.json'
    if record_path.is_file():
        print('Run record: ' + str(record_path))
    else:
        print('Run directory (if created): ' + str(record_path.parent))
    return 0 if result['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-cookhla-run', help='Completed V2 CookHLA run; uses its staged FAM and result.')
    parser.add_argument('--alleles', help='CookHLA eight-column .alleles output.')
    parser.add_argument('--fam', help='Matching original target FAM; metadata/order are preserved.')
    parser.add_argument('--run', action='store_true', help='Request approval and write a new HPED run.')
    parser.add_argument('--check-results', metavar='RUN_DIR')
    args = parser.parse_args()
    try:
        if args.check_results:
            print(json.dumps(validate_conversion(args.check_results), indent=2))
            return 0
        if args.from_cookhla_run:
            if args.alleles or args.fam:
                parser.error('Choose --from-cookhla-run or --alleles/--fam, not both.')
            alleles, fam = cookhla_run_inputs(args.from_cookhla_run)
        else:
            if not args.alleles or not args.fam:
                parser.error('Provide --from-cookhla-run or both --alleles and --fam.')
            alleles, fam = args.alleles, args.fam
        plan = prepare_conversion(alleles, fam)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to request approval.')
            return 0
        if input('Convert to HPED? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        result = run_conversion(plan)
        return show_result(result)
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(f'Cannot convert CookHLA calls: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
