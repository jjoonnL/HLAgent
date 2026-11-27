"""Run the bundled CookHLA example without editing configuration files."""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from hlagent.analysis.cookhla import check_docker, check_run_results, prepare_example, run_example


CANCEL_WORDS = {"cancel", "quit", "exit", "q"}


def ask(prompt):
    answer = input(prompt).strip()
    if answer.lower() in CANCEL_WORDS:
        raise EOFError
    return answer


def show_plan(plan, verbose=True):
    details = plan.to_dict()
    if plan.dataset is not None:
        print(f"Registered dataset: {plan.dataset}")
    label = "Example files" if plan.inputs is None else "Input files"
    print(f"{label}: verified ({len(plan.sources['files'])})")
    print(f"Input: {details['input']} (hg{details['genome_build']})")
    print(f"Reference: {details['reference']}")
    if details["agm"] == "generate":
        print("AGM: CookHLA will generate a new map. This can take substantial time.")
    else:
        print("AGM: using the supplied genetic map and error rates")
    if plan.inputs is not None:
        print("Expected result genes: " + ", ".join(details["expected_genes"]))
        print("Reference build assumed: hg18. Actual builds, panel suitability, and supplied AGM provenance are not verified.")
    print(f"Java memory: {plan.memory}; processes: {plan.processes}")
    print("These settings do not cap total container memory or CPU use.")
    print(f"Results will be saved in: {plan.run_dir}")
    if plan.seccomp_unconfined:
        print("Compatibility test: Docker syscall restrictions are disabled for this run only.")
    if verbose:
        print(f"Image: {details['image']}")
        print(f"Command: {shlex.join(plan.command())}")


def edit_plan(plan):
    while True:
        memory = ask(f"Java memory [{plan.memory}]: ") or plan.memory
        processes = ask(f"Processes [{plan.processes}]: ") or str(plan.processes)
        try:
            return prepare_example(memory, int(processes),
                                   seccomp_unconfined=plan.seccomp_unconfined)
        except ValueError as error:
            print(f"Invalid settings: {error}")
            print("Enter memory such as 4g and a positive integer for processes.")


def interactive_plan(plan):
    print("CookHLA example")
    print("The example genotype data and reference panel are included.")
    print("Enter edit to change settings, or cancel to leave. Enter keeps a default.")
    while True:
        print()
        show_plan(plan, verbose=False)
        print("Docker may download the CookHLA image when the analysis starts.")
        answer = ask("Start analysis? [y/edit/n]: ").lower()
        if answer in ("y", "yes"):
            return plan
        if answer == "edit":
            plan = edit_plan(plan)
        elif answer in ("", "n", "no"):
            return None
        else:
            print("Enter y to start, edit to change settings, or n to leave.")


def show_summary(summary):
    print(f"Samples: {summary['output_samples']} (input: {summary['input_samples']})")
    print("Results by gene: " + ", ".join(
        f"{gene}: {count}" for gene, count in summary["gene_counts"].items()))
    print(f"Result rows: {summary['result_rows']}")
    if summary["valid"]:
        print("Result checks passed: every input sample has one row per expected gene.")
    else:
        print("Result checks failed: " + "; ".join(summary["errors"]))
    print("These checks do not assess prediction accuracy.")


def show_result(result):
    if result.status == "completed":
        print("CookHLA finished.")
        if result.summary is not None:
            show_summary(result.summary)
        print(f"HLA allele predictions: {result.alleles}")
        print(f"Log: {result.log_path}")
        return 0
    if result.status in ("cancelled", "cancellation_unconfirmed"):
        print("Cancelled." if result.status == "cancelled" else "Cancellation could not be confirmed.")
        if result.error:
            print(result.error, file=sys.stderr)
        print(f"Run directory: {result.run_dir}")
        return 130
    print(f"CookHLA did not complete: {result.error}", file=sys.stderr)
    print(f"Run directory: {result.run_dir}", file=sys.stderr)
    if result.log_path:
        print(f"Log path (if created): {result.log_path}", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="Check Docker and ask to start.")
    mode.add_argument("--interactive", action="store_true", help="Review or edit example settings, then start.")
    mode.add_argument("--check-results", type=Path, metavar="RUN_DIRECTORY",
                      help="Check an existing example or custom run without rerunning CookHLA.")
    parser.add_argument("--memory", default="16g", help="CookHLA Java memory (default: 16g).")
    parser.add_argument("--processes", type=int, default=6, help="CookHLA processes (default: 6).")
    parser.add_argument("--seccomp-unconfined", action="store_true",
                        help="Disable syscall restrictions for this container only (compatibility test).")
    args = parser.parse_args()
    try:
        if args.check_results is not None:
            summary = check_run_results(args.check_results)
            show_summary(summary)
            return 0 if summary["valid"] else 1
        plan = prepare_example(args.memory, args.processes,
                               seccomp_unconfined=args.seccomp_unconfined)
        if args.interactive:
            plan = interactive_plan(plan)
            if plan is None:
                print("Not started.")
                return 0
            check_docker()
        else:
            show_plan(plan)
            if not args.run:
                print("Preview only. Use --run or --interactive to start.")
                return 0
            check_docker()
            print("Docker may download the image if it is not present.")
            if ask("Start CookHLA? [y/n]: ").lower() not in ("y", "yes"):
                print("Not started.")
                return 0
        print("Starting CookHLA. Press Ctrl+C to request cancellation.")
        print(f"Log: {plan.run_dir / 'cookhla.log'}", flush=True)
        return show_result(run_example(plan))
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
