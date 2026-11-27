"""RA example association checks and static HATK plots."""

from collections import Counter
import csv
import json
import logging
import math
from pathlib import Path
import re
import subprocess


GENES = ("A", "B", "C", "DPA1", "DPB1", "DQA1", "DQB1", "DRB1")
COLORS = {"SNP": "#8c8c8c", "HLA": "#d55e00", "AA": "#0072b2", "SNPS": "#009e73", "INS": "#cc79a7"}
AA_PATTERN = re.compile(r"^AA_([A-Z0-9]+)_(-?\d+)_(\d+)_(exon\d+)(?:_([A-Z.*-]))?$")


def phenotype_rows(fam, pheno, name="RA"):
    lines = [line.split() for line in Path(pheno).read_text().splitlines()]
    if (not lines or len(lines[0]) < 3 or lines[0][:2] != ["FID", "IID"]
            or len(set(lines[0])) != len(lines[0]) or name not in lines[0][2:]):
        raise ValueError(f"Expected FID IID header and a unique phenotype column {name}.")
    index = lines[0].index(name)
    values = {}
    for row in lines[1:]:
        if len(row) != len(lines[0]) or row[index] not in ("1", "2") or tuple(row[:2]) in values:
            raise ValueError("Phenotype requires unique FID/IID pairs and binary codes 1/2.")
        values[tuple(row[:2])] = row[index]
    result = []
    for line in Path(fam).read_text().splitlines():
        row = line.split()
        if len(row) != 6 or tuple(row[:2]) not in values:
            raise ValueError(f"A genotype sample has no matching {name} phenotype.")
        result.append(row[:2] + [values[tuple(row[:2])]])
    ids = [tuple(row[:2]) for row in result]
    if not result or len(ids) != len(set(ids)):
        raise ValueError("Empty or duplicate genotype samples.")
    counts = Counter(row[2] for row in result)
    if not counts["1"] or not counts["2"]:
        raise ValueError("Both controls and cases are required.")
    return result, {"samples": len(result), "controls": counts["1"], "cases": counts["2"]}


def marker_table(bim):
    markers = {}
    for line in Path(bim).read_text().splitlines():
        row = line.split()
        if len(row) != 6 or row[0] not in ("6", "chr6") or row[1] in markers:
            raise ValueError("Expected unique chromosome 6 BIM markers.")
        markers[row[1]] = {"BP": int(row[3]), "A1": row[4], "A2": row[5]}
    return markers


def association_rows(path, bim, sample_count, excluded=(), exclude_invalid_stats=False, allow_no_estimable=False):
    markers = marker_table(bim)
    excluded = set(excluded)
    if not excluded.issubset(markers):
        raise ValueError("Unknown conditioning marker.")
    with Path(path).open() as stream:
        header = stream.readline().split()
        if not {"CHR", "SNP", "BP", "A1", "TEST", "NMISS", "OR", "SE", "L95", "U95", "P"}.issubset(header):
            raise ValueError("Missing logistic association columns.")
        result = []
        seen = set()
        for line in stream:
            fields = line.split()
            if len(fields) != len(header):
                raise ValueError("Malformed association row.")
            row = dict(zip(header, fields))
            if row["TEST"] != "ADD":
                continue
            name = row["SNP"]
            if name in excluded:
                continue
            if name not in markers or name in seen:
                raise ValueError("Unexpected or duplicate additive marker result.")
            seen.add(name)
            marker = markers[name]
            if row["CHR"] != "6" or int(row["BP"]) != marker["BP"] or row["A1"].upper() != marker["A1"].upper():
                raise ValueError("Association position or effect allele differs from the reviewed BIM.")
            n = float(row["NMISS"])
            if not n.is_integer() or not 0 <= n <= sample_count:
                raise ValueError("Invalid association sample count.")
            p, effect, se, low, high = [_statistic(row[key]) for key in ("P", "OR", "SE", "L95", "U95")]
            estimated = all(math.isfinite(value) for value in (p, effect, se, low, high))
            invalid_stats = estimated and not (n > 0 and 0 <= p <= 1 and effect > 0 and se >= 0 and 0 < low <= high)
            if invalid_stats:
                if not exclude_invalid_stats:
                    raise ValueError("Invalid finite association statistics.")
                estimated = False
            row.update(A2=marker["A2"], ESTIMABLE=estimated,
                       EXCLUSION="" if estimated else "invalid_finite_statistics" if invalid_stats else "missing_or_nonfinite_statistics",
                       NEG_LOG10_P=-math.log10(max(p, 1e-300)) if estimated else None,
                       P_ZERO_CLIPPED=estimated and p == 0)
            result.append(row)
    if seen != set(markers) - excluded:
        raise ValueError("Association output does not cover every reviewed marker.")
    valid = [row for row in result if row["ESTIMABLE"]]
    if not valid and not allow_no_estimable:
        raise ValueError("No estimable additive association results.")
    return result, {"tested_markers": len(result), "estimable_markers": len(valid),
                    "unestimable_markers": len(result) - len(valid),
                    "zero_pvalues": sum(row["P_ZERO_CLIPPED"] for row in valid),
                    "minimum_p": min((float(row["P"]) for row in valid), default=None),
                    "maximum_nmiss": max((int(float(row["NMISS"])) for row in result), default=0)}


def _statistic(text):
    if text.upper() == "NA":
        return math.nan
    try:
        return float(text)
    except ValueError:
        raise ValueError("Invalid association statistic text.") from None


def aa_scores(rows):
    """Map single-residue and biallelic effect alleles without guessing direction."""
    scores = {}
    for row in rows:
        if not row["ESTIMABLE"] or not row["SNP"].startswith("AA_"):
            continue
        match = AA_PATTERN.fullmatch(row["SNP"])
        if not match:
            raise ValueError("Unrecognized amino acid marker label.")
        gene, position, bp, exon, residue = match.groups()
        effect = float(row["OR"])
        score = row["NEG_LOG10_P"] * (1 if effect > 1 else -1 if effect < 1 else 0)
        a1, a2 = row["A1"].upper(), row["A2"].upper()
        if residue:
            if {a1, a2} != {"P", "A"}:
                raise ValueError("A residue-presence marker requires P/A coding.")
            values = {residue: score if a1 == "P" else -score}
        else:
            if any(not re.fullmatch(r"[A-Z.*-]", a) for a in (a1, a2)) or a1 == a2:
                raise ValueError("Unexpected biallelic amino acid coding.")
            values = {a1: score, a2: -score}
        for char, value in values.items():
            key = (gene, position, bp, exon, char)
            if key in scores:
                raise ValueError("Multiple association values map to the same amino acid residue.")
            scores[key] = value
    return scores


def _save_figure(fig, prefix):
    fig.savefig(str(prefix) + ".png", dpi=300, bbox_inches="tight")
    fig.savefig(str(prefix) + ".pdf", bbox_inches="tight")


def plot_manhattan(work, rows, sample_summary, phenotype_name="RA", genome_build=18, output_name="RA"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    plt.rcParams["font.family"] = "Arial"
    try:
        font_manager.findfont("Arial", fallback_to_default=False)
        font_used = "Arial"
    except ValueError:
        plt.rcParams["font.family"] = ["Arial", "DejaVu Sans"]
        logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
        font_used = "DejaVu Sans (Arial unavailable)"
    work = Path(work)
    out = work / "output"
    valid = [row for row in rows if row["ESTIMABLE"]]
    fig, axis = plt.subplots(figsize=(12, 4.5))
    for kind, color in COLORS.items():
        selected = [row for row in valid if marker_class(row["SNP"]) == kind]
        if selected:
            axis.scatter([int(row["BP"]) / 1e6 for row in selected],
                         [row["NEG_LOG10_P"] for row in selected], s=6, alpha=0.65,
                         color=color, label=kind, linewidths=0, rasterized=True)
    threshold = -math.log10(0.05 / len(rows))
    axis.axhline(threshold, color="#333333", linestyle="--", linewidth=0.8,
                 label=f"Bonferroni: 0.05/{len(rows):,}")
    axis.set(xlabel=f"Chromosome 6 position (Mb, hg{genome_build})", ylabel="-log10(P)",
             title=f"{phenotype_name} association | {sample_summary['cases']} cases, {sample_summary['controls']} controls")
    axis.legend(frameon=False, fontsize=8, ncol=3)
    fig.tight_layout()
    _save_figure(fig, out / (output_name + ".manhattan"))
    plt.close(fig)

    return font_used


def plot_results(work, rows, sample_summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    work = Path(work)
    out = work / "output"
    font_used = plot_manhattan(work, rows, sample_summary)
    chped = pd.read_csv(work / "input/cohort.chped", sep=r"\s+", dtype=str)
    eligible = {tuple(line.split()[:2]) for line in (work / "input/markers.fam").read_text().splitlines()}
    chped = chped[[pair in eligible for pair in zip(chped["FID"], chped["IID"])]]
    scores = aa_scores(rows)
    panels = {}
    for gene in GENES:
        maptable = pd.read_csv(work / f"dictionary/HLA_MAPTABLE_{gene}.hg18.imgt3320.txt",
                               sep="\t", header=[0, 1, 2], index_col=0, dtype=str)
        if not maptable.index.is_unique:
            raise ValueError("Duplicate HLA alleles in the map table.")
        counts = Counter(chped[[f"{gene}_1", f"{gene}_2"]].values.flatten())
        counts.pop("0", None)
        alleles = sorted(counts, key=lambda a: (-counts[a], a))
        if any(a not in maptable.index for a in alleles):
            raise ValueError("An observed HLA allele is absent from the matching map table.")
        columns = [col for col in maptable.columns if any(key[:4] == (gene, *col) for key in scores)]
        columns.sort(key=lambda col: (int(col[0]), int(col[1])))
        matrix = np.full((len(alleles), len(columns)), np.nan)
        residues = maptable.loc[alleles, columns]
        for i, allele in enumerate(alleles):
            for j, column in enumerate(columns):
                matrix[i, j] = scores.get((gene, *column, str(residues.iloc[i, j])), np.nan)
        labels = ["|".join(col) for col in columns]
        pd.DataFrame(matrix, index=alleles, columns=labels).to_csv(out / f"RA.HLA-{gene}.scores.tsv", sep="\t", na_rep="NA")
        residues.to_csv(out / f"RA.HLA-{gene}.residues.tsv", sep="\t")
        # Keep full matrices; bound only the rendered view for legibility.
        ranked = sorted(range(len(columns)), key=lambda j: -max(
            (abs(v) for v in matrix[:, j] if math.isfinite(v)), default=0))[:30]
        selected_cols = sorted(ranked, key=lambda j: int(columns[j][0]))
        displayed = matrix[:30, selected_cols]
        fig, axis = plt.subplots(figsize=(max(7, len(selected_cols) * 0.32), max(3, min(30, len(alleles)) * 0.25 + 2)))
        if displayed.size and np.isfinite(displayed).any():
            limit = max(1, float(np.nanmax(np.abs(displayed))))
            cmap = plt.get_cmap("RdBu_r").copy()
            cmap.set_bad("#d9d9d9")
            mesh = axis.imshow(np.ma.masked_invalid(displayed), aspect="auto", cmap=cmap, vmin=-limit, vmax=limit)
            axis.set_xticks(range(len(selected_cols)), [columns[j][0] for j in selected_cols], rotation=90)
            axis.set_yticks(range(min(30, len(alleles))), alleles[:30])
            for i in range(displayed.shape[0]):
                for j, col_index in enumerate(selected_cols):
                    color = "white" if math.isfinite(displayed[i, j]) and abs(displayed[i, j]) > 0.55 * limit else "#222222"
                    axis.text(j, i, str(residues.iloc[i, col_index]), ha="center", va="center", fontsize=5, color=color)
            fig.colorbar(mesh, ax=axis, label="Signed -log10(P)")
        else:
            axis.text(0.5, 0.5, "No estimable amino acid associations", ha="center", va="center", transform=axis.transAxes)
            axis.set_xticks([])
            axis.set_yticks([])
        axis.tick_params(labelsize=7)
        axis.set(xlabel="Amino acid position", ylabel="Observed HLA allele",
                 title=f"HLA-{gene} | {len(selected_cols)} positions, {min(30, len(alleles))} observed alleles")
        fig.text(0.01, 0.01, "Red: higher RA odds; blue: lower odds. Grey: no estimable test.\n"
                 "Up to 30 positions ranked by signal and 30 alleles by count; full matrices exported.", fontsize=6.5)
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        _save_figure(fig, out / f"RA.HLA-{gene}.heatmap")
        plt.close(fig)
        panels[gene] = {"observed_alleles": len(alleles), "positions": len(columns),
                        "displayed_alleles": min(30, len(alleles)),
                        "displayed_positions": [labels[j] for j in selected_cols]}
    return {"font": font_used, "heatmaps": panels, "plot_p_floor": 1e-300,
            "bonferroni_tests": len(rows), "covariates": []}


def marker_class(name):
    return next((kind for kind in ("HLA", "AA", "SNPS", "INS") if name.startswith(kind + "_")), "SNP")


def run_association(work):
    work = Path(work)
    aligned, samples = phenotype_rows(work / "input/markers.fam", work / "input/RA.phe")
    out = work / "output"
    with (out / "RA.aligned.phe").open("w") as stream:
        stream.write("FID IID RA\n" + "\n".join(" ".join(row) for row in aligned) + "\n")
    markers = marker_table(work / "input/markers.bim")
    (out / "RA.effect-alleles.txt").write_text("\n".join(f"{name} {row['A1']}" for name, row in markers.items()) + "\n")
    command = ["plink", "--bfile", str(work / "input/markers"), "--pheno", str(out / "RA.aligned.phe"),
               "--pheno-name", "RA", "--logistic", "hide-covar", "--ci", "0.95", "--allow-no-sex",
               "--keep-allele-order", "--a1-allele", str(out / "RA.effect-alleles.txt"), "2", "1",
               "--out", str(out / "RA")]
    subprocess.run(command, check=True)
    rows, stats = association_rows(out / "RA.assoc.logistic", work / "input/markers.bim", samples["samples"])
    with (out / "RA.results.tsv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    plots = plot_results(work, rows, samples)
    (out / "RA.summary.json").write_text(json.dumps({"phenotype": samples, "association": stats,
                                                   "plots": plots, "plink_command": command}, indent=2) + "\n")


def validate_association_outputs(work):
    work = Path(work)
    _, samples = phenotype_rows(work / "input/markers.fam", work / "input/RA.phe")
    checked, stats = association_rows(work / "output/RA.assoc.logistic", work / "input/markers.bim", samples["samples"])
    for prefix in ["RA.manhattan"] + [f"RA.HLA-{gene}.heatmap" for gene in GENES]:
        for ext, signature in ((".png", b"\x89PNG\r\n\x1a\n"), (".pdf", b"%PDF-")):
            path = work / "output" / (prefix + ext)
            if not path.is_file() or path.stat().st_size < 100 or not path.read_bytes().startswith(signature):
                raise ValueError("Missing or invalid visualization: " + str(path))
    summary = json.loads((work / "output/RA.summary.json").read_text())
    if summary.get("phenotype") != samples or summary.get("association") != stats:
        raise ValueError("Association summary does not match the checked results.")
    with (work / "output/RA.results.tsv").open() as stream:
        normalized = list(csv.DictReader(stream, delimiter="\t"))
    if (len(normalized) != stats["tested_markers"] or {row["SNP"] for row in normalized} != {row["SNP"] for row in checked}
            or any(row["TEST"] != "ADD" for row in normalized)):
        raise ValueError("Normalized association table is incomplete or duplicated.")
    for gene in GENES:
        for kind in ("scores", "residues"):
            path = work / "output" / f"RA.HLA-{gene}.{kind}.tsv"
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("Missing heatmap matrix: " + str(path))
    return {**samples, **stats}
