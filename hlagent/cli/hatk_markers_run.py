"""Generate a HATK marker panel from supplied HPED and prepared dictionaries."""

import argparse
import subprocess
import sys

from hlagent.analysis.hatk import check_environment, prepare_custom_markers, run_example
from hlagent.cli.hatk_example import show_plan, show_result
from hlagent.analysis.cookhla_to_hped import conversion_hped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--hped', help='Headerless HPED with the standard eight genes, 22 columns.')
    sources.add_argument('--from-conversion-run', help='Completed CookHLA-to-HPED conversion; rechecks its output.')
    parser.add_argument('--dictionary-dir', required=True, help='Prepared allele table and two-field AA/SNP sequence/MAP files.')
    parser.add_argument('--hg', required=True, type=int, choices=(18, 19, 38), help='Declared matching genome build.')
    parser.add_argument('--imgt', required=True, help='Numeric dictionary filename tag, for example 3320.')
    parser.add_argument('--snp-prefix', help='Optional chromosome 6 SNP BED/BIM/FAM prefix with matching HPED sample metadata.')
    parser.add_argument('--run', action='store_true', help='Check Docker and request approval; default is preview.')
    parser.add_argument('--seccomp-unconfined', action='store_true', help='Use the container-only seccomp compatibility override.')
    args = parser.parse_args()
    try:
        hped = conversion_hped(args.from_conversion_run) if args.from_conversion_run else args.hped
        plan = prepare_custom_markers(hped, args.dictionary_dir, args.hg, args.imgt, args.snp_prefix,
                                      seccomp_unconfined=args.seccomp_unconfined)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to check Docker and request approval.')
            return 0
        check_environment(args.seccomp_unconfined)
        if input('Start HLA marker generation (HATK Node1)? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        print(f'Starting HLA marker generation (HATK Node1). Log: {plan.run_dir / "hatk.log"}', flush=True)
        return show_result(run_example(plan))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Cannot generate HATK markers: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
