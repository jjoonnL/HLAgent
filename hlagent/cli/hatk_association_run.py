"""Run Node2 association on a supplied chromosome 6 PLINK marker panel."""

import argparse
import subprocess
import sys

from hlagent.analysis.hatk import check_environment, prepare_custom_association, run_example
from hlagent.cli.hatk_example import show_plan, show_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bfile-prefix', required=True, help='PLINK marker panel prefix without .bed/.bim/.fam.')
    parser.add_argument('--pheno', required=True, help='Whitespace-delimited table with FID IID and a binary phenotype column.')
    parser.add_argument('--pheno-name', required=True, help='Phenotype column; 1=control, 2=case. Every panel sample must be present.')
    parser.add_argument('--hg', required=True, type=int, choices=(18, 19, 38), help='Declared panel genome build.')
    parser.add_argument('--covar', help='Numeric covariate table starting with FID IID. Defaults to the phenotype table.')
    parser.add_argument('--covar-name', nargs='+', help='Explicit numeric covariate column names, for example age sex PC1 PC2.')
    parser.add_argument('--run', action='store_true', help='Check Docker and request approval. Default: preview only.')
    parser.add_argument('--seccomp-unconfined', action='store_true', help='Use the container-only seccomp compatibility override.')
    args = parser.parse_args()
    try:
        plan = prepare_custom_association(args.bfile_prefix, args.pheno, args.pheno_name, args.hg,
                                          seccomp_unconfined=args.seccomp_unconfined,
                                          covariate_file=args.covar, covariates=args.covar_name)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to check Docker and request approval.')
            return 0
        check_environment(args.seccomp_unconfined)
        if input('Start Marker association (HATK Node2)? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        print(f'Starting Marker association (HATK Node2). Log: {plan.run_dir / "hatk.log"}', flush=True)
        return show_result(run_example(plan))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Cannot run association: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
