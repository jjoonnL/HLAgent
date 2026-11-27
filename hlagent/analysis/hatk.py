"""Plan, execute, and check the bundled HATK Node1 example."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from typing import Optional

from hlagent.analysis.hatk_phasing import phase_settings, phase_inputs, validate_phasing_outputs, checked_omnibus_provenance
from hlagent.analysis.cookhla_inputs import check_plink
from hlagent.analysis.example_data import ensure_examples
from hlagent.analysis.hatk_worker import GENES, QC
from hlagent.analysis.hatk_association import marker_table, phenotype_rows, validate_association_outputs
from hlagent.analysis.covariates import aligned_covariates, covariate_names, plan_covariates
from hlagent.analysis.hatk_custom_association import check_settings, validate_custom_association_outputs
from hlagent.analysis.hatk_stepwise import selection_settings, validate_stepwise_outputs
from hlagent.analysis.stepwise_firth import panel_selection, selection_arguments
from hlagent.analysis.hatk_custom_conditional import review_custom_conditions, validate_custom_conditional_outputs
from hlagent.analysis.hatk_custom_markers import pedigree, marker_settings, dictionary_names, check_marker_inputs, validate_custom_marker_outputs
from hlagent.analysis.hatk_conditional import review_conditions, validate_conditional_outputs
from hlagent.analysis.hatk_meta import MODEL as META_MODEL, SPLIT as META_SPLIT, split_samples, validate_meta_outputs
from hlagent.analysis.hatk_omnibus import MODEL as OMNIBUS_MODEL, read_phased, read_phased_files, test_counts, validate_omnibus_outputs
from hlagent.analysis.hatk_custom_omnibus import validate_custom_omnibus_outputs
from hlagent.analysis.hatk_custom_meta import MODEL as CUSTOM_META_MODEL, meta_settings, study_table, combine_tables, validate_custom_meta_outputs


from hlagent.paths import ROOT, image_source_hashes, new_run_id
DATA = ROOT / "datasets/examples/hatk"
SOURCE = ROOT / "vendor/hatk"
IMAGE = "hlagent-hatk:layout-v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_manifest(root):
    if root == DATA or DATA in root.parents:
        ensure_examples('hatk', IMAGE, DATA)
    manifest = json.loads((root / "sources.json").read_text())
    for item in manifest["files"]:
        path = root / item["path"]
        if not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError(f"Bundled file missing or changed: {path}")
    return manifest


def check_example():
    manifest = checked_manifest(DATA)
    checked_manifest(SOURCE)
    target = check_plink(DATA / "input/genotypes")
    rows = pedigree(DATA / "input/cohort.hped")
    fam = [line.split() for line in (DATA / "input/genotypes.fam").read_text().splitlines()]
    if [row[:6] for row in rows] != fam:
        raise ValueError("Example HPED metadata/order differs from the genotype FAM.")
    return manifest, target


@dataclass(frozen=True)
class Node1Plan:
    run_dir: Path
    data: dict
    source_hashes: dict
    seccomp_unconfined: bool = False
    parent_run: Optional[str] = None

    @property
    def dictionary(self):
        return self.data.get('stage') == 'imgt2seq'

    @property
    def phasing(self):
        return self.data.get("stage") == "phasing"

    @property
    def custom_meta(self):
        return self.data.get("stage") == "node5_custom"

    @property
    def custom_omnibus(self):
        return self.data.get("stage") == "node4_custom"

    @property
    def custom_markers(self):
        return self.data.get("stage") == "node1_custom"

    @property
    def custom_conditional(self):
        return self.data.get("stage") == "node3_custom"

    @property
    def stepwise(self):
        return self.data.get("stage") == "stepwise"

    @property
    def custom_association(self):
        return self.data.get("stage") == "node2_custom"

    @property
    def meta(self):
        return self.data.get("stage") == "node5"

    @property
    def omnibus(self):
        return self.data.get("stage") == "node4"

    @property
    def conditional(self):
        return self.data.get("stage") == "node3"

    def command(self, image=IMAGE):
        command = ["docker", "run", "--rm", "--name", self.run_dir.name,
                   "--user", f"{os.getuid()}:{os.getgid()}",
                   "--mount", f"type=bind,source={self.run_dir},target=/work"]
        if self.seccomp_unconfined:
            command += ["--security-opt", "seccomp=unconfined"]
        if self.dictionary:
            return command + ['--network', 'none', image, '--imgt2seq']
        return command + [image, "--stepwise" if self.stepwise else "--phasing" if self.phasing else "--meta-custom" if self.custom_meta else "--omnibus-custom" if self.custom_omnibus else "--markers-custom" if self.custom_markers else "--conditional-custom" if self.custom_conditional else "--association-custom" if self.custom_association else "--meta" if self.meta else "--omnibus" if self.omnibus else "--conditional" if self.conditional else "--association" if self.parent_run else "--run"]

    def to_dict(self):
        if self.dictionary:
            return {'example': 'hatk_imgt2seq', 'genome_build': 18, 'imgt': self.data['settings']['imgt'], 'field_format': 2,
                    'genes': list(GENES), 'dictionary_mode': 'generated_from_local_snapshot',
                    'run_dir': str(self.run_dir), 'data': self.data, 'source_sha256': self.source_hashes,
                    'image': IMAGE, 'seccomp_unconfined': self.seccomp_unconfined, 'command': self.command()}
        details = {"example": "hatk_node1", "genome_build": 18, "imgt": "3320", "field_format": 2,
                "genes": list(GENES), "dictionary_mode": "bundled_existing", "qc": QC,
                "run_dir": str(self.run_dir), "data": self.data, "source_sha256": self.source_hashes,
                "image": IMAGE, "seccomp_unconfined": self.seccomp_unconfined, "command": self.command()}
        if self.parent_run:
            details.pop("qc")
            details.update(example="hatk_node2", parent_run=self.parent_run, phenotype="RA",
                           regression="logistic", covariates=[], parent_qc=self.data.get("parent_qc"),
                           visualization="Manhattan and eight AA heatmaps")
        if self.conditional:
            details.update(example="hatk_node3", conditional_mode="single", conditions=self.data["conditions"],
                           condition_selection=self.data["condition_selection"], conditional_analysis_version=4,
                           regression_engine="PLINK 1.9 additive logistic",
                           baseline_engine="reused Node2 PLINK 1.9 logistic",
                           visualization=None, comparison="baseline vs conditional association table")
        if self.omnibus or self.custom_omnibus:
            for key in ("qc", "imgt", "field_format", "dictionary_mode", "genes"):
                details.pop(key, None)
            details.update(example="hatk_node4", model=OMNIBUS_MODEL, phenotype="RA",
                           regression="haplotype-row binomial LRT", covariates=[], conditions=[],
                           phasing="bundled existing phased AA markers; no new phasing",
                           build_provenance="hg18 declared by original example; not independently verified",
                           visualization="Omnibus Manhattan PNG/PDF")
        if self.custom_omnibus:
            details.update(example="hatk_node4_custom", genome_build=self.data["settings"]["genome_build"],
                           phenotype=self.data["settings"]["phenotype_name"], input_mode="supplied_phased_AA",
                           phasing="supplied phased AA markers; no new phasing",
                           build_provenance="caller declared; not independently verified",
                           within_person_dependence_adjusted=False)
            if self.data.get('test_counts', {}).get('insertion_tests'):
                details.update(input_mode="supplied_phased_AA_and_insertions",
                               phasing="supplied phased AA and insertion markers; no new phasing")
        if self.meta:
            details.pop("visualization", None)
            details.update(example="hatk_node5", model=META_MODEL, split_method=META_SPLIT,
                           study_count=2, study_design="nonoverlapping subsets of one RA example; not independent replication cohorts",
                           regression="two additive logistic scans then fixed-effect IVW",
                           visualization="Meta-analysis Manhattan PNG/PDF")
        if self.custom_meta:
            for key in ("qc", "imgt", "field_format", "dictionary_mode", "genes"):
                details.pop(key, None)
            details.update(example="hatk_node5_custom", model=CUSTOM_META_MODEL,
                           phenotype=self.data["settings"]["phenotype_name"], genome_build=self.data["settings"]["genome_build"],
                           input_mode="supplied_normalized_association_tables", study_count=2,
                           effect_scale="log_odds_ratio", sample_overlap_assessment="caller declared; not verified",
                           visualization="Fixed-effect Manhattan PNG/PDF")
        if self.custom_association or self.custom_conditional or self.stepwise:
            for key in ("imgt", "field_format", "genes", "dictionary_mode", "qc"):
                details.pop(key, None)
            details.update(example="hatk_node2_custom", genome_build=self.data["settings"]["genome_build"],
                           phenotype=self.data["settings"]["phenotype_name"], regression="logistic",
                           covariates=self.data["settings"].get("covariates", []),
                           input_mode="supplied_marker_panel", visualization="Manhattan PNG/PDF; no heatmaps",
                           qc="no additional QC; input preparation is caller supplied")
        if self.custom_conditional:
            details.update(example="hatk_node3_custom", conditional_mode="single", conditions=self.data["conditions"],
                           conditional_analysis_version=1, regression_engine="PLINK 1.9 additive logistic",
                           baseline_engine="fresh PLINK 1.9 scan on the same panel/phenotype",
                           visualization=None, comparison="matched fresh baseline vs supplied-condition association")
        if self.stepwise:
            version = self.data.get('stepwise_analysis_version', 2)
            details.update(example="hatk_stepwise", conditional_mode="stepwise", selection=self.data["selection"], stepwise_analysis_version=version,
                           regression_engine="PLINK 2 always-Firth additive logistic" if version == 3 else "PLINK 1.9 additive logistic", visualization=None)
            if version == 3:
                details['qc'] = 'candidate MAC eligibility only. No genotype QC rerun'
        if self.custom_markers:
            details.update(example="hatk_node1_custom", genome_build=self.data["settings"]["genome_build"],
                           imgt=self.data["settings"]["imgt"], dictionary_mode="supplied_existing_two_field",
                           input_mode="supplied_HPED", field_format=2, qc=QC,
                           merge_supplied_snps=self.data["snp_prefix"] is not None)
        if self.phasing:
            for key in ("qc", "imgt", "field_format", "dictionary_mode", "genes"):
                details.pop(key, None)
            details.update(example="hatk_phasing", genome_build=self.data["settings"]["genome_build"],
                           engine="BEAGLE 3.3.2", settings=self.data["settings"],
                           input_mode="unphased_marker_panel", visualization=None)
        return details


def _source_hashes():
    hashes = image_source_hashes()
    for path in sorted((ROOT / "hlagent").rglob("*.py")):
        hashes[str(path.relative_to(ROOT))] = digest(path)
    for name in (".dockerignore", "docker/hatk/Dockerfile", "docker/hatk/requirements.txt",
                 "docker/hatk/install.sh", "docker/hatk/build_legacy.sh",
                 "docker/examples/hatk.Dockerfile", "docker/examples/cookhla.Dockerfile"):
        hashes[name] = digest(ROOT / name)
    for path in sorted((ROOT / 'examples/manifests').rglob('sources.json')):
        hashes[str(path.relative_to(ROOT))] = digest(path)
    return hashes


def prepare_dictionary(source_dir, run_root=None, seccomp_unconfined=False):
    from hlagent.analysis.imgt_dictionary import source_settings, source_files
    checked_manifest(SOURCE)
    settings = source_settings(source_dir)
    data = {'stage': 'imgt2seq', 'source_dir': str(Path(source_dir).expanduser().resolve()),
            'settings': settings, 'files': source_files(source_dir, settings)}
    root = Path(run_root or ROOT / 'runs').expanduser().resolve()
    if ',' in str(root):
        raise ValueError('The run directory path cannot contain commas with this Docker mount format.')
    return Node1Plan(root / new_run_id('dictionary', root), data, _source_hashes(), seccomp_unconfined)


def prepare_example(run_root=None, seccomp_unconfined=False):
    manifest, _ = check_example()
    hashes = _source_hashes()
    parent = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / "runs"
    if "," in str(parent):
        raise ValueError("The run directory path cannot contain commas with this Docker mount format.")
    return Node1Plan(parent / new_run_id('markers', parent), manifest, hashes, seccomp_unconfined)


def check_association_panel(prefix):
    checked = check_plink(prefix)
    marker_table(checked["prefix"] + ".bim")
    return checked


def prepare_custom_association(input_prefix, phenotype_file, phenotype_name, genome_build,
                               run_root=None, seccomp_unconfined=False, *, covariate_file=None, covariates=None):
    settings = check_settings(phenotype_name, genome_build)
    panel = check_association_panel(input_prefix)
    phenotype = Path(phenotype_file).expanduser().resolve()
    _, counts = phenotype_rows(panel["prefix"] + ".fam", phenotype, phenotype_name)
    files = []
    for source, destination in [(Path(panel["prefix"] + ext), "input/markers" + ext)
                                for ext in (".bed", ".bim", ".fam")] + [(phenotype, "input/phenotype.txt")]:
        files.append({"source": str(source), "path": destination, "sha256": digest(source), "bytes": source.stat().st_size})
    if covariates:
        settings['covariates'] = covariate_names(covariates, phenotype_name)
        source = Path(covariate_file or phenotype).expanduser().resolve()
        aligned_covariates(panel['prefix'] + '.fam', source, covariates, phenotype_name)
        files.append({'source': str(source), 'path': 'input/covariates.txt',
                      'sha256': digest(source), 'bytes': source.stat().st_size})
    elif covariate_file is not None:
        raise ValueError('Select covariate columns explicitly when supplying a covariate file.')
    parent = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / "runs"
    if "," in str(parent):
        raise ValueError("The run directory path cannot contain commas with this Docker mount format.")
    data = {"stage": "node2_custom", "files": files, "panel": panel, "phenotype": counts,
            "phenotype_file": str(phenotype), "settings": settings}
    if covariates:
        data["covariate_file"] = str(source)
    return Node1Plan(parent / new_run_id('association', parent), data,
                     _source_hashes(), seccomp_unconfined)


def prepare_custom_conditional(input_prefix, phenotype_file, phenotype_name, genome_build, condition_markers,
                               run_root=None, seccomp_unconfined=False, *, covariate_file=None, covariates=None):
    base = prepare_custom_association(input_prefix, phenotype_file, phenotype_name, genome_build,
                                      run_root, seccomp_unconfined, covariate_file=covariate_file, covariates=covariates)
    names = review_custom_conditions(base.data["panel"]["prefix"] + ".bim", condition_markers)
    data = {**base.data, "stage": "node3_custom", "conditions": names}
    return Node1Plan(base.run_dir.with_name(new_run_id('conditional', base.run_dir.parent)), data,
                     base.source_hashes, seccomp_unconfined)


def prepare_stepwise(input_prefix, phenotype_file, phenotype_name, genome_build,
                     threshold=None, max_iterations=100, run_root=None, seccomp_unconfined=False,
                     min_mac=20, analysis_version=3, *, covariate_file=None, covariates=None):
    if analysis_version != 3 and (covariates or covariate_file):
        raise ValueError("Covariates require the current PLINK2 always-Firth stepwise path.")
    base = prepare_custom_association(input_prefix, phenotype_file, phenotype_name, genome_build,
                                      run_root, seccomp_unconfined, covariate_file=covariate_file, covariates=covariates)
    if analysis_version == 3:
        options = panel_selection(base.data['panel']['prefix'], base.data['phenotype']['samples'],
                                  threshold, max_iterations, min_mac)
    elif analysis_version == 2:
        options = selection_settings(0.05 if threshold is None else threshold, max_iterations)
    else:
        raise ValueError('Unsupported stepwise plan version.')
    data = {**base.data, "stage": "stepwise", "selection": options}
    if analysis_version == 3:
        data['stepwise_analysis_version'] = 3
    return Node1Plan(base.run_dir.with_name(new_run_id('stepwise', base.run_dir.parent)), data,
                     base.source_hashes, seccomp_unconfined)


def prepare_custom_markers(hped_file, dictionary_dir, genome_build, imgt, snp_prefix=None,
                           run_root=None, seccomp_unconfined=False):
    hped = Path(hped_file).expanduser().resolve()
    dictionary = Path(dictionary_dir).expanduser().resolve()
    settings = marker_settings(genome_build, imgt)
    checked = check_marker_inputs(hped, dictionary, settings, snp_prefix)
    prefix = checked["snp_panel"]["prefix"] if checked["snp_panel"] is not None else None
    sources = [(hped, "input/cohort.hped")] + [(dictionary / name, "dictionary/" + name)
                                                     for name in dictionary_names(settings)]
    if prefix is not None:
        sources += [(Path(prefix + ext), "input/genotypes" + ext) for ext in (".bed", ".bim", ".fam")]
    files = [{"source": str(path), "path": destination, "sha256": digest(path), "bytes": path.stat().st_size}
             for path, destination in sources]
    parent = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / "runs"
    if "," in str(parent):
        raise ValueError("The run directory path cannot contain commas with this Docker mount format.")
    data = {"stage": "node1_custom", "files": files, "settings": settings, "hped_file": str(hped),
            "dictionary_dir": str(dictionary), "snp_prefix": prefix, "input_samples": checked["input_samples"],
            "dictionary_positions": checked["dictionary_positions"]}
    return Node1Plan(parent / new_run_id('markers', parent), data,
                     _source_hashes(), seccomp_unconfined)


def prepare_phasing(input_prefix, genome_build, memory="4g", seed=1, run_root=None, seccomp_unconfined=False):
    settings = phase_settings(genome_build, memory, seed)
    panel, _, markers = phase_inputs(input_prefix)
    files = [{"source": panel["prefix"] + ext, "path": "input/markers" + ext,
              "sha256": digest(panel["prefix"] + ext), "bytes": Path(panel["prefix"] + ext).stat().st_size}
             for ext in (".bed", ".bim", ".fam")]
    parent = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / "runs"
    if "," in str(parent):
        raise ValueError("The run directory path cannot contain commas with this Docker mount format.")
    data = {"stage": "phasing", "files": files, "settings": settings, "panel": panel,
            "aa_markers": sum(name.startswith("AA_") for name in markers)}
    return Node1Plan(parent / new_run_id('phasing', parent), data,
                     _source_hashes(), seccomp_unconfined)


def prepare_association(parent_run, run_root=None, seccomp_unconfined=False):
    parent = Path(parent_run).expanduser().resolve()
    record = json.loads((parent / "run.json").read_text())
    if not isinstance(record, dict) or not isinstance(record.get("plan"), dict):
        raise ValueError("The selected directory does not contain a valid Node1 run record.")
    settings = record.get("plan", {})
    if (record.get("status") != "completed" or settings.get("example") != "hatk_node1"
            or settings.get("genome_build") != 18 or str(settings.get("imgt")) != "3320"
            or settings.get("field_format") != 2):
        raise ValueError("Select a completed hg18/IMGT3320/two-field Node1 example run.")
    validate_results(parent)
    previous_hashes = {item["path"]: item["sha256"] for item in record.get("outputs", [])}
    files = []
    for relative in ("output/cohort.chped", "output/markers.bed", "output/markers.bim", "output/markers.fam"):
        path = parent / relative
        if previous_hashes.get(relative) != digest(path):
            raise ValueError("Node1 output differs from its completed run record.")
        files.append({"source": str(path), "path": relative.replace("output/", "input/", 1),
                      "sha256": digest(path), "bytes": path.stat().st_size})
    manifest = checked_manifest(DATA / "node2")
    for item in manifest["files"]:
        path = DATA / "node2" / item["path"]
        destination = "input/RA.phe" if item["path"] == "RA.phe" else "dictionary/" + item["path"]
        files.append({"source": str(path), "path": destination, "sha256": item["sha256"], "bytes": item["bytes"]})
    _, samples = phenotype_rows(parent / "output/markers.fam", DATA / "node2/RA.phe")
    template = prepare_example(run_root, seccomp_unconfined)
    data = {"files": files, "phenotype": samples, "parent_record_sha256": digest(parent / "run.json"),
            "parent_qc": settings.get("qc")}
    return Node1Plan(template.run_dir.with_name(new_run_id('association', template.run_dir.parent)), data,
                     template.source_hashes, seccomp_unconfined, str(parent))


def prepare_conditional(parent_run, run_root=None, seccomp_unconfined=False, condition_markers=None):
    parent = Path(parent_run).expanduser().resolve()
    record = json.loads((parent / "run.json").read_text())
    if not isinstance(record, dict) or not isinstance(record.get("plan"), dict):
        raise ValueError("Select a completed Node2 example run.")
    settings = record["plan"]
    if (record.get("status") != "completed" or settings.get("example") != "hatk_node2"
            or settings.get("genome_build") != 18 or str(settings.get("imgt")) != "3320"
            or settings.get("field_format") != 2 or settings.get("phenotype") != "RA"
            or settings.get("regression") != "logistic" or settings.get("covariates") != []):
        raise ValueError("Select a completed hg18/IMGT3320/two-field RA Node2 example without covariates.")
    validate_association_outputs(parent)
    inputs = {item["path"]: item["sha256"] for item in settings["data"]["files"]}
    outputs = {item["path"]: item["sha256"] for item in record.get("outputs", [])}
    files = []
    for relative in ("input/markers.bed", "input/markers.bim", "input/markers.fam", "input/RA.phe",
                     "output/RA.assoc.logistic"):
        path = parent / relative
        expected = (outputs if relative.startswith("output/") else inputs).get(relative)
        if expected != digest(path):
            raise ValueError("Node2 input or baseline result differs from its recorded hash.")
        destination = "input/baseline.assoc.logistic" if relative.startswith("output/") else relative
        files.append({"source": str(path), "path": destination, "sha256": expected, "bytes": path.stat().st_size})
    _, samples = phenotype_rows(parent / "input/markers.fam", parent / "input/RA.phe")
    conditions = review_conditions(parent / "output/RA.assoc.logistic", parent / "input/markers.bim",
                                   samples["samples"], condition_markers)
    template = prepare_example(run_root, seccomp_unconfined)
    data = {"stage": "node3", "files": files, "phenotype": samples,
            "parent_record_sha256": digest(parent / "run.json"), "parent_qc": settings.get("parent_qc"),
            "conditions": conditions,
            "condition_selection": "suggested_baseline_lead" if condition_markers is None else "user_specified"}
    return Node1Plan(template.run_dir.with_name(new_run_id('conditional', template.run_dir.parent)), data,
                     template.source_hashes, seccomp_unconfined, str(parent))


def prepare_meta(parent_run, run_root=None, seccomp_unconfined=False):
    parent = Path(parent_run).expanduser().resolve()
    record = json.loads((parent / "run.json").read_text())
    if not isinstance(record, dict) or not isinstance(record.get("plan"), dict):
        raise ValueError("Select a completed Node2 example run.")
    settings = record["plan"]
    if (record.get("status") != "completed" or settings.get("example") != "hatk_node2"
            or settings.get("genome_build") != 18 or str(settings.get("imgt")) != "3320"
            or settings.get("field_format") != 2 or settings.get("phenotype") != "RA"
            or settings.get("regression") != "logistic" or settings.get("covariates") != []):
        raise ValueError("Select a completed hg18/IMGT3320/two-field RA Node2 example without covariates.")
    validate_association_outputs(parent)
    inputs = {item["path"]: item["sha256"] for item in settings["data"]["files"]}
    files = []
    for relative in ("input/markers.bed", "input/markers.bim", "input/markers.fam", "input/RA.phe"):
        path = parent / relative
        expected = inputs.get(relative)
        if expected != digest(path):
            raise ValueError("Node2 inputs differ from their recorded hashes.")
        files.append({"source": str(path), "path": relative, "sha256": expected, "bytes": path.stat().st_size})
    _, samples, splits = split_samples(parent)
    template = prepare_example(run_root, seccomp_unconfined)
    data = {"stage": "node5", "files": files, "phenotype": samples, "split_counts": splits,
            "parent_record_sha256": digest(parent / "run.json"), "parent_qc": settings.get("parent_qc")}
    return Node1Plan(template.run_dir.with_name(new_run_id('meta', template.run_dir.parent)), data,
                     template.source_hashes, seccomp_unconfined, str(parent))


def prepare_custom_meta(study1_file, study2_file, phenotype_name, genome_build, nonoverlap_confirmed=False,
                        run_root=None, seccomp_unconfined=False):
    settings = meta_settings(phenotype_name, genome_build, nonoverlap_confirmed)
    paths = [Path(path).expanduser().resolve() for path in (study1_file, study2_file)]
    tables = [study_table(path) for path in paths]
    if paths[0] == paths[1] or digest(paths[0]) == digest(paths[1]):
        raise ValueError("The same or byte-identical result table cannot be used as two studies.")
    _, summary, _ = combine_tables(*tables, settings)
    files = [{"source": str(path), "path": f"input/study{i}.tsv", "sha256": digest(path), "bytes": path.stat().st_size}
             for i, path in enumerate(paths, 1)]
    parent = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / "runs"
    if "," in str(parent):
        raise ValueError("The run directory path cannot contain commas with this Docker mount format.")
    data = {"stage": "node5_custom", "files": files, "settings": settings,
            "study1_file": str(paths[0]), "study2_file": str(paths[1]), "review_summary": summary}
    return Node1Plan(parent / new_run_id('meta', parent), data,
                     _source_hashes(), seccomp_unconfined)


def prepare_custom_omnibus(phased_file, fam_file, phenotype_file, phenotype_name, genome_build,
                           run_root=None, seccomp_unconfined=False):
    settings = check_settings(phenotype_name, genome_build)
    phased, fam, phenotype = [Path(path).expanduser().resolve() for path in
                              (phased_file, fam_file, phenotype_file)]
    _, _, samples, positions = read_phased_files(phased, fam, phenotype, phenotype_name)
    files = [{"source": str(path), "path": destination, "sha256": digest(path), "bytes": path.stat().st_size}
             for path, destination in ((phased, "input/phased-aa.bgl"), (fam, "input/cohort.fam"),
                                       (phenotype, "input/phenotype.txt"))]
    parent = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / "runs"
    if "," in str(parent):
        raise ValueError("The run directory path cannot contain commas with this Docker mount format.")
    data = {"stage": "node4_custom", "files": files, "settings": settings, "phenotype": samples,
            "phased_file": str(phased), "fam_file": str(fam), "phenotype_file": str(phenotype),
            "positions": len(positions), "test_counts": test_counts(positions)}
    provenance = checked_omnibus_provenance(phased, fam, genome_build)
    if provenance is not None:
        data['phasing_source'] = provenance
    return Node1Plan(parent / new_run_id('omnibus', parent), data,
                     _source_hashes(), seccomp_unconfined)


def prepare_omnibus(run_root=None, seccomp_unconfined=False):
    manifest = checked_manifest(DATA / "node4")
    samples = read_phased(DATA / "node4", input_dir=".")[2]
    template = prepare_example(run_root, seccomp_unconfined)
    files = [{**item, "source": str(DATA / "node4" / item["path"]), "path": "input/" + item["path"]}
             for item in manifest["files"]]
    data = {"stage": "node4", "files": files, "phenotype": samples}
    return Node1Plan(template.run_dir.with_name(new_run_id('omnibus', template.run_dir.parent)), data,
                     template.source_hashes, seccomp_unconfined)


def check_environment(seccomp_unconfined=False):
    if not shutil.which("docker"):
        raise RuntimeError("Docker was not found. HATK Python packages are needed only inside the image.")
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20)
    if info.returncode:
        raise RuntimeError("Cannot access Docker:\n" + info.stderr.strip())
    inspected = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True, timeout=20)
    if inspected.returncode:
        raise RuntimeError(f"Cannot inspect {IMAGE}. Prepare the example-enabled image as described in "
                           "docker/examples/README.md.\n" + inspected.stderr.strip())
    images = json.loads(inspected.stdout)
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise ValueError("Docker returned unexpected image metadata.")
    image = images[0]
    image_id = image.get("Id", "")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Docker returned an invalid image ID.")
    command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}"]
    if seccomp_unconfined:
        command += ["--security-opt", "seccomp=unconfined"]
    result = subprocess.run(command + [image_id, "--check"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("HATK environment check failed:\n" + result.stderr.strip())
    environment = json.loads(result.stdout)
    expected = image_source_hashes()
    if not isinstance(environment, dict) or environment.get("source_sha256") != expected:
        raise ValueError("The HATK image source differs from this checkout. Rebuild the image before running.")
    environment["image"] = {"requested_reference": IMAGE, "id": image_id,
                            "repo_digests": image.get("RepoDigests") or [],
                            "created": image.get("Created"), "os": image.get("Os"),
                            "architecture": image.get("Architecture")}
    return environment


def validate_results(run_dir):
    run_dir = Path(run_dir)
    original = pedigree(run_dir / "input/cohort.hped")
    cleaned = pedigree(run_dir / "output/cohort.chped", header=True)
    if [row[:6] for row in original] != [row[:6] for row in cleaned]:
        raise ValueError("CHPED sample metadata or order differs from the HPED input.")
    lost = sum(before != "0" and after == "0" for a, b in zip(original, cleaned)
               for before, after in zip(a[6:], b[6:]))
    if lost:
        raise ValueError(f"NomenCleaner replaced {lost} nonmissing alleles with missing values.")
    for row in cleaned:
        for index, allele in enumerate(row[6:]):
            if allele != "0" and not re.fullmatch(re.escape(GENES[index // 2]) + r"\*\d{2,}:\d{2,}[A-Z]?", allele):
                raise ValueError("Unexpected cleaned allele format or gene column.")
    panel = check_plink(run_dir / "output/markers")
    rows = [line.split() for line in (run_dir / "output/markers.fam").read_text().splitlines()]
    originals = {tuple(row[:2]): row[:5] for row in original}
    if any(tuple(row[:2]) not in originals or row[:5] != originals[tuple(row[:2])] for row in rows):
        raise ValueError("Marker FAM contains unexpected sample IDs or pedigree/sex metadata.")
    counts = Counter()
    labels = set()
    for line in (run_dir / "output/markers.bim").read_text().splitlines():
        label = line.split()[1]
        if label in labels:
            raise ValueError("Duplicate marker IDs in the generated panel.")
        labels.add(label)
        kind = next((prefix for prefix in ("HLA", "AA", "SNPS", "INS") if label.startswith(prefix + "_")), "SNP")
        counts[kind] += 1
    if any(not counts[kind] for kind in ("HLA", "AA", "SNPS", "SNP")):
        raise ValueError("An expected marker class is absent from the generated panel.")
    return {"input_samples": len(original), "output_samples": panel["samples"],
            "removed_samples": len(original) - panel["samples"], "markers": panel["variants"],
            "marker_classes": dict(counts)}


def run_example(plan, progress=None):
    """Execute a plan after the caller has obtained user approval."""
    report_stage = progress or (lambda stage: None)
    report_stage("inputs")
    current = (prepare_dictionary(plan.data['source_dir'], plan.run_dir.parent, plan.seccomp_unconfined) if plan.dictionary else
               prepare_stepwise(plan.data["panel"]["prefix"], plan.data["phenotype_file"],
                                **plan.data["settings"], **selection_arguments(plan.data['selection']),
                                analysis_version=plan.data.get('stepwise_analysis_version', 2),
                                **({'covariate_file': plan.data['covariate_file']} if plan.data['settings'].get('covariates') else {}),
                                run_root=plan.run_dir.parent, seccomp_unconfined=plan.seccomp_unconfined) if plan.stepwise else
               prepare_phasing(plan.data["panel"]["prefix"], **plan.data["settings"],
                               run_root=plan.run_dir.parent, seccomp_unconfined=plan.seccomp_unconfined) if plan.phasing else
               prepare_custom_meta(plan.data["study1_file"], plan.data["study2_file"],
                                  plan.data["settings"]["phenotype_name"], plan.data["settings"]["genome_build"],
                                  plan.data["settings"]["nonoverlap_confirmed"], plan.run_dir.parent,
                                  plan.seccomp_unconfined) if plan.custom_meta else
               prepare_custom_omnibus(plan.data["phased_file"], plan.data["fam_file"], plan.data["phenotype_file"],
                                     plan.data["settings"]["phenotype_name"], plan.data["settings"]["genome_build"],
                                     plan.run_dir.parent, plan.seccomp_unconfined) if plan.custom_omnibus else
               prepare_custom_markers(plan.data["hped_file"], plan.data["dictionary_dir"],
                                     plan.data["settings"]["genome_build"], plan.data["settings"]["imgt"],
                                     plan.data["snp_prefix"], plan.run_dir.parent, plan.seccomp_unconfined) if plan.custom_markers else
               prepare_custom_conditional(plan.data["panel"]["prefix"], plan.data["phenotype_file"],
                                          plan.data["settings"]["phenotype_name"], plan.data["settings"]["genome_build"],
                                          plan.data["conditions"], plan.run_dir.parent, plan.seccomp_unconfined,
                                          **plan_covariates(plan.data)) if plan.custom_conditional else
               prepare_custom_association(plan.data["panel"]["prefix"], plan.data["phenotype_file"],
                                          plan.data["settings"]["phenotype_name"], plan.data["settings"]["genome_build"],
                                          plan.run_dir.parent, plan.seccomp_unconfined,
                                          **plan_covariates(plan.data)) if plan.custom_association else
               prepare_meta(plan.parent_run, plan.run_dir.parent, plan.seccomp_unconfined) if plan.meta else
               prepare_omnibus(plan.run_dir.parent, plan.seccomp_unconfined) if plan.omnibus else
               prepare_conditional(plan.parent_run, plan.run_dir.parent, plan.seccomp_unconfined,
                                   None if plan.data["condition_selection"] == "suggested_baseline_lead"
                                   else plan.data["conditions"]) if plan.conditional else
               prepare_association(plan.parent_run, plan.run_dir.parent, plan.seccomp_unconfined)
               if plan.parent_run else prepare_example(plan.run_dir.parent, plan.seccomp_unconfined))
    if current.data != plan.data or current.source_hashes != plan.source_hashes:
        raise ValueError("Example inputs or source changed after the plan was prepared.")
    report_stage("environment")
    environment = check_environment(plan.seccomp_unconfined)
    report_stage("staging")
    plan.run_dir.mkdir(parents=True)
    record = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "plan": plan.to_dict(), "environment": environment}
    record_path = plan.run_dir / "run.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    process = None
    try:
        for item in plan.data["files"]:
            target = plan.run_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.get("source", DATA / item["path"]), target)
            if digest(target) != item["sha256"]:
                raise ValueError("Example input changed during staging.")
        (plan.run_dir / "output").mkdir()
        if plan.phasing:
            settings_file = plan.run_dir / "input/phasing-settings.json"
            settings_file.write_text(json.dumps(plan.data["settings"], indent=2) + "\n")
            record["derived_inputs"] = [{"path": "input/phasing-settings.json", "sha256": digest(settings_file)}]
        if plan.custom_meta:
            settings_file = plan.run_dir / "input/meta-settings.json"
            settings_file.write_text(json.dumps(plan.data["settings"], indent=2) + "\n")
            record["derived_inputs"] = [{"path": "input/meta-settings.json", "sha256": digest(settings_file)}]
        if plan.custom_markers:
            settings_file = plan.run_dir / "input/marker-settings.json"
            settings_file.write_text(json.dumps(plan.data["settings"], indent=2) + "\n")
            record["derived_inputs"] = [{"path": "input/marker-settings.json", "sha256": digest(settings_file)}]
        if plan.custom_association or plan.custom_conditional or plan.custom_omnibus or plan.stepwise:
            settings_file = plan.run_dir / "input/analysis.json"
            settings_file.write_text(json.dumps(plan.data["settings"], indent=2) + "\n")
            record["derived_inputs"] = [{"path": "input/analysis.json", "sha256": digest(settings_file)}]
        if plan.stepwise:
            settings_file = plan.run_dir / "input/stepwise-settings.json"
            settings_file.write_text(json.dumps(plan.data["selection"], indent=2) + "\n")
            record.setdefault("derived_inputs", []).append({"path": "input/stepwise-settings.json", "sha256": digest(settings_file)})
        if plan.conditional or plan.custom_conditional:
            conditions_file = plan.run_dir / "input/conditions.txt"
            conditions_file.write_text("\n".join(plan.data["conditions"]) + "\n")
            record.setdefault("derived_inputs", []).append({"path": "input/conditions.txt", "sha256": digest(conditions_file)})
        command = plan.command(environment["image"]["id"])
        record["command"] = command
        record_path.write_text(json.dumps(record, indent=2) + "\n")
        with (plan.run_dir / "hatk.log").open("w") as log:
            report_stage("execution")
            process = subprocess.Popen(command, cwd=plan.run_dir, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            code = process.wait()
        record["returncode"] = code
        if code:
            raise RuntimeError(f"HATK worker exited with code {code}. Inspect hatk.log.")
        report_stage("validation")
        from hlagent.analysis.imgt_dictionary import validate_dictionary_outputs
        record["summary"] = (validate_dictionary_outputs(plan.run_dir) if plan.dictionary else
                             validate_stepwise_outputs(plan.run_dir) if plan.stepwise else
                             validate_phasing_outputs(plan.run_dir) if plan.phasing else
                             validate_custom_meta_outputs(plan.run_dir) if plan.custom_meta else
                             validate_custom_omnibus_outputs(plan.run_dir) if plan.custom_omnibus else
                             validate_custom_marker_outputs(plan.run_dir) if plan.custom_markers else
                             validate_custom_conditional_outputs(plan.run_dir) if plan.custom_conditional else
                             validate_custom_association_outputs(plan.run_dir) if plan.custom_association else
                             validate_meta_outputs(plan.run_dir) if plan.meta else
                             validate_omnibus_outputs(plan.run_dir) if plan.omnibus else
                             validate_conditional_outputs(plan.run_dir) if plan.conditional else
                             validate_association_outputs(plan.run_dir) if plan.parent_run
                             else validate_results(plan.run_dir))
        record["outputs"] = [{"path": str(path.relative_to(plan.run_dir)), "sha256": digest(path)}
                             for path in (sorted((plan.run_dir / "output").glob("*")) if plan.dictionary or plan.stepwise or plan.parent_run or plan.phasing or plan.custom_meta or plan.omnibus or plan.custom_omnibus or plan.custom_association or plan.custom_conditional or plan.custom_markers else
                                          [plan.run_dir / "output/cohort.chped",
                                           *(plan.run_dir / ("output/markers" + ext) for ext in (".bed", ".bim", ".fam"))])
                             if path.is_file()]
        record["status"] = ("incomplete" if plan.stepwise and record["summary"].get("selection_status") != "complete"
                            else "completed")
    except KeyboardInterrupt:
        record["status"] = "cancellation_unconfirmed"
        if process is not None:
            try:
                stopped = subprocess.run(["docker", "stop", "--time", "10", plan.run_dir.name],
                                         capture_output=True, text=True, timeout=20)
                if stopped.returncode == 0:
                    record["status"] = "cancelled"
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    except (OSError, ValueError, RuntimeError) as error:
        record.update(status="failed", error=str(error))
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record_path.write_text(json.dumps(record, indent=2) + "\n")
    return record
