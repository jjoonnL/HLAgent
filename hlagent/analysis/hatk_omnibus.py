"""Checked prephased fixture for the original HATK haplotype-row omnibus model."""

from collections import OrderedDict
import csv
import json
import math
from pathlib import Path
import re
import subprocess

from hlagent.analysis.hatk_association import phenotype_rows


MODEL = "original_HATK_haplotype_rows"


def read_phased(work, input_dir="input", phenotype_name="RA", phenotype_file="RA.phe", supplied=False):
    work = Path(work)
    inputs = work / input_dir
    return read_phased_files(inputs / "phased-aa.bgl", inputs / "cohort.fam",
                             inputs / phenotype_file, phenotype_name, supplied)


def insertion_coordinate(name):
    match = re.fullmatch(r"INS_AA_(A|B|C|DPA1|DPB1|DQA1|DQB1|DRB1)_(-?\d+)x(-?\d+)_(\d+)", name)
    if match is None or int(match[2]) >= int(match[3]) or int(match[4]) <= 0:
        raise ValueError("Unsupported amino-acid insertion identifier or coordinate.")
    return int(match[4])


def test_counts(positions):
    insertions = sum(name.startswith("INS_AA_") for name in positions)
    return {"aa_positions": len(positions) - insertions,
            "insertion_tests": insertions, "total_tests": len(positions)}


def read_phased_files(phased_file, fam_file, phenotype_file, phenotype_name="RA", supplied=True):
    aligned, samples = phenotype_rows(fam_file, phenotype_file, phenotype_name)
    fam = [line.split() for line in Path(fam_file).read_text().splitlines()]
    ids = [row[1] for row in fam]
    if (not supplied and any(row[0] != row[1] for row in fam)) or len(set(ids)) != len(ids):
        raise ValueError("Phased input requires unique IIDs; the bundled fixture also requires FID=IID.")
    groups = OrderedDict()
    with Path(phased_file).open() as stream:
        first = stream.readline().split()
        expected = [line.split() for line in residue_text(fam, {}).splitlines()]
        if supplied and first[:2] == ["P", "pedigree"]:
            if [first] + [stream.readline().split() for _ in range(4)] != expected:
                raise ValueError("Phased pedigree headers differ from the reviewed FAM order/metadata.")
        elif first != expected[1]:
            raise ValueError("Phased sample pairs differ from the reviewed FAM order.")
        names = set()
        for line in stream:
            fields = line.split()
            if len(fields) != 2 + len(ids) * 2 or fields[0] != "M" or fields[1] in names:
                raise ValueError("Malformed or duplicate phased marker row.")
            names.add(fields[1])
            if fields[1].startswith("INS_AA_"):
                bp = insertion_coordinate(fields[1])
                if any(value not in ("z", "Z", "0", "NA") for value in fields[2:]):
                    raise ValueError("Invalid insertion state. Expected case-sensitive z/Z or missing calls.")
                groups.setdefault(fields[1], []).append((bp, None, fields[2:]))
                continue
            match = re.fullmatch(r"(AA_[A-Z0-9]+_-?\d+)_(\d+)_exon\d+(?:_([A-Z.*-]+))?", fields[1])
            if match is None:
                raise ValueError("Unsupported amino-acid marker identifier.")
            name, bp, residue = match.groups()
            if supplied and name.split("_")[1] not in ("A", "B", "C", "DPA1", "DPB1", "DQA1", "DQB1", "DRB1"):
                raise ValueError("Unsupported classical HLA gene in phased AA marker.")
            groups.setdefault(name, []).append((int(bp), residue, fields[2:]))
    if not groups:
        raise ValueError("No phased amino-acid markers found.")
    positions = OrderedDict()
    for name, rows in groups.items():
        if len({row[0] for row in rows}) != 1:
            raise ValueError("An amino-acid position has inconsistent coordinates.")
        direct = [row for row in rows if row[1] is None]
        if direct:
            if len(rows) != 1:
                raise ValueError("Ambiguous direct and presence/absence residue coding.")
            calls = direct[0][2]
            pattern = r"[zZ]|0|NA" if name.startswith("INS_AA_") else r"[A-Z.*-]|0|NA"
            if any(not re.fullmatch(pattern, value) for value in calls):
                raise ValueError("Invalid direct residue call.")
            calls = ["NA" if value == "0" else value for value in calls]
        else:
            single = [row for row in rows if len(row[1]) == 1]
            if len(single) < 2 or len({row[1] for row in single}) != len(single):
                raise ValueError("Incomplete or duplicate single-residue marker definitions.")
            if any(value not in ("p", "a", "0", "NA") for row in rows for value in row[2]):
                raise ValueError("Invalid presence/absence residue call.")
            calls = []
            for index in range(2 * len(ids)):
                present = [row[1] for row in single if row[2][index] == "p"]
                complete = all(row[2][index] in ("p", "a") for row in single)
                calls.append(present[0] if complete and len(present) == 1 else "NA")
        if supplied and rows[0][0] <= 0:
            raise ValueError("Amino-acid positions must have positive chromosome 6 coordinates.")
        positions[name] = {"bp": rows[0][0], "calls": calls}
    return fam, aligned, samples, positions


def prepare_residues(work, settings=None):
    work = Path(work)
    name = settings["phenotype_name"] if settings else "RA"
    fam, aligned, samples, positions = read_phased(work, phenotype_name=name,
        phenotype_file="phenotype.txt" if settings else "RA.phe", supplied=settings is not None)
    (work / "output/residues.aa").write_text(residue_text(fam, positions))
    (work / f"output/{'omnibus' if settings else 'RA'}.aligned.phe").write_text("FID IID " + ("HLAgentTrait" if settings else name) + "\n" + "\n".join(" ".join(row) for row in aligned) + "\n")
    return samples, positions


def residue_text(fam, positions):
    headers = [("P", "pedigree", 0), ("I", "id", 1), ("fID", "father", 2),
               ("mID", "mother", 3), ("C", "gender", 4)]
    lines = [" ".join([kind, label] + [row[index] for row in fam for _ in (1, 2)])
             for kind, label, index in headers]
    lines += [" ".join(["M", name] + row["calls"]) for name, row in positions.items()]
    return "\n".join(lines) + "\n"


def checked_rows(work, settings=None):
    work = Path(work)
    name = settings["phenotype_name"] if settings else "RA"
    output = "omnibus" if settings else "RA.omnibus"
    fam, aligned, samples, positions = read_phased(work, phenotype_name=name,
        phenotype_file="phenotype.txt" if settings else "RA.phe", supplied=settings is not None)
    if (work / "output/residues.aa").read_text() != residue_text(fam, positions):
        raise ValueError("Decoded residues differ from the checked phased inputs.")
    expected_pheno = "FID IID " + ("HLAgentTrait" if settings else name) + "\n" + "\n".join(" ".join(row) for row in aligned) + "\n"
    if (work / f"output/{'omnibus' if settings else 'RA'}.aligned.phe").read_text() != expected_pheno:
        raise ValueError("Aligned phenotype differs from the checked sample identity.")
    with (work / f"output/{output}").open() as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        expected_columns = ["Variant", "deltaDeviance", "deltaDF", "N", "log10_P", "Residues"]
        if reader.fieldnames != expected_columns:
            raise ValueError("Unexpected omnibus result columns.")
        rows = list(reader)
    if any(set(row) != set(expected_columns) or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Malformed omnibus result row.")
    if len(rows) != len(positions) or {row.get("Variant") for row in rows} != set(positions):
        raise ValueError("Omnibus results do not cover each amino-acid position exactly once.")
    estimable, minimum_log10 = 0, 0.0
    for row in rows:
        calls = [value for value in positions[row["Variant"]]["calls"] if value != "NA"]
        residues = set(calls)
        if set(row["Residues"].split(",")) != residues:
            raise ValueError("Omnibus residues differ from the checked phased calls.")
        if len(residues) < 2:
            if any(row[key] not in ("NaN", "NA") for key in ("deltaDeviance", "deltaDF", "N", "log10_P")):
                raise ValueError("Monomorphic position has unexpected finite test statistics.")
            continue
        dev, df, n, logp = [float(row[key]) for key in ("deltaDeviance", "deltaDF", "N", "log10_P")]
        if (not all(math.isfinite(x) for x in (dev, df, n, logp)) or dev < -1e-8
                or df != len(residues) - 1 or n != len(calls) or logp > 1e-10):
            raise ValueError("Invalid omnibus deviance, degrees of freedom, haplotype count, or log10 P.")
        row["BP"] = positions[row["Variant"]]["bp"]
        estimable += 1
        minimum_log10 = min(minimum_log10, logp)
    if not estimable:
        raise ValueError("No estimable omnibus positions.")
    counts = test_counts(positions)
    # Preserve the summary schema used by existing AA-only result records.
    scope = counts if counts["insertion_tests"] else {}
    return rows, {**samples, **scope, "haplotype_rows": samples["samples"] * 2, "positions": len(rows),
                  "estimable_positions": estimable, "unestimable_positions": len(rows) - estimable,
                  "minimum_log10_p": minimum_log10, "model": MODEL}


def plot_omnibus(work, rows, phenotype_name="RA", genome_build=18, output="RA.omnibus"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    plt.rcParams["font.family"] = "Arial"
    font = "Arial"
    if not any(item.name == "Arial" for item in font_manager.fontManager.ttflist):
        font = "DejaVu Sans"
        plt.rcParams["font.family"] = ["Arial", font]
    valid = [row for row in rows if "BP" in row]
    fig, axis = plt.subplots(figsize=(10, 4))
    axis.scatter([row["BP"] / 1e6 for row in valid], [-float(row["log10_P"]) for row in valid], s=8, alpha=.7)
    axis.axhline(-math.log10(.05 / len(rows)), linestyle="--", color="grey", linewidth=.8)
    scope = "amino-acid and insertion" if any(row["Variant"].startswith("INS_AA_") for row in rows) else "amino-acid"
    axis.set(xlabel=f"Chromosome 6 position (hg{genome_build}, Mb)", ylabel="-log10(P)",
             title=f"{phenotype_name} {scope} omnibus test | original HATK haplotype-row model")
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(work / "output" / f"{output}.manhattan.{suffix}", dpi=300 if suffix == "png" else 180)
    plt.close(fig)
    return font


def run_omnibus(work, script):
    work = Path(work)
    prepare_residues(work)
    command = ["Rscript", str(script), str(work / "output/RA.omnibus"), str(work / "input/cohort.fam"),
               str(work / "output/residues.aa"), str(work / "output/RA.aligned.phe"), "RA", "NA", "NA", "NA"]
    with (work / "output/omnibus-r.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    rows, summary = checked_rows(work)
    font = plot_omnibus(work, rows)
    (work / "output/RA.omnibus.summary.json").write_text(json.dumps(
        {"summary": summary, "command": command, "font": font}, indent=2) + "\n")


def validate_omnibus_outputs(work):
    work = Path(work)
    _, summary = checked_rows(work)
    if json.loads((work / "output/RA.omnibus.summary.json").read_text()).get("summary") != summary:
        raise ValueError("Omnibus summary differs from the checked results.")
    for suffix, magic in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
        path = work / "output" / f"RA.omnibus.manhattan.{suffix}"
        if path.stat().st_size < 100 or not path.read_bytes().startswith(magic):
            raise ValueError("Missing or invalid omnibus plot.")
    return summary
