"""CookHLA planning and execution, shared by command-line and agent interfaces."""

from dataclasses import asdict, dataclass
from collections import Counter
from datetime import datetime, timezone
import re
from typing import Optional
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess

from hlagent.analysis.cookhla_inputs import REFERENCE_SUFFIXES, check_inputs
from hlagent.analysis.example_data import ensure_examples


from hlagent.paths import ROOT, new_run_id
DATA = ROOT / "datasets/examples/cookhla"
TARGET = "1958BC.hg19"
REFERENCE = "1000G_REF.EUR.chr6.hg18.29mb-34mb.inT1DGC"
AGM = "AGM.1958BC.hg19.COPY.LiftDown_hg18.NoAmbig+" + REFERENCE
IMAGE = "jaejoon/cookhla:v1"
EXAMPLE_GENES = ("A", "B", "C", "DQB1", "DRB1")
RESULT_NAME = "1958BC+1000G_REF.EUR.MHC.HLA_IMPUTATION_OUT.alleles"


def check_data():
    ensure_examples('cookhla', IMAGE, DATA)
    manifest = json.loads((DATA / "sources.json").read_text())
    for item in manifest["files"]:
        path = DATA / item["path"]
        if not path.is_file():
            raise RuntimeError(f"Missing example file: {path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Example file differs from its recorded source: {path}")
    return manifest


def build_command(run_dir, name, memory, processes, seccomp_unconfined=False, image=IMAGE, inputs=None):
    command = [
        "docker", "run", "--rm", "--name", name,
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--mount", f"type=bind,source={run_dir},target=/work",
    ]
    if seccomp_unconfined:
        command.extend(["--security-opt", "seccomp=unconfined"])
    target = TARGET if inputs is None else "genotypes"
    reference = REFERENCE if inputs is None else "panel"
    build = 19 if inputs is None else inputs["target_genome_build"]
    output = "1958BC+1000G_REF.EUR" if inputs is None else "imputed"
    command += [image, "CookHLA.py", "-i", f"/work/input/{target}", "-hg", str(build),
                "-ref", f"/work/reference/{reference}"]
    if inputs is None:
        command += ["-gm", f"/work/input/{AGM}.mach_step.avg.clpsB",
                    "-ae", f"/work/input/{AGM}.aver.erate"]
    elif inputs["agm"] == "supplied":
        command += ["-gm", "/work/input/agm.map", "-ae", "/work/input/agm.erate"]
    return command + ["-o", f"/work/output/{output}", "-mem", memory, "-mp", str(processes)]



@dataclass(frozen=True)
class RunPlan:
    run_dir: Path
    container_name: str
    memory: str
    processes: int
    sources: dict
    seccomp_unconfined: bool = False
    inputs: Optional[dict] = None
    dataset: Optional[str] = None

    def command(self, image=IMAGE):
        return build_command(self.run_dir, self.container_name, self.memory, self.processes,
                             self.seccomp_unconfined, image, self.inputs)

    def to_dict(self):
        details = {
            "example": "cookhla", "input": TARGET, "genome_build": 19,
            "reference": REFERENCE, "agm": "bundled", "image": IMAGE,
            "memory": self.memory, "processes": self.processes,
            "run_dir": str(self.run_dir), "container_name": self.container_name,
            "command": self.command(), "sources": self.sources,
            "seccomp_unconfined": self.seccomp_unconfined,
        }
        if self.inputs is not None:
            details.update(example=None, input=self.inputs["target"]["prefix"],
                           genome_build=self.inputs["target_genome_build"],
                           reference=self.inputs["reference"]["prefix"], agm=self.inputs["agm"],
                           inputs=self.inputs, expected_genes=self.inputs["reference"]["hla_marker_genes"])
        if self.dataset is not None:
            details["dataset"] = self.dataset
        return details


@dataclass(frozen=True)
class RunResult:
    status: str
    run_dir: str
    log_path: str
    alleles: Optional[str] = None
    error: Optional[str] = None
    returncode: Optional[int] = None
    container_stopped: Optional[bool] = None
    summary: Optional[dict] = None

    def to_dict(self):
        return asdict(self)


def check_resources(memory, processes):
    if not isinstance(memory, str) or not re.fullmatch(r"[1-9][0-9]*[mMgG]", memory):
        raise ValueError("Memory must be a positive integer followed by m or g, such as 4g.")
    if type(processes) is not int or processes < 1:
        raise ValueError("Processes must be a positive integer.")


def prepare_example(memory="16g", processes=6, run_root=None, seccomp_unconfined=False):
    check_resources(memory, processes)
    sources = check_data()
    parent = Path(run_root).resolve() if run_root is not None else ROOT / "runs"
    name = new_run_id('imputation', parent)
    return RunPlan(parent / name, name, memory, processes, sources, seccomp_unconfined)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def custom_sources(inputs):
    files = []
    for label, destination, suffixes in (("target", "input/genotypes", (".bed", ".bim", ".fam")),
                                         ("reference", "reference/panel", REFERENCE_SUFFIXES)):
        for suffix in suffixes:
            files.append((inputs[label]["prefix"] + suffix, destination + suffix))
    if inputs["agm"] == "supplied":
        files += [(inputs["genetic_map"], "input/agm.map"), (inputs["error_rate"], "input/agm.erate")]
    return {"kind": "user", "files": [
        {"source_path": source, "path": destination, "bytes": Path(source).stat().st_size,
         "sha256": file_hash(source)} for source, destination in files]}


def prepare_custom(input_prefix, reference_prefix, genome_build, genetic_map=None, error_rate=None,
                   memory="16g", processes=6, run_root=None, seccomp_unconfined=False):
    check_resources(memory, processes)
    inputs = check_inputs(input_prefix, reference_prefix, genome_build, genetic_map, error_rate)
    supported = {"A", "B", "C", "DPA1", "DPB1", "DQA1", "DQB1", "DRB1"}
    if set(inputs["reference"]["hla_marker_genes"]) - supported:
        raise ValueError("The reference contains HLA genes outside the supported classical gene set.")
    sources = custom_sources(inputs)
    parent = Path(run_root).expanduser().resolve() if run_root is not None else ROOT / "runs"
    if "," in str(parent):
        raise ValueError("The run directory path cannot contain commas with this Docker mount format.")
    name = new_run_id('imputation', parent)
    return RunPlan(parent / name, name, memory, processes, sources, seccomp_unconfined, inputs)


def check_docker():
    if not shutil.which("docker"):
        raise RuntimeError("Docker was not found. Install it before running CookHLA.")
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20)
    if info.returncode:
        raise RuntimeError(f"Cannot access Docker:\n{info.stderr.strip()}")


def prepare_image(log):
    """Resolve the local image to its immutable ID, pulling only if absent."""
    command = ["docker", "image", "inspect", IMAGE]
    inspected = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if inspected.returncode:
        pulled = subprocess.run(["docker", "pull", IMAGE], stdout=log, stderr=subprocess.STDOUT)
        if pulled.returncode:
            raise RuntimeError(f"Docker image download failed with code {pulled.returncode}.")
        inspected = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if inspected.returncode:
        raise RuntimeError(f"Cannot inspect the CookHLA image: {inspected.stderr.strip()}")
    images = json.loads(inspected.stdout)
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise RuntimeError("Docker returned unexpected image metadata.")
    image = images[0]
    image_id = image.get("Id", "")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise RuntimeError("Docker did not return a valid image ID.")
    return {
        "requested_reference": IMAGE, "id": image_id,
        "repo_digests": image.get("RepoDigests") or [],
        "created": image.get("Created"), "os": image.get("Os"),
        "architecture": image.get("Architecture"),
    }


def execution_environment():
    version = subprocess.run(
        ["docker", "version", "--format", "{{.Client.Version}} {{.Server.Version}}"],
        capture_output=True, text=True, timeout=20)
    versions = version.stdout.split()
    if version.returncode or len(versions) != 2:
        raise RuntimeError(f"Cannot record Docker versions: {version.stderr.strip()}")
    return {
        "docker": {"client": versions[0], "server": versions[1]},
        "host": {"os": platform.system(), "kernel": platform.release(),
                 "architecture": platform.machine(), "python": platform.python_version(),
                 "uid": os.getuid(), "gid": os.getgid()},
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("hlagent/paths.py", "hlagent/analysis/cookhla.py", "hlagent/cli/cookhla_example.py",
                         "hlagent/analysis/cookhla_inputs.py", "hlagent/cli/cookhla_run.py")
        },
    }


def validate_results(fam_path, alleles_path, expected_genes=EXAMPLE_GENES):
    """Check sample/gene coverage, not prediction accuracy."""
    if not expected_genes:
        raise ValueError("Expected HLA genes must be specified.")
    samples = set()
    for number, line in enumerate(Path(fam_path).read_text().splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 6:
            raise ValueError(f"Invalid FAM row at line {number}: expected 6 columns.")
        sample = tuple(fields[:2])
        if sample in samples:
            raise ValueError(f"Duplicate input sample at FAM line {number}.")
        samples.add(sample)
    if not samples:
        raise ValueError("The input FAM file contains no samples.")

    observed = Counter()
    gene_counts = Counter()
    output_samples = set()
    malformed_rows = []
    row_count = 0
    for number, line in enumerate(Path(alleles_path).read_text().splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        row_count += 1
        if len(fields) != 8:
            malformed_rows.append(number)
            continue
        fid, iid, gene = fields[:3]
        observed[(fid, iid, gene)] += 1
        gene_counts[gene] += 1
        output_samples.add((fid, iid))

    expected = {(fid, iid, gene) for fid, iid in samples for gene in expected_genes}
    missing = sorted(expected - observed.keys())
    duplicates = sorted(key for key, count in observed.items() if count > 1)
    unexpected_samples = sorted(output_samples - samples)
    unexpected_genes = sorted(set(gene_counts) - set(expected_genes))
    errors = []
    for count, label in ((len(malformed_rows), "malformed result rows"),
                         (len(missing), "missing sample/gene pairs"),
                         (len(duplicates), "duplicate sample/gene pairs"),
                         (len(unexpected_samples), "unexpected samples"),
                         (len(unexpected_genes), "unexpected genes")):
        if count:
            errors.append(f"{count} {label}")
    return {
        "valid": not errors, "input_samples": len(samples),
        "output_samples": len(output_samples), "result_rows": row_count,
        "expected_genes": list(expected_genes), "gene_counts": dict(sorted(gene_counts.items())),
        "missing_pairs": [list(key) for key in missing],
        "duplicate_pairs": [list(key) for key in duplicates],
        "unexpected_samples": [list(sample) for sample in unexpected_samples],
        "unexpected_genes": unexpected_genes, "malformed_rows": malformed_rows,
        "errors": errors,
    }


def check_run_results(run_dir):
    """Inspect an existing run without changing files or contacting Docker."""
    run_dir = Path(run_dir).resolve()
    record = run_dir / "run.json"
    if not (run_dir / "input" / (TARGET + ".fam")).is_file() and record.is_file():
        details = json.loads(record.read_text()).get("plan", {})
        if details.get("inputs") is not None:
            return validate_results(run_dir / "input/genotypes.fam",
                                    run_dir / "output/imputed.MHC.HLA_IMPUTATION_OUT.alleles",
                                    details["expected_genes"])
    return validate_results(run_dir / "input" / (TARGET + ".fam"),
                            run_dir / "output" / RESULT_NAME)


def run_analysis(plan, progress=None):
    """Execute an approved plan. The calling interface must obtain approval first.

    By default this function is silent; progress receives actual stage names.
    It never reads terminal input and blocks until the run finishes;
    cancellation currently comes from KeyboardInterrupt.
    """
    report_stage = progress or (lambda stage: None)
    run_dir = plan.run_dir
    log_path = run_dir / "cookhla.log"
    record_path = run_dir / "run.json"
    record = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "plan": plan.to_dict()}
    created = False
    returncode = None
    stopped = None
    summary = None
    try:
        report_stage("inputs")
        # Settings may have been previewed earlier; recheck before starting.
        if plan.inputs is not None:
            checked = check_inputs(plan.inputs["target"]["prefix"], plan.inputs["reference"]["prefix"],
                                   plan.inputs["target_genome_build"], plan.inputs["genetic_map"],
                                   plan.inputs["error_rate"])
            if checked != plan.inputs:
                raise RuntimeError("Input sources changed after the plan was prepared.")
        current_sources = check_data() if plan.inputs is None else custom_sources(plan.inputs)
        if current_sources != plan.sources:
            raise RuntimeError("Input sources changed after the plan was prepared.")
        report_stage("environment")
        check_docker()
        report_stage("staging")
        run_dir.mkdir(parents=True)
        created = True
        record_path.write_text(json.dumps(record, indent=2) + "\n")
        if plan.inputs is None:
            for directory in ("input", "reference"):
                shutil.copytree(DATA / directory, run_dir / directory)
        else:
            for item in plan.sources["files"]:
                destination = run_dir / item["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item["source_path"], destination)
                if file_hash(destination) != item["sha256"]:
                    raise RuntimeError("Input sources changed while copying to the run directory.")
        (run_dir / "output").mkdir()
        with log_path.open("w") as log:
            report_stage("environment")
            record["environment"] = execution_environment()
            record["image"] = prepare_image(log)
            # Execute the inspected ID rather than a tag that can change.
            record["command"] = plan.command(image=record["image"]["id"])
            record_path.write_text(json.dumps(record, indent=2) + "\n")
            report_stage("execution")
            process = subprocess.run(record["command"], stdout=log, stderr=subprocess.STDOUT)
        returncode = process.returncode
        if returncode:
            raise RuntimeError(f"CookHLA exited with code {returncode}.")
        report_stage("validation")
        result_name = RESULT_NAME if plan.inputs is None else "imputed.MHC.HLA_IMPUTATION_OUT.alleles"
        alleles = run_dir / "output" / result_name
        if not alleles.is_file() or not alleles.stat().st_size:
            raise RuntimeError("CookHLA returned without a nonempty .alleles file.")
        summary = check_run_results(run_dir)
        if not summary["valid"]:
            raise RuntimeError("Result checks failed: " + "; ".join(summary["errors"]))
        result = RunResult("completed", str(run_dir), str(log_path),
                           alleles=str(alleles), returncode=returncode, summary=summary)
    except KeyboardInterrupt:
        stop_error = None
        if created:
            try:
                stop = subprocess.run(
                    ["docker", "stop", "--time", "10", plan.container_name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
                stopped = stop.returncode == 0
                if not stopped:
                    stop_error = "Could not confirm container stop. Check docker ps."
            except (OSError, subprocess.TimeoutExpired) as error:
                stopped = False
                stop_error = str(error)
        status = "cancelled" if not created or stopped else "cancellation_unconfirmed"
        result = RunResult(status, str(run_dir), str(log_path),
                           error=stop_error, container_stopped=stopped)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        result = RunResult("failed", str(run_dir), str(log_path),
                           error=str(error), returncode=returncode, summary=summary)
    if created:
        record.update(result.to_dict())
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record_path.write_text(json.dumps(record, indent=2) + "\n")
    return result


def run_example(plan, progress=None):
    """Retain the example interface while sharing execution with custom runs."""
    return run_analysis(plan, progress=progress)
