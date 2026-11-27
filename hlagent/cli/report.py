"""Create an offline HTML report without running analysis or calling an LLM."""

import argparse
import sys

from hlagent.paths import ROOT
from hlagent.runtime.html_report import generate_report, report_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', help='Saved run directories; select each explicitly.')
    parser.add_argument('--output', help='New report directory; default: runs/reports/report-ID.')
    args = parser.parse_args()
    try:
        path = generate_report(args.runs, args.output or report_directory(ROOT / 'runs'))
        print('HTML report: ' + str(path))
        print('Check report.json and each task status for incomplete or failed saved-result checks.')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Could not create report: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
