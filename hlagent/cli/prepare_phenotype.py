"""Preview or approve local CSV/TSV to binary phenotype preparation."""

import argparse
import json
from hlagent.paths import ROOT
from hlagent.cli.hatk_phasing_run import node1_panel
from hlagent.runtime import phenotype_preparation as preparation
from hlagent.runtime import phenotype_suggestions as suggestions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', help='UTF-8 clinical CSV/TSV with a header.')
    parser.add_argument('--delimiter', choices=('csv', 'tsv', 'auto'))
    parser.add_argument('--suggest', action='store_true', help='Display local mapping candidates only; no files or LLM calls.')
    for field in ('fid-column', 'iid-column', 'trait-column', 'control-value', 'case-value', 'name'):
        parser.add_argument('--' + field)
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--fam', help='Matching six-column FAM.')
    target.add_argument('--from-node1-run', help='Completed Node1 run; rechecks panel and uses its FAM.')
    parser.add_argument('--run', action='store_true', help='Request local approval; default is preview only.')
    parser.add_argument('--check-results', metavar='RUN_DIR')
    args = parser.parse_args()
    try:
        if args.check_results:
            preparation.prepared_phenotype(args.check_results)
            print(json.dumps(preparation.validate(args.check_results), indent=2))
            return 0
        if args.suggest:
            if args.run or not args.source or not (args.fam or args.from_node1_run):
                parser.error('--suggest needs a source and matching FAM/Node1 run, and cannot be combined with --run.')
            delimiter = suggestions.detect_delimiter(args.source) if args.delimiter in (None, 'auto') else args.delimiter
            fam = str(node1_panel(args.from_node1_run)[0]) + '.fam' if args.from_node1_run else args.fam
            print(json.dumps(suggestions.suggest(args.source, fam, delimiter), indent=2))
            print('CLI suggestions are preview only. Supply explicit mapping arguments to prepare files; optional LLM assist is available in chat.')
            return 0
        if any(getattr(args, field) is None for field in ('source', 'delimiter', 'fid_column', 'iid_column',
                                                        'trait_column', 'control_value', 'case_value', 'name')):
            parser.error('Specify source, delimiter, ID/trait columns, control/case values and output name.')
        if not args.fam and not args.from_node1_run:
            parser.error('Supply --fam or --from-node1-run.')
        fam = str(node1_panel(args.from_node1_run)[0]) + '.fam' if args.from_node1_run else args.fam
        settings = {key: getattr(args, key) for key in ('delimiter', 'fid_column', 'iid_column', 'trait_column',
                                                       'control_value', 'case_value')}
        if settings['delimiter'] == 'auto':
            settings['delimiter'] = suggestions.detect_delimiter(args.source)
        settings['phenotype_name'] = args.name
        plan = preparation.prepare(args.source, fam, settings, ROOT / 'runs')
        preparation.show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to request approval.')
            return 0
        if input('Prepare phenotype? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        return preparation.show_result(preparation.execute(plan))
    except (OSError, ValueError, KeyError) as error:
        print(f'Phenotype preparation failed: {error}')
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
