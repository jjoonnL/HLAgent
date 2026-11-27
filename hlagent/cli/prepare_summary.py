"""Preview or approve explicit CSV/TSV association summary normalization."""

import argparse
import json
from hlagent.paths import ROOT
from hlagent.runtime import summary_preparation as preparation, summary_suggestions
from hlagent.runtime.phenotype_suggestions import detect_delimiter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source')
    parser.add_argument('--delimiter', choices=('csv', 'tsv', 'auto'))
    parser.add_argument('--suggest', action='store_true', help='Preview local header proposals only; no files or LLM calls.')
    parser.add_argument('--columns', help='JSON object mapping CHR/SNP/BP/A1/A2/NMISS/EFFECT/SE/ESTIMABLE to source names; optional flag is null.')
    parser.add_argument('--effect-scale', choices=('or', 'log_or'))
    parser.add_argument('--se-scale', choices=('log_or',), help='Confirm standard error of log(OR), not OR.')
    parser.add_argument('--trait', help='Declared binary trait name.')
    parser.add_argument('--build', type=int, choices=(18, 19, 38))
    parser.add_argument('--run', action='store_true', help='Request approval; default is preview only.')
    parser.add_argument('--check-results', metavar='RUN_DIR')
    args = parser.parse_args()
    try:
        if args.check_results:
            preparation.prepared_summary(args.check_results)
            print(json.dumps(preparation.validate(args.check_results), indent=2))
            return 0
        if args.suggest:
            if not args.source or args.run:
                parser.error('--suggest requires a source and cannot be combined with --run.')
            delimiter = detect_delimiter(args.source) if args.delimiter in (None, 'auto') else args.delimiter
            print(summary_suggestions.render(summary_suggestions.suggest(args.source, delimiter, ROOT / 'runs')))
            return 0
        if any(getattr(args, key) is None for key in ('source', 'delimiter', 'columns', 'effect_scale', 'se_scale', 'trait', 'build')):
            parser.error('Specify source, delimiter, columns, effect/SE scales, binary trait and build.')
        settings = {'delimiter': detect_delimiter(args.source) if args.delimiter == 'auto' else args.delimiter, 'columns': json.loads(args.columns), 'effect_scale': args.effect_scale,
                    'se_scale': args.se_scale, 'phenotype_name': args.trait, 'genome_build': args.build}
        plan = preparation.prepare(args.source, settings, ROOT / 'runs')
        preparation.show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to request approval.')
            return 0
        if input('Prepare summary? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        return preparation.show_result(preparation.execute(plan))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Summary preparation failed: {error}')
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
