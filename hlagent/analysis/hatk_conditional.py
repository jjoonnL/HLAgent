"""One conditional logistic regression on a reviewed Node2 marker panel."""

import csv
import io
import json
from pathlib import Path
import subprocess

from hlagent.analysis.hatk_association import association_rows, marker_table, phenotype_rows


COMPARISON_COLUMNS = ("SNP", "BP", "A1", "A2", "CONDITIONING_MARKER", "BASELINE_NMISS",
                      "BASELINE_OR", "BASELINE_P", "BASELINE_ESTIMABLE", "CONDITIONAL_NMISS",
                      "CONDITIONAL_OR", "CONDITIONAL_P", "CONDITIONAL_ESTIMABLE")


def review_conditions(baseline, bim, sample_count, requested=None):
    rows, _ = association_rows(baseline, bim, sample_count)
    available = {row["SNP"]: row for row in rows}
    if requested is None:
        requested = [min((row for row in rows if row["ESTIMABLE"]),
                         key=lambda row: (float(row["P"]), row["SNP"]))["SNP"]]
    if (not isinstance(requested, (list, tuple)) or not requested
            or any(not isinstance(name, str) for name in requested)
            or len(set(requested)) != len(requested)):
        raise ValueError("Provide a nonempty list of unique condition marker IDs.")
    for name in requested:
        if name not in available or not available[name]["ESTIMABLE"]:
            raise ValueError(f"Condition marker is missing or unestimable in Node2: {name}")
    if len(requested) == len(rows):
        raise ValueError("At least one marker must remain for testing.")
    return list(requested)


def read_conditions(work, samples):
    names = (work / "input/conditions.txt").read_text().splitlines()
    names = review_conditions(work / "input/baseline.assoc.logistic", work / "input/markers.bim",
                              samples["samples"], names)
    record_path = work / "run.json"
    if record_path.is_file():
        plan = json.loads(record_path.read_text()).get("plan", {})
        if plan.get("conditional_mode") != "single" or plan.get("conditions") != names:
            raise ValueError("Condition markers differ from the approved single-pass plan.")
    return names


def conditional_command(work):
    return ["plink", "--bfile", str(work / "input/markers"),
            "--pheno", str(work / "output/RA.aligned.phe"), "--pheno-name", "RA",
            "--logistic", "hide-covar", "--ci", "0.95", "--allow-no-sex", "--keep-allele-order",
            "--a1-allele", str(work / "output/RA.effect-alleles.txt"), "2", "1",
            "--condition-list", str(work / "input/conditions.txt"),
            "--out", str(work / "output/RA.conditional")]


def comparison_text(baseline, conditioned, conditions):
    after = {row["SNP"]: row for row in conditioned}
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(COMPARISON_COLUMNS)
    for before in baseline:
        name = before["SNP"]
        row = after.get(name)
        writer.writerow([name, before["BP"], before["A1"], before["A2"], name in conditions,
                         before["NMISS"], before["OR"], before["P"], before["ESTIMABLE"],
                         *([row["NMISS"], row["OR"], row["P"], row["ESTIMABLE"]] if row else ["", "", "", ""])])
    return stream.getvalue()


def checked_outputs(work, samples, conditions):
    before, baseline_counts = association_rows(work / "input/baseline.assoc.logistic",
                                              work / "input/markers.bim", samples["samples"])
    after, counts = association_rows(work / "output/RA.conditional.assoc.logistic",
                                     work / "input/markers.bim", samples["samples"], conditions,
                                     exclude_invalid_stats=True)
    summary = {**samples, **counts, "analysis_version": 4, "mode": "single_conditional",
               "conditions": conditions, "condition_count": len(conditions), "conditional_passes": 1,
               "regression_engine": "PLINK 1.9 additive logistic", "baseline_counts": baseline_counts,
               "scope": "estimable marker tests; unestimable tests retained"}
    return summary, comparison_text(before, after, conditions)


def run_conditional(work):
    work = Path(work).resolve()
    aligned, samples = phenotype_rows(work / "input/markers.fam", work / "input/RA.phe")
    conditions = read_conditions(work, samples)
    out = work / "output"
    (out / "RA.aligned.phe").write_text("FID IID RA\n" + "\n".join(" ".join(row) for row in aligned) + "\n")
    markers = marker_table(work / "input/markers.bim")
    (out / "RA.effect-alleles.txt").write_text("".join(f"{name} {row['A1']}\n" for name, row in markers.items()))
    command = conditional_command(work)
    (out / "RA.conditional.command.json").write_text(json.dumps(command, indent=2) + "\n")
    subprocess.run(command, check=True)
    summary, comparison = checked_outputs(work, samples, conditions)
    (out / "RA.comparison.tsv").write_text(comparison)
    (out / "RA.conditional.summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def validate_conditional_outputs(work):
    work = Path(work).resolve()
    summary = json.loads((work / "output/RA.conditional.summary.json").read_text())
    if summary.get("analysis_version", 1) != 4:
        if (work / "output/RA.conditional.command.json").exists():
            raise ValueError("Missing or invalid single-pass summary version.")
        record = work / "run.json"
        if record.is_file() and json.loads(record.read_text()).get("plan", {}).get("conditional_mode") == "single":
            raise ValueError("A stepwise result cannot complete a single-pass plan.")
        from archive.hatk_stepwise import validate_conditional_outputs as validate_stepwise
        return validate_stepwise(work)
    _, samples = phenotype_rows(work / "input/markers.fam", work / "input/RA.phe")
    conditions = read_conditions(work, samples)
    expected, comparison = checked_outputs(work, samples, conditions)
    if summary != expected:
        raise ValueError("Conditional summary differs from the checked single-pass results.")
    if (work / "output/RA.comparison.tsv").read_text() != comparison:
        raise ValueError("Before/after comparison differs from the checked regression results.")
    aligned, _ = phenotype_rows(work / "input/markers.fam", work / "input/RA.phe")
    if (work / "output/RA.aligned.phe").read_text() != "FID IID RA\n" + "\n".join(" ".join(row) for row in aligned) + "\n":
        raise ValueError("Aligned phenotype differs from the reviewed sample order.")
    markers = marker_table(work / "input/markers.bim")
    if (work / "output/RA.effect-alleles.txt").read_text() != "".join(f"{name} {row['A1']}\n" for name, row in markers.items()):
        raise ValueError("Effect allele file differs from BIM A1.")
    command = json.loads((work / "output/RA.conditional.command.json").read_text())
    if command not in (conditional_command(work), conditional_command(Path("/work"))):
        raise ValueError("Recorded command differs from the single-pass regression.")
    return expected
