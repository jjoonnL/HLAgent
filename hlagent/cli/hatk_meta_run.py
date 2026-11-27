"""Combine two supplied binary-trait study tables using fixed-effect IVW."""

import argparse
import subprocess
import sys

from hlagent.analysis.hatk import check_environment, prepare_custom_meta, run_example
from hlagent.cli.hatk_example import show_plan, show_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study1', required=True, help='Normalized association TSV from the first study.')
    parser.add_argument('--study2', required=True, help='Normalized association TSV from the second study.')
    parser.add_argument('--confirm-nonoverlap', action='store_true', help='Declare that study samples do not overlap.')
    parser.add_argument('--pheno-name', required=True, help='Shared trait name; both tables must use compatible case coding and log-odds effects.')
    parser.add_argument('--hg', required=True, type=int, choices=(18, 19, 38), help='Declared panel build.')
    parser.add_argument('--run', action='store_true', help='Check Docker and request approval. Default: preview only.')
    parser.add_argument('--seccomp-unconfined', action='store_true', help='Use the container-only seccomp compatibility override.')
    args = parser.parse_args()
    try:
        plan = prepare_custom_meta(args.study1, args.study2, args.pheno_name, args.hg, args.confirm_nonoverlap,
                                          seccomp_unconfined=args.seccomp_unconfined)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to check Docker and request approval.')
            return 0
        check_environment(args.seccomp_unconfined)
        if input('Start Meta-analysis (HATK Node5)? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        print(f'Starting Meta-analysis (HATK Node5). Log: {plan.run_dir / "hatk.log"}', flush=True)
        return show_result(run_example(plan))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Cannot run meta-analysis: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
