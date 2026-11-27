"""Register local CookHLA input combinations and prepare them by name."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import re
import sys

from hlagent.analysis.cookhla import prepare_custom


from hlagent.paths import ROOT

REGISTRY = ROOT / "datasets"
INPUT_KEYS = ("input_prefix", "reference_prefix", "genome_build", "genetic_map", "error_rate")


def entry_path(name, registry):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("Use 1–64 letters, digits, underscores, or hyphens, starting with a letter.")
    return Path(registry) / (name + ".json")


def read_entry(name, registry=REGISTRY):
    entry = json.loads(entry_path(name, registry).read_text())
    if (not isinstance(entry, dict) or entry.get("version") != 1 or entry.get("name") != name
            or not isinstance(entry.get("inputs"), dict)
            or set(entry["inputs"]) != set(INPUT_KEYS)
            or not isinstance(entry.get("sources"), dict)):
        raise ValueError("Invalid dataset record. Register the input combination again under a new name.")
    inputs = entry["inputs"]
    if (any(not isinstance(inputs[key], str) or not inputs[key] for key in INPUT_KEYS[:2])
            or type(inputs["genome_build"]) is not int
            or any(inputs[key] is not None and not isinstance(inputs[key], str) for key in INPUT_KEYS[3:])):
        raise ValueError("Invalid dataset input values.")
    return entry


def list_datasets(registry=REGISTRY):
    return sorted(path.stem for path in Path(registry).glob("*.json"))


def register_dataset(name, plan, registry=REGISTRY):
    """Save checked custom inputs without copying data or starting a run."""
    path = entry_path(name, registry)
    if plan.inputs is None:
        raise ValueError("Register a custom input plan, not an example plan.")
    inputs = plan.inputs
    entry = {"version": 1, "name": name, "inputs": {
        "input_prefix": inputs["target"]["prefix"],
        "reference_prefix": inputs["reference"]["prefix"],
        "genome_build": inputs["target_genome_build"],
        "genetic_map": inputs["genetic_map"], "error_rate": inputs["error_rate"],
    }, "sources": plan.sources}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves existing registrations, including concurrent writes.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, indent=2) + "\n")
    return path


def register_from_run(name, run_dir, registry=REGISTRY):
    record = json.loads((Path(run_dir) / "run.json").read_text())
    plan = record.get("plan", {}) if isinstance(record, dict) else {}
    inputs = plan.get("inputs") if isinstance(plan, dict) else None
    if not isinstance(inputs, dict):
        raise ValueError("This run has no custom input record. Use direct input arguments instead.")
    try:
        current = prepare_custom(inputs["target"]["prefix"], inputs["reference"]["prefix"],
                                 inputs["target_genome_build"], inputs["genetic_map"], inputs["error_rate"])
    except (KeyError, TypeError) as error:
        raise ValueError("The run has an invalid custom input record.") from error
    if current.sources != plan.get("sources"):
        raise ValueError("Input files differ from the run record. Register the current inputs explicitly instead.")
    return register_dataset(name, current, registry)


def prepare_registered(name, memory="16g", processes=6, seccomp_unconfined=False, registry=REGISTRY):
    entry = read_entry(name, registry)
    plan = prepare_custom(**entry["inputs"], memory=memory, processes=processes,
                          seccomp_unconfined=seccomp_unconfined)
    if plan.sources != entry["sources"]:
        raise ValueError("Registered files changed. Register the current input combination under a new name.")
    return replace(plan, dataset=name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List locally registered names.")
    register = commands.add_parser("register", help="Save an input combination once; never starts analysis.")
    register.add_argument("name")
    register.add_argument("--from-run", help="Reuse original inputs recorded in a custom run directory.")
    register.add_argument("--input-prefix")
    register.add_argument("--reference-prefix")
    register.add_argument("--hg", type=int, choices=(18, 19, 38))
    register.add_argument("--genetic-map")
    register.add_argument("--error-rate")
    args = parser.parse_args()
    try:
        if args.command == "list":
            print("\n".join(list_datasets()) or "No datasets registered.")
            return 0
        if args.from_run:
            if any(value is not None for value in (args.input_prefix, args.reference_prefix, args.hg,
                                                   args.genetic_map, args.error_rate)):
                raise ValueError("Use --from-run alone, without input arguments.")
            path = register_from_run(args.name, args.from_run)
        else:
            if args.input_prefix is None or args.reference_prefix is None or args.hg is None:
                raise ValueError("Provide --input-prefix, --reference-prefix, and --hg, or use --from-run.")
            plan = prepare_custom(args.input_prefix, args.reference_prefix, args.hg,
                                  args.genetic_map, args.error_rate)
            path = register_dataset(args.name, plan)
        print(f"Registered {args.name}: {path}")
        print("File identities recorded. AGM suitability and actual genomic builds are not verified.")
        print("No analysis was started. In the assistant, type saved to select a registered dataset.")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Dataset registration failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
