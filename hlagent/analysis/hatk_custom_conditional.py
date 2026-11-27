"""One supplied-marker conditional scan with a fresh, matched baseline scan."""

import json
from pathlib import Path
import subprocess

from hlagent.analysis.hatk_association import association_rows, marker_table
from hlagent.analysis.hatk_conditional import comparison_text
from hlagent.analysis.hatk_custom_association import aligned_text, association_command, effect_text, settings_from_run, check_covariates, write_covariates


def review_custom_conditions(bim, requested):
    markers = marker_table(bim)
    if (not isinstance(requested, (list, tuple)) or not requested
            or any(not isinstance(name, str) or not name or any(c.isspace() for c in name) for name in requested)
            or len(set(requested)) != len(requested)):
        raise ValueError("Provide a nonempty list of unique condition marker IDs.")
    for name in requested:
        if name not in markers:
            raise ValueError(f"Condition marker is absent from the supplied BIM: {name}")
    if len(requested) >= len(markers):
        raise ValueError("At least one marker must remain for testing.")
    return list(requested)


def read_custom_conditions(work):
    work = Path(work)
    names = review_custom_conditions(work / "input/markers.bim", (work / "input/conditions.txt").read_text().splitlines())
    plan = json.loads((work / "run.json").read_text())["plan"]
    if (plan.get("conditional_mode") != "single" or plan.get("conditions") != names
            or plan.get("data", {}).get("conditions") != names):
        raise ValueError("Condition markers differ from the reviewed single-pass plan.")
    return names


def custom_conditional_command(work, settings):
    work = Path(work).resolve()
    baseline = association_command(work, settings)
    return baseline[:-2] + ["--condition-list", str(work / "input/conditions.txt"),
                            "--out", str(work / "output/conditional")]


def checked_custom_conditional(work):
    work = Path(work).resolve()
    settings = settings_from_run(work, "hatk_node3_custom")
    names = read_custom_conditions(work)
    check_covariates(work, settings)
    text, samples = aligned_text(work, settings)
    if (work / "output/association.aligned.phe").read_text() != text:
        raise ValueError("Aligned phenotype differs from the supplied sample/phenotype data.")
    if (work / "output/association.effect-alleles.txt").read_text() != effect_text(work):
        raise ValueError("Effect alleles differ from the supplied BIM.")
    for filename, build in (("association", association_command), ("conditional", custom_conditional_command)):
        command = json.loads((work / f"output/{filename}.command.json").read_text())
        if command not in (build(work, settings), build('/work', settings)):
            raise ValueError(f"Recorded {filename} command differs from the reviewed analysis.")
    before, baseline_counts = association_rows(work / "output/association.assoc.logistic", work / "input/markers.bim",
                                              samples["samples"], exclude_invalid_stats=True)
    after, counts = association_rows(work / "output/conditional.assoc.logistic", work / "input/markers.bim",
                                     samples["samples"], names, exclude_invalid_stats=True)
    summary = {**samples, **counts, "phenotype_name": settings["phenotype_name"],
               "genome_build": settings["genome_build"], "mode": "single_conditional", "analysis_version": 1,
               "conditions": names, "condition_count": len(names), "conditional_passes": 1, "baseline_passes": 1,
               "baseline_counts": baseline_counts, "regression_engine": "PLINK 1.9 additive logistic",
               "baseline_engine": "fresh PLINK 1.9 scan on the same panel/phenotype",
               "covariates": settings.get("covariates", []),
               "scope": "supplied conditions; estimable marker tests; unestimable tests retained"}
    return summary, comparison_text(before, after, names)


def run_custom_conditional(work):
    work = Path(work).resolve()
    settings = settings_from_run(work, "hatk_node3_custom")
    read_custom_conditions(work)
    write_covariates(work, settings)
    (work / "output/association.aligned.phe").write_text(aligned_text(work, settings)[0])
    (work / "output/association.effect-alleles.txt").write_text(effect_text(work))
    for name, command in (("association", association_command(work, settings)),
                           ("conditional", custom_conditional_command(work, settings))):
        (work / f"output/{name}.command.json").write_text(json.dumps(command, indent=2) + "\n")
        subprocess.run(command, check=True)
    summary, comparison = checked_custom_conditional(work)
    (work / "output/conditional.comparison.tsv").write_text(comparison)
    (work / "output/conditional.summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def validate_custom_conditional_outputs(work):
    work = Path(work).resolve()
    summary, comparison = checked_custom_conditional(work)
    if (work / "output/conditional.comparison.tsv").read_text() != comparison:
        raise ValueError("Conditional comparison differs from the checked native results.")
    if json.loads((work / "output/conditional.summary.json").read_text()) != summary:
        raise ValueError("Conditional summary differs from the checked native results.")
    return summary
