"""Run one supplied-condition analysis without a parent Node2 run."""

import argparse
import subprocess
import sys

from hlagent.analysis.hatk import check_environment, prepare_custom_conditional, run_example
from hlagent.cli.hatk_example import show_plan, show_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bfile-prefix', required=True, help='Chromosome 6 PLINK marker panel prefix.')
    parser.add_argument('--pheno', required=True, help='Phenotype table starting with FID IID.')
    parser.add_argument('--pheno-name', required=True, help='Binary phenotype column; 1=control, 2=case.')
    parser.add_argument('--hg', required=True, type=int, choices=(18, 19, 38), help='Declared panel build.')
    parser.add_argument('--condition-marker', required=True, action='append', help='Exact marker ID; repeat for several.')
    parser.add_argument('--covar', help='Numeric covariate table starting with FID IID. Defaults to the phenotype table.')
    parser.add_argument('--covar-name', nargs='+', help='Explicit numeric covariate column names, for example age sex PC1 PC2.')
    parser.add_argument('--run', action='store_true', help='Check Docker and request approval. Default: preview only.')
    parser.add_argument('--seccomp-unconfined', action='store_true', help='Use the container-only seccomp compatibility override.')
    args = parser.parse_args()
    try:
        plan = prepare_custom_conditional(args.bfile_prefix, args.pheno, args.pheno_name, args.hg, args.condition_marker,
                                          seccomp_unconfined=args.seccomp_unconfined,
                                          covariate_file=args.covar, covariates=args.covar_name)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to check Docker and request approval.')
            return 0
        check_environment(args.seccomp_unconfined)
        if input('Start Conditional association (HATK Node3)? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        print(f'Starting Conditional association (HATK Node3). Log: {plan.run_dir / "hatk.log"}', flush=True)
        return show_result(run_example(plan))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Cannot run conditional analysis: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
