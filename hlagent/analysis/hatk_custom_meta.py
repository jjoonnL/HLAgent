"""Two-study fixed-effect IVW from supplied normalized binary-trait results."""

from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path

from hlagent.analysis.hatk_custom_association import check_settings
from hlagent.analysis.hatk_meta import fixed_effect, plot_meta, _same_float, _same_summary


MODEL = "supplied_two_study_fixed_effect_IVW"
INPUT_FIELDS = ("CHR", "SNP", "BP", "A1", "A2", "NMISS", "OR", "SE", "ESTIMABLE")
FIELDS = ("SNP", "BP", "A1", "A2") + tuple(
    f"STUDY{i}_{key}" for i in (1, 2) for key in ("BP", "A1", "A2", "NMISS", "OR", "SE", "ESTIMABLE")) + (
    "STUDY2_FLIPPED", "STUDY1_BETA", "STUDY2_ALIGNED_BETA", "META_BETA", "META_SE", "META_Z", "META_P", "EXCLUSION")
NUMERIC_FIELDS = {"STUDY1_BETA", "STUDY2_ALIGNED_BETA", "META_BETA", "META_SE", "META_Z", "META_P"}


def meta_settings(phenotype_name, genome_build, nonoverlap_confirmed):
    settings = check_settings(phenotype_name, genome_build)
    if nonoverlap_confirmed is not True:
        raise ValueError("Confirm that the two study samples do not overlap before preparing a meta-analysis.")
    return {**settings, "nonoverlap_confirmed": True}


def study_table(path):
    """Read Node2 normalized TSV columns; keep raw unestimable statistics."""
    table = {}
    with Path(path).open() as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if not header or len(header) != len(set(header)) or not set(INPUT_FIELDS).issubset(header):
            raise ValueError("Expected a normalized TSV with " + ", ".join(INPUT_FIELDS) + ".")
        for row in reader:
            if set(row) != set(header) or any(value is None for value in row.values()):
                raise ValueError("Malformed study result row.")
            if (row["CHR"] not in ("6", "chr6") or not row["SNP"] or any(c.isspace() for c in row["SNP"])
                    or row["SNP"] in table or row["ESTIMABLE"] not in ("True", "False")):
                raise ValueError("Study rows require unique chromosome 6 marker IDs and ESTIMABLE=True/False.")
            try:
                bp, n = int(row["BP"]), int(row["NMISS"])
            except ValueError:
                raise ValueError("Study BP and NMISS must be integers.") from None
            if bp <= 0 or n < 0 or any(not row[key] or row[key] in ("0", "NA")
                                      or any(c.isspace() for c in row[key]) for key in ("A1", "A2")):
                raise ValueError("Study rows need positive coordinates, nonnegative NMISS and explicit allele labels.")
            if row["A1"] == row["A2"]:
                raise ValueError("Effect and other allele labels must differ.")
            table[row["SNP"]] = row
    if not table:
        raise ValueError("Empty study result table.")
    return table


def usable(row):
    try:
        effect, error = float(row["OR"]), float(row["SE"])
    except ValueError:
        return False
    return (row["ESTIMABLE"] == "True" and int(row["NMISS"]) > 0
            and math.isfinite(effect) and effect > 0 and math.isfinite(error) and error > 0)


def combine_tables(first, second, settings):
    rows, valid, exclusions = [], [], Counter()
    flips = 0
    for name in sorted(set(first) | set(second)):
        a, b = first.get(name), second.get(name)
        reference = a if a is not None else b
        row = {"SNP": name, "BP": int(reference["BP"]), "A1": reference["A1"], "A2": reference["A2"]}
        reasons = []
        for i, native in enumerate((a, b), 1):
            if native is None:
                reasons.append(f"study{i}_missing_marker")
                continue
            row.update({f"STUDY{i}_{key}": native[key] for key in
                        ("BP", "A1", "A2", "NMISS", "OR", "SE", "ESTIMABLE")})
            if not usable(native):
                reasons.append(f"study{i}_unestimable_or_invalid_statistics")
        if a is not None and b is not None:
            if int(a["BP"]) != int(b["BP"]):
                reasons.append("coordinate_mismatch")
            pair1, pair2 = (a["A1"], a["A2"]), (b["A1"], b["A2"])
            if "." in pair1 + pair2 and not name.startswith(("AA_", "SNPS_", "INS_")):
                reasons.append("unknown_allele_label")
            if pair1 != pair2 and pair1 != pair2[::-1]:
                reasons.append("allele_mismatch_no_strand_inference")
            elif (not name.startswith(("AA_", "HLA_", "INS_"))
                  and set(pair1) in ({"A", "T"}, {"C", "G"})):
                reasons.append("strand_ambiguous_SNP")
            else:
                row["STUDY2_FLIPPED"] = pair1 == pair2[::-1]
        if not reasons:
            beta1 = math.log(float(a["OR"]))
            beta2 = math.log(float(b["OR"])) * (-1 if row["STUDY2_FLIPPED"] else 1)
            try:
                beta, se, z, p = fixed_effect(beta1, float(a["SE"]), beta2, float(b["SE"]))
            except (ValueError, OverflowError, ZeroDivisionError):
                reasons.append("nonfinite_meta_statistics")
            else:
                row.update(STUDY1_BETA=beta1, STUDY2_ALIGNED_BETA=beta2,
                           META_BETA=beta, META_SE=se, META_Z=z, META_P=p)
                flips += int(row["STUDY2_FLIPPED"])
                valid.append(row)
        row["EXCLUSION"] = ";".join(reasons)
        if reasons:
            exclusions[row["EXCLUSION"]] += 1
        rows.append(row)
    if not valid:
        raise ValueError("No markers remain after statistics, coordinate and allele checks.")
    summary = {"model": MODEL, "study_count": 2, "phenotype_name": settings["phenotype_name"],
               "genome_build": settings["genome_build"], "effect_scale": "log_odds_ratio",
               "study_marker_counts": [len(first), len(second)], "tested_markers": len(rows),
               "meta_markers": len(valid), "excluded_markers": len(rows) - len(valid),
               "exclusion_counts": dict(exclusions), "flipped_markers": flips,
               "minimum_p": min(row["META_P"] for row in valid),
               "zero_pvalues": sum(row["META_P"] == 0 for row in valid),
               "sample_overlap_assessment": "caller declared no overlap; not verified from summary tables",
               "trait_build_effect_compatibility": "caller declared; not independently verified",
               "regression_rerun": False}
    return rows, summary, valid


def settings_from_run(work):
    work = Path(work)
    settings = json.loads((work / "input/meta-settings.json").read_text())
    if not isinstance(settings, dict) or set(settings) != {"phenotype_name", "genome_build", "nonoverlap_confirmed"}:
        raise ValueError("Invalid meta-analysis settings file.")
    meta_settings(**settings)
    plan = json.loads((work / "run.json").read_text())["plan"]
    if (plan.get("example") != "hatk_node5_custom" or plan.get("data", {}).get("settings") != settings
            or plan.get("phenotype") != settings["phenotype_name"] or plan.get("model") != MODEL
            or plan.get("genome_build") != settings["genome_build"]):
        raise ValueError("Meta-analysis settings differ from the reviewed plan.")
    files = plan["data"].get("files", [])
    if len(files) != 2 or {item["path"] for item in files} != {"input/study1.tsv", "input/study2.tsv"}:
        raise ValueError("Missing supplied study input record.")
    for item in files:
        if hashlib.sha256((work / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Supplied study table differs from its reviewed hash.")
    return settings


def checked_custom_meta(work):
    work = Path(work)
    settings = settings_from_run(work)
    return combine_tables(study_table(work / 'input/study1.tsv'), study_table(work / 'input/study2.tsv'), settings)


def result_text(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def run_custom_meta(work):
    work = Path(work)
    rows, summary, valid = checked_custom_meta(work)
    (work / "output/meta.tsv").write_text(result_text(rows))
    font = plot_meta(work, valid, summary, summary["phenotype_name"], summary["genome_build"], "meta")
    (work / "output/meta.summary.json").write_text(json.dumps({"summary": summary, "plot_font": font}, indent=2) + "\n")


def validate_custom_meta_outputs(work):
    work = Path(work)
    rows, summary, _ = checked_custom_meta(work)
    expected = list(csv.reader(io.StringIO(result_text(rows)), delimiter="\t"))
    with (work / "output/meta.tsv").open() as stream:
        actual = list(csv.reader(stream, delimiter="\t"))
    if len(actual) != len(expected) or actual[0] != expected[0]:
        raise ValueError("Meta-analysis table header or row count differs from the checked inputs.")
    for saved, checked in zip(actual[1:], expected[1:]):
        if len(saved) != len(checked):
            raise ValueError("Malformed meta-analysis result row.")
        for column, a, e in zip(FIELDS, saved, checked):
            if not (_same_float(a, e) if column in NUMERIC_FIELDS and e != "" else a == e):
                raise ValueError(f"Meta-analysis result differs in column {column}.")
    if not _same_summary(json.loads((work / "output/meta.summary.json").read_text()).get("summary"), summary):
        raise ValueError("Meta-analysis summary differs from the checked inputs.")
    for suffix, magic in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
        data = (work / f"output/meta.manhattan.{suffix}").read_bytes()
        if len(data) < 100 or not data.startswith(magic):
            raise ValueError("Missing or invalid meta-analysis plot.")
    return summary
