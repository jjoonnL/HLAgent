"""Prepare dictionaries from a checked local IMGT snapshot."""

import argparse
import json
import sys

from hlagent.analysis.hatk import prepare_dictionary, run_example
from hlagent.analysis.imgt_dictionary import validate_dictionary_outputs
from hlagent.cli.hatk_example import show_plan, show_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--source', help='Local IMGT directory. Version is checked from source headers. No download is performed.')
    source.add_argument('--download', metavar='VERSION', help='Obtain an IMGT release or latest after approval.')
    source.add_argument('--check-results', help='Recheck a completed dictionary run without generating files.')
    parser.add_argument('--run', action='store_true', help='Review a plan and ask for y approval.')
    parser.add_argument('--seccomp-unconfined', action='store_true')
    args = parser.parse_args()
    try:
        if args.check_results:
            print(json.dumps(validate_dictionary_outputs(args.check_results), indent=2))
            return 0
        selected = args.source
        if args.download:
            from hlagent.analysis.imgt_source import download_request, download_source
            from hlagent.analysis.hatk import ROOT
            version = download_request(args.download)
            print('Download IMGT ' + version + ' from ANHIG/IMGTHLA on GitHub.')
            if not args.run:
                print('Preview only. Add --run to request download and generation approvals.')
                return 0
            if input('Download IMGT source? [y/n]: ').strip().lower() not in ('y', 'yes'):
                print('Not started.')
                return 0
            selected = download_source(version, ROOT / 'runs/imgt-sources')
        plan = prepare_dictionary(selected, seccomp_unconfined=args.seccomp_unconfined)
        show_plan(plan)
        if not args.run:
            print('Preview only. Add --run to request approval.')
            return 0
        if input('Prepare HATK dictionaries? [y/n]: ').strip().lower() not in ('y', 'yes'):
            print('Not started.')
            return 0
        result = run_example(plan)
        show_result(result)
        return 0 if result['status'] == 'completed' else 1
    except (OSError, RuntimeError, ValueError) as error:
        print(f'Dictionary preparation failed: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nNot started or cancelled.')
        return 1
