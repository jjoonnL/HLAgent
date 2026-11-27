"""Preview or run BEAGLE phasing of a HATK marker panel for Node4."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from hlagent.analysis.hatk import check_environment, prepare_phasing, run_example
from hlagent.analysis.hatk_phasing import validate_phasing_outputs


def node1_panel(run):
    from hlagent.analysis.hatk import digest, validate_results
    from hlagent.analysis.hatk_custom_markers import validate_custom_marker_outputs

    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    stage = record.get('plan', {}).get('example')
    if record.get('status') != 'completed' or stage not in ('hatk_node1', 'hatk_node1_custom'):
        raise ValueError('Select a completed Node1 run.')
    (validate_results if stage == 'hatk_node1' else validate_custom_marker_outputs)(run)
    saved = {item['path']: item['sha256'] for item in record['outputs']}
    for ext in ('.bed', '.bim', '.fam'):
        name = 'output/markers' + ext
        if saved.get(name) != digest(run / name):
            raise ValueError('Node1 marker panel differs from its recorded output hash.')
    return run / 'output/markers', record['plan']['genome_build']


def show_plan(plan):
    settings = plan.data["settings"]
    build = settings["genome_build"]
    print('HATK marker phasing: BEAGLE 3.3.2. Unrelated founder samples. Single thread')
    print(f"Panel: {plan.data['panel']['prefix']}; {plan.data['panel']['samples']} samples; "
          f"{plan.data['panel']['variants']} markers; {plan.data['aa_markers']} AA markers; declared hg{build}")
    print(f"Java memory: {settings['memory']}; seed: {settings['seed']}")
    print('Phases all supplied markers and infers missing genotypes. No external reference panel or new QC.')
    print('G/C coding and unique phasing coordinates are temporary. Original allele labels/AA coordinates are retained.')
    print('The marker/BGL rows are sorted by the adjusted coordinates. Phase accuracy is not assessed.')
    print(f"Image: {plan.to_dict()['image']}")
    if plan.seccomp_unconfined:
        print('Compatibility test: Docker syscall restrictions are disabled for this run only.')
    print(f'Results will be saved in: {plan.run_dir}')


def show_result(record):
    print('Status: ' + record['status'])
    directory = Path(record['plan']['run_dir'])
    if record['status'] == 'completed':
        print(json.dumps(record['summary'], indent=2))
        print('Genotype/sample/marker checks passed. These checks do not assess phase accuracy.')
        print('AA-only export: ' + str(directory / 'output/phased-aa.bgl'))
        print('Omnibus FAM input (HATK Node4): ' + str(directory / 'output/cohort.fam'))
        print('Select this completed run in Node4 to include AA insertions with the AA markers.')
        print('Supply a matching named phenotype table. No new phasing is needed.')
    elif record.get('error'):
        print(record['error'], file=sys.stderr)
    if (directory / 'run.json').is_file():
        print('Run record: ' + str(directory / 'run.json'))
    else:
        print('Run directory (if created): ' + str(directory))
    return 0 if record['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--from-node1-run', help='Completed bundled or custom Node1 run. Checks its result hashes.')
    sources.add_argument('--input-prefix', help='Chr6 HATK BED/BIM/FAM prefix, including AA markers.')
    parser.add_argument('--hg', type=int, choices=(18, 19, 38), help='Required for a supplied panel. Declaration only.')
    parser.add_argument('--memory', default='4g', help='Java heap, for example 4g. Does not cap container memory.')
    parser.add_argument('--seed', type=int, default=1, help='BEAGLE random seed. Default 1.')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--seccomp-unconfined', action='store_true')
    parser.add_argument('--check-results', metavar='RUN_DIR')
    args = parser.parse_args()
    try:
        if args.check_results:
            print(json.dumps(validate_phasing_outputs(args.check_results), indent=2))
            return 0
        if args.from_node1_run:
            prefix, build = node1_panel(args.from_node1_run)
            if args.hg is not None and args.hg != build:
                parser.error('--hg differs from the recorded Node1 genome build.')
        else:
            if args.input_prefix is None or args.hg is None:
                parser.error('Provide --from-node1-run or both --input-prefix and --hg.')
            prefix, build = args.input_prefix, args.hg
        plan = prepare_phasing(prefix, build, args.memory, args.seed, seccomp_unconfined=args.seccomp_unconfined)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to check Docker and request approval.')
            return 0
        check_environment(args.seccomp_unconfined)
        if input('Start HATK phasing? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        print(f'Starting HATK phasing. Log: {plan.run_dir / "hatk.log"}', flush=True)
        record = run_example(plan)
        return show_result(record)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired) as error:
        print(f'Cannot phase HATK markers: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
