"""Fixed-effect meta-analysis example from two nonoverlapping RA sample subsets."""

import csv
import io
import json
import logging
import math
from pathlib import Path
import subprocess

from hlagent.analysis.hatk_association import association_rows, marker_table, phenotype_rows


MODEL = "two_split_RA_fixed_effect_IVW"
SPLIT = "sort FID/IID within each phenotype, alternate into split1/split2"


def split_samples(work):
    work = Path(work).resolve()
    rows, samples = phenotype_rows(work / "input/markers.fam", work / "input/RA.phe")
    groups = [[], []]
    for phenotype in ("1", "2"):
        ordered = sorted((row for row in rows if row[2] == phenotype), key=lambda row: tuple(row[:2]))
        if len(ordered) < 2:
            raise ValueError("Each phenotype needs at least two samples for the split example.")
        for index, row in enumerate(ordered):
            groups[index % 2].append(row)
    counts = [{"samples": len(group), "controls": sum(row[2] == "1" for row in group),
               "cases": sum(row[2] == "2" for row in group)} for group in groups]
    return groups, samples, counts


def derived_texts(work):
    groups, _, _ = split_samples(work)
    markers = marker_table(Path(work) / "input/markers.bim")
    return {**{f"split{i}.keep": "".join(f"{r[0]} {r[1]}\n" for r in group)
               for i, group in enumerate(groups, 1)},
            "RA.aligned.phe": "FID IID RA\n" + "".join(" ".join(row) + "\n" for group in groups for row in group),
            "effect-alleles.txt": "".join(f"{name} {row['A1']}\n" for name, row in markers.items())}


def regression_command(work, split):
    work = Path(work).resolve()
    return ["plink", "--bfile", str(work / "input/markers"),
            "--keep", str(work / f"output/split{split}.keep"),
            "--pheno", str(work / "output/RA.aligned.phe"), "--pheno-name", "RA",
            "--logistic", "hide-covar", "--ci", "0.95", "--allow-no-sex", "--keep-allele-order",
            "--a1-allele", str(work / "output/effect-alleles.txt"), "2", "1",
            "--threads", "1", "--out", str(work / f"output/RA.split{split}")]


def fixed_effect(beta1, se1, beta2, se2):
    if not all(math.isfinite(x) for x in (beta1, se1, beta2, se2)) or min(se1, se2) <= 0:
        raise ValueError("Fixed-effect analysis needs finite log odds and positive standard errors.")
    # Scale the weights to avoid overflowing 1/SE squared for small SE.
    scale = min(se1, se2)
    weights = [(scale / se1) ** 2, (scale / se2) ** 2]
    beta = (weights[0] * beta1 + weights[1] * beta2) / sum(weights)
    se = scale / math.sqrt(sum(weights))
    z = beta / se
    if not all(math.isfinite(x) for x in (beta, se, z)) or se <= 0:
        raise ValueError("Nonfinite meta-analysis statistic.")
    return beta, se, z, math.erfc(abs(z) / math.sqrt(2))


def checked_meta(work):
    work = Path(work).resolve()
    groups, samples, counts = split_samples(work)
    for name, text in derived_texts(work).items():
        if (work / "output" / name).read_text() != text:
            raise ValueError("Split membership, phenotype, or effect alleles changed.")
    tables, stats = [], []
    for split in (1, 2):
        rows, summary = association_rows(work / f"output/RA.split{split}.assoc.logistic",
                                         work / "input/markers.bim", counts[split - 1]["samples"],
                                         exclude_invalid_stats=True)
        tables.append({r["SNP"]: r for r in rows})
        stats.append(summary)
        command = json.loads((work / f"output/RA.split{split}.command.json").read_text())
        if command not in (regression_command(work, split), regression_command(Path('/work'), split)):
            raise ValueError("Split regression command differs from the example specification.")
    header = ["SNP", "BP", "A1", "A2", "SPLIT1_N", "SPLIT1_BETA", "SPLIT1_SE",
              "SPLIT2_N", "SPLIT2_BETA", "SPLIT2_SE", "META_BETA", "META_SE", "META_Z", "META_P", "EXCLUSION"]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, header, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    valid = []
    exclusions = {}
    for name, marker in marker_table(work / "input/markers.bim").items():
        row = {"SNP": name, **marker}
        invalid = []
        betas, errors = [], []
        for split, table in enumerate(tables, 1):
            native = table[name]
            row[f"SPLIT{split}_N"] = native["NMISS"]
            if native["ESTIMABLE"] and float(native["SE"]) > 0:
                beta, se = math.log(float(native["OR"])), float(native["SE"])
                row[f"SPLIT{split}_BETA"], row[f"SPLIT{split}_SE"] = beta, se
                betas.append(beta)
                errors.append(se)
            else:
                invalid.append(f"split{split}_unestimable_or_nonpositive_se")
        row["EXCLUSION"] = ";".join(invalid)
        if invalid:
            exclusions[row["EXCLUSION"]] = exclusions.get(row["EXCLUSION"], 0) + 1
        else:
            beta, se, z, p = fixed_effect(betas[0], errors[0], betas[1], errors[1])
            row.update(META_BETA=beta, META_SE=se, META_Z=z, META_P=p)
            valid.append(row)
        writer.writerow(row)
    if not valid:
        raise ValueError("No markers have estimable positive-SE results in both splits.")
    summary = {**samples, "model": MODEL, "study_count": 2, "sample_overlap": 0,
               "split_method": SPLIT, "split_counts": counts, "split_association_counts": stats,
               "tested_markers": len(tables[0]), "meta_markers": len(valid),
               "excluded_markers": len(tables[0]) - len(valid), "exclusion_counts": exclusions,
               "minimum_p": min(r["META_P"] for r in valid),
               "zero_pvalues": sum(r["META_P"] == 0 for r in valid)}
    return stream.getvalue(), summary, valid


def plot_meta(work, valid, summary, phenotype_name="RA", genome_build=18, output="RA.meta"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    plt.rcParams['font.family'] = 'Arial'
    try:
        font_manager.findfont("Arial", fallback_to_default=False)
        font = "Arial"
    except ValueError:
        plt.rcParams['font.family'] = ['Arial', 'DejaVu Sans']
        logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
        font = "DejaVu Sans (Arial unavailable)"
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.scatter([r["BP"] / 1e6 for r in valid],
               [-math.log10(max(r["META_P"], 1e-300)) for r in valid],
               s=5, alpha=.65, linewidths=0, rasterized=True)
    ax.axhline(-math.log10(.05 / summary["tested_markers"]), color="#333333", linestyle="--",
               linewidth=.8, label=f"Bonferroni: 0.05/{summary['tested_markers']:,}")
    ax.set(xlabel=f"Chromosome 6 position (Mb, hg{genome_build})", ylabel="-log10(meta P)",
           title=("RA fixed-effect meta-analysis | two nonoverlapping example subsets" if output == "RA.meta"
                  else f"{phenotype_name} fixed-effect meta-analysis | two supplied studies"))
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    for extension in ("png", "pdf"):
        fig.savefig(Path(work) / f"output/{output}.manhattan.{extension}", dpi=300 if extension == "png" else 180, bbox_inches="tight")
    plt.close(fig)
    return font


def run_meta(work):
    work = Path(work).resolve()
    for name, text in derived_texts(work).items():
        (work / "output" / name).write_text(text)
    for split in (1, 2):
        command = regression_command(work, split)
        (work / f"output/RA.split{split}.command.json").write_text(json.dumps(command, indent=2) + "\n")
        subprocess.run(command, check=True)
    text, summary, valid = checked_meta(work)
    (work / "output/RA.meta.tsv").write_text(text)
    font = plot_meta(work, valid, summary)
    (work / "output/RA.meta.summary.json").write_text(json.dumps({"summary": summary, "plot_font": font}, indent=2) + "\n")


def _same_float(actual, expected):
    try:
        value = float(actual)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and math.isclose(value, float(expected), rel_tol=1e-12, abs_tol=0.0)


def _same_summary(actual, expected):
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and actual.keys() == expected.keys()
                and all(_same_summary(actual[key], value) for key, value in expected.items()))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_same_summary(a, e) for a, e in zip(actual, expected)))
    if isinstance(expected, float):
        return type(actual) in (int, float) and _same_float(actual, expected)
    return type(actual) is type(expected) and actual == expected


def validate_meta_outputs(work):
    work = Path(work).resolve()
    text, summary, _ = checked_meta(work)
    expected = list(csv.reader(io.StringIO(text), delimiter="\t"))
    with (work / "output/RA.meta.tsv").open() as stream:
        actual = list(csv.reader(stream, delimiter="\t"))
    if len(actual) != len(expected) or actual[0] != expected[0]:
        raise ValueError("Meta-analysis table header or row count differs from the checked split results.")
    numeric = {"SPLIT1_BETA", "SPLIT1_SE", "SPLIT2_BETA", "SPLIT2_SE",
               "META_BETA", "META_SE", "META_Z", "META_P"}
    for row_number, (saved, checked) in enumerate(zip(actual[1:], expected[1:]), 2):
        if len(saved) != len(checked):
            raise ValueError(f"Malformed meta-analysis table row {row_number}.")
        for column, a, e in zip(expected[0], saved, checked):
            # Counts, IDs, alleles, order, exclusions and empty cells stay exact.
            matches = _same_float(a, e) if column in numeric and e != "" else a == e
            if not matches:
                raise ValueError(f"Meta-analysis table differs at row {row_number}, column {column}.")
    saved_summary = json.loads((work / "output/RA.meta.summary.json").read_text()).get("summary")
    if not _same_summary(saved_summary, summary):
        raise ValueError("Meta-analysis summary differs from the checked results.")
    for extension, magic in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
        data = (work / f"output/RA.meta.manhattan.{extension}").read_bytes()
        if not data.startswith(magic) or len(data) < 100:
            raise ValueError("Missing or invalid meta-analysis plot.")
    return summary
