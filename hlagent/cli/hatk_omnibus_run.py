"""Run the original HATK omnibus model on prephased AA and insertion markers."""

import argparse
import subprocess
import sys

from hlagent.analysis.hatk import check_environment, prepare_custom_omnibus, run_example
from hlagent.cli.hatk_example import show_plan, show_result
from hlagent.analysis.hatk_phasing import completed_phasing_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--phased-aa', help='AA BGL with optional INS_AA z/Z rows. Paired I id or five FAM-matching headers.')
    sources.add_argument('--from-phasing-run', help='Recheck a completed Phasing run and prepare a separate AA/insertion input.')
    parser.add_argument('--fam', help='Required with --phased-aa; six-column FAM in the phased pair order.')
    parser.add_argument('--pheno', required=True, help='Phenotype table starting with FID IID.')
    parser.add_argument('--pheno-name', required=True, help='Binary phenotype column; 1=control, 2=case.')
    parser.add_argument('--hg', type=int, choices=(18, 19, 38), help='Required with --phased-aa; declared panel build.')
    parser.add_argument('--run', action='store_true', help='Check Docker and request approval. Default: preview only.')
    parser.add_argument('--seccomp-unconfined', action='store_true', help='Use the container-only seccomp compatibility override.')
    args = parser.parse_args()
    try:
        if args.from_phasing_run:
            if args.fam:
                parser.error('--fam cannot be combined with --from-phasing-run.')
            phased, fam, build = completed_phasing_inputs(args.from_phasing_run)
            if args.hg is not None and args.hg != build:
                parser.error('--hg differs from the recorded phasing build.')
        else:
            if not args.fam or args.hg is None:
                parser.error('--phased-aa requires --fam and --hg.')
            phased, fam, build = args.phased_aa, args.fam, args.hg
        plan = prepare_custom_omnibus(phased, fam, args.pheno, args.pheno_name, build,
                                          seccomp_unconfined=args.seccomp_unconfined)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to check Docker and request approval.')
            return 0
        check_environment(args.seccomp_unconfined)
        if input('Start Amino-acid omnibus test (HATK Node4)? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        print(f'Starting Amino-acid omnibus test (HATK Node4). Log: {plan.run_dir / "hatk.log"}', flush=True)
        return show_result(run_example(plan))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Cannot run omnibus analysis: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
