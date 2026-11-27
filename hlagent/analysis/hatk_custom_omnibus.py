"""Checked supplied phased AA inputs for the original HATK omnibus model."""

from hlagent.paths import ROOT

import hashlib
import json
from pathlib import Path
import subprocess

from hlagent.analysis.hatk_custom_association import check_settings
from hlagent.analysis.hatk_omnibus import MODEL, prepare_residues, checked_rows, plot_omnibus, test_counts


def settings_from_run(work):
    work = Path(work)
    settings = json.loads((work / "input/analysis.json").read_text())
    if not isinstance(settings, dict) or set(settings) != {"phenotype_name", "genome_build"}:
        raise ValueError("Invalid omnibus settings file.")
    check_settings(**settings)
    plan = json.loads((work / "run.json").read_text())["plan"]
    if (plan.get("example") != "hatk_node4_custom" or plan.get("data", {}).get("settings") != settings
            or plan.get("phenotype") != settings["phenotype_name"]
            or plan.get("genome_build") != settings["genome_build"] or plan.get("model") != MODEL
            or plan.get("covariates") != [] or plan.get("conditions") != []):
        raise ValueError("Omnibus settings differ from the reviewed plan.")
    files = plan["data"].get("files", [])
    required = {"input/cohort.fam", "input/phased-aa.bgl", "input/phenotype.txt"}
    if len(files) != 3 or {item["path"] for item in files} != required:
        raise ValueError("Missing supplied omnibus input record.")
    for item in files:
        if hashlib.sha256((work / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Supplied omnibus input differs from its reviewed hash.")
    return settings


def omnibus_command(work, script):
    work = Path(work).resolve()
    return ["Rscript", str(script), str(work / "output/omnibus"), str(work / "input/cohort.fam"),
            str(work / "output/residues.aa"), str(work / "output/omnibus.aligned.phe"),
            "HLAgentTrait", "NA", "NA", "NA"]


def checked_custom_omnibus(work):
    settings = settings_from_run(work)
    rows, summary = checked_rows(work, settings)
    counts = json.loads((Path(work) / 'run.json').read_text())['plan']['data'].get('test_counts')
    if counts is not None and counts != test_counts({row['Variant']: None for row in rows}):
        raise ValueError('Omnibus test scope differs from the reviewed plan.')
    summary.update(**settings, covariates=[], conditions=[], new_phasing=False,
                   within_person_dependence_adjusted=False)
    return rows, summary


def run_custom_omnibus(work, script):
    work = Path(work).resolve()
    settings = settings_from_run(work)
    prepare_residues(work, settings)
    command = omnibus_command(work, script)
    with (work / "output/omnibus-r.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    rows, summary = checked_custom_omnibus(work)
    font = plot_omnibus(work, rows, settings["phenotype_name"], settings["genome_build"], "omnibus")
    (work / "output/omnibus.summary.json").write_text(json.dumps(
        {"summary": summary, "command": command, "font": font}, indent=2) + "\n")


def validate_custom_omnibus_outputs(work):
    work = Path(work).resolve()
    _, summary = checked_custom_omnibus(work)
    saved = json.loads((work / "output/omnibus.summary.json").read_text())
    if saved.get("summary") != summary:
        raise ValueError("Omnibus summary differs from the checked results.")
    script = ROOT / "vendor/hatk/src/tools/HATK/OmnibusTest/OmnibusTest_BHv5.R"
    command = saved.get("command")
    if command not in (omnibus_command(work, script), omnibus_command(
            "/work", "/opt/hlagent/vendor/hatk/src/tools/HATK/OmnibusTest/OmnibusTest_BHv5.R")):
        raise ValueError("Omnibus command differs from the reviewed model/inputs.")
    for suffix, magic in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
        path = work / f"output/omnibus.manhattan.{suffix}"
        if path.stat().st_size < 100 or not path.read_bytes().startswith(magic):
            raise ValueError("Missing or invalid omnibus plot.")
    return summary
