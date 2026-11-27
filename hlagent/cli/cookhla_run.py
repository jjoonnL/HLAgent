"""Preview or run CookHLA with user-supplied target and reference data."""

import argparse
import subprocess
import sys

from hlagent.analysis.cookhla import check_docker, prepare_custom, run_analysis
from hlagent.cli.cookhla_example import ask, show_plan, show_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-prefix", required=True, help="Target PLINK prefix without extensions.")
    parser.add_argument("--reference-prefix", required=True, help="SNP2HLA-format panel prefix; assumed hg18.")
    parser.add_argument("--hg", type=int, choices=(18, 19, 38), required=True, help="Declared target genome build.")
    parser.add_argument("--genetic-map", help="Map generated for this target/reference pair.")
    parser.add_argument("--error-rate", help="Matching AGM error-rate file.")
    parser.add_argument("--memory", default="16g", help="CookHLA Java memory (default: 16g).")
    parser.add_argument("--processes", type=int, default=6, help="CookHLA processes (default: 6).")
    parser.add_argument("--seccomp-unconfined", action="store_true", help="Use the container-only seccomp compatibility override.")
    parser.add_argument("--run", action="store_true", help="Check Docker and ask for approval before running.")
    args = parser.parse_args()
    try:
        print("Checking files and recording input hashes...", flush=True)
        plan = prepare_custom(args.input_prefix, args.reference_prefix, args.hg,
                              args.genetic_map, args.error_rate, args.memory, args.processes,
                              seccomp_unconfined=args.seccomp_unconfined)
        show_plan(plan)
        if not args.run:
            print("Preview only. Add --run to check Docker and request approval.")
            return 0
        check_docker()
        print("Docker may download the CookHLA image when the analysis starts.")
        if ask("Start CookHLA? [y/n]: ").lower() not in ("y", "yes"):
            print("Not started.")
            return 0
        print("Starting CookHLA. Press Ctrl+C to request cancellation.")
        print(f"Log: {plan.run_dir / 'cookhla.log'}", flush=True)
        return show_result(run_analysis(plan))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"Cannot start: {error}", file=sys.stderr)
        return 1
    except EOFError:
        print("\nNot started.")
        return 0
    except KeyboardInterrupt:
        print("\nNot started.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
