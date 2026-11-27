"""Run binary-trait association on a supplied chromosome 6 marker panel."""

import csv
import io
import hashlib
import json
from pathlib import Path
import re
import subprocess

from hlagent.analysis.covariates import aligned_covariates, covariate_names
from hlagent.analysis.hatk_association import association_rows, marker_table, phenotype_rows, plot_manhattan


FIELDS = ("CHR", "SNP", "BP", "A1", "A2", "TEST", "NMISS", "OR", "SE", "L95", "U95", "P", "ESTIMABLE", "EXCLUSION")


def check_settings(phenotype_name, genome_build):
    if not isinstance(phenotype_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", phenotype_name):
        raise ValueError("Use a phenotype column name starting with a letter/underscore, followed by letters, digits, _, . or -.")
    if type(genome_build) is not int or genome_build not in (18, 19, 38):
        raise ValueError("Specify the panel genome build as 18, 19, or 38.")
    return {"phenotype_name": phenotype_name, "genome_build": genome_build}


def settings_from_run(work, expected_stage="hatk_node2_custom"):
    path = Path(work) / "input/analysis.json"
    settings = json.loads(path.read_text())
    if not isinstance(settings, dict) or set(settings) not in ({"phenotype_name", "genome_build"}, {"phenotype_name", "genome_build", "covariates"}):
        raise ValueError("Invalid association settings file.")
    check_settings(settings["phenotype_name"], settings["genome_build"])
    if "covariates" in settings:
        covariate_names(settings["covariates"], settings["phenotype_name"])
    plan = json.loads((Path(work) / "run.json").read_text())["plan"]
    if (plan.get("example") != expected_stage or plan.get("data", {}).get("settings") != settings
            or plan.get("phenotype") != settings["phenotype_name"]
            or plan.get("genome_build") != settings["genome_build"]):
        raise ValueError("Association settings differ from the reviewed plan.")
    files = plan["data"].get("files", [])
    required = {"input/markers.bed", "input/markers.bim", "input/markers.fam", "input/phenotype.txt"}
    if settings.get("covariates"):
        required.add("input/covariates.txt")
        if plan.get("covariates") != settings["covariates"]:
            raise ValueError("Covariates differ from the reviewed plan.")
    if len(files) != len(required) or {item["path"] for item in files} != required:
        raise ValueError("Missing supplied association input record.")
    for item in files:
        if hashlib.sha256((Path(work) / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Supplied association input differs from its reviewed hash.")
    return settings


def aligned_text(work, settings):
    rows, counts = phenotype_rows(Path(work) / "input/markers.fam", Path(work) / "input/phenotype.txt",
                                  settings["phenotype_name"])
    return "FID IID " + settings["phenotype_name"] + "\n" + "".join(" ".join(row) + "\n" for row in rows), counts


def effect_text(work):
    return "".join(f"{name} {marker['A1']}\n" for name, marker in marker_table(Path(work) / "input/markers.bim").items())


def association_command(work, settings):
    work = Path(work).resolve()
    command = ["plink", "--bfile", str(work / "input/markers"),
            "--pheno", str(work / "output/association.aligned.phe"), "--pheno-name", settings["phenotype_name"],
            "--logistic", "hide-covar", "--ci", "0.95", "--allow-no-sex", "--keep-allele-order",
            "--a1-allele", str(work / "output/association.effect-alleles.txt"), "2", "1",
            "--threads", "1"]
    if settings.get("covariates"):
        command += ["--covar", str(work / "output/association.aligned.cov"),
                    "--covar-name", ",".join(settings["covariates"])]
    return command + ["--out", str(work / "output/association")]


def covariate_text(work, settings):
    if not settings.get('covariates'):
        return None
    return aligned_covariates(Path(work) / 'input/markers.fam', Path(work) / 'input/covariates.txt',
                              settings['covariates'], settings['phenotype_name'])


def write_covariates(work, settings):
    text = covariate_text(work, settings)
    if text is not None:
        (Path(work) / 'output/association.aligned.cov').write_text(text)


def check_covariates(work, settings):
    text = covariate_text(work, settings)
    if text is not None and (Path(work) / 'output/association.aligned.cov').read_text() != text:
        raise ValueError('Aligned covariates differ from the reviewed inputs or FAM sample order.')


def normalized_text(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, FIELDS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def checked_custom_association(work):
    work = Path(work).resolve()
    settings = settings_from_run(work)
    aligned, samples = aligned_text(work, settings)
    check_covariates(work, settings)
    if (work / "output/association.aligned.phe").read_text() != aligned:
        raise ValueError("Aligned phenotype differs from the supplied phenotype/sample IDs.")
    if (work / "output/association.effect-alleles.txt").read_text() != effect_text(work):
        raise ValueError("Effect alleles differ from the supplied BIM.")
    command = json.loads((work / "output/association.command.json").read_text())
    if command not in (association_command(work, settings), association_command('/work', settings)):
        raise ValueError("Association command differs from the reviewed settings.")
    rows, stats = association_rows(work / "output/association.assoc.logistic", work / "input/markers.bim",
                                  samples["samples"], exclude_invalid_stats=True)
    return rows, {**samples, **stats, "phenotype_name": settings["phenotype_name"],
                  "genome_build": settings["genome_build"], "regression_engine": "PLINK 1.9 additive logistic",
                  "covariates": settings.get("covariates", []), "heatmaps": False}


def run_custom_association(work):
    work = Path(work).resolve()
    settings = settings_from_run(work)
    aligned, _ = aligned_text(work, settings)
    write_covariates(work, settings)
    (work / "output/association.aligned.phe").write_text(aligned)
    (work / "output/association.effect-alleles.txt").write_text(effect_text(work))
    command = association_command(work, settings)
    (work / "output/association.command.json").write_text(json.dumps(command, indent=2) + "\n")
    subprocess.run(command, check=True)
    rows, summary = checked_custom_association(work)
    (work / "output/association.results.tsv").write_text(normalized_text(rows))
    font = plot_manhattan(work, rows, summary, settings["phenotype_name"], settings["genome_build"], "association")
    (work / "output/association.summary.json").write_text(json.dumps({"summary": summary, "plot_font": font}, indent=2) + "\n")


def validate_custom_association_outputs(work):
    work = Path(work).resolve()
    rows, summary = checked_custom_association(work)
    if (work / "output/association.results.tsv").read_text() != normalized_text(rows):
        raise ValueError("Normalized association table differs from the native results.")
    if json.loads((work / "output/association.summary.json").read_text()).get("summary") != summary:
        raise ValueError("Association summary differs from the checked results.")
    for extension, signature in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
        path = work / f"output/association.manhattan.{extension}"
        if not path.is_file() or path.stat().st_size < 100 or not path.read_bytes().startswith(signature):
            raise ValueError("Missing or invalid association Manhattan plot.")
    return summary
