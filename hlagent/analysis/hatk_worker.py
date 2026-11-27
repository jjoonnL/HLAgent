"""Run the bundled Node1 modules in a separate Python process."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from hlagent.paths import ROOT, image_source_hashes


GENES = ("A", "B", "C", "DPA1", "DPB1", "DQA1", "DQB1", "DRB1")
QC = {"mind": 0.3, "geno": 0.5, "maf_threshold": 0.0001, "hwe_threshold": 0.000001}


def main():
    import importlib.metadata

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--phasing", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument('--imgt2seq', action='store_true')
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--markers-custom", action="store_true")
    mode.add_argument("--association", action="store_true")
    mode.add_argument("--association-custom", action="store_true")
    mode.add_argument("--stepwise", action="store_true")
    mode.add_argument("--conditional", action="store_true")
    mode.add_argument("--conditional-custom", action="store_true")
    mode.add_argument("--omnibus-custom", action="store_true")
    mode.add_argument("--omnibus", action="store_true")
    mode.add_argument("--meta-custom", action="store_true")
    mode.add_argument("--meta", action="store_true")
    args = parser.parse_args()
    plink = shutil.which("plink")
    if plink is None:
        raise RuntimeError("PLINK 1.9 was not found inside the HATK image.")
    version = subprocess.run([plink, "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if "PLINK v1.9" not in version:
        raise RuntimeError("The example requires PLINK 1.9, not PLINK 2 or PuTTY plink.")
    plink2 = shutil.which("plink2")
    if plink2 is None:
        raise RuntimeError("PLINK 2 was not found inside the HATK image. Rebuild it.")
    version2 = subprocess.run([plink2, "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if "PLINK v2.0.0-b.1.3" not in version2:
        raise RuntimeError("The HATK image requires the pinned PLINK 2 build. Rebuild it.")
    source = ROOT / "vendor/hatk"
    manifest = json.loads((source / "sources.json").read_text())
    for item in manifest["files"]:
        if hashlib.sha256((source / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Bundled HATK source/data changed inside the image. Rebuild it.")
    sys.path.insert(0, str(source))
    os.environ["HLAGENT_MAIN_DIRECTORY"] = str(source)
    from src.tools.HATK.NomenCleaner.NomenCleaner import NomenCleaner
    from src.tools.HATK.NomenCleaner.src.CHPED import CHPED
    from src.tools.HATK.bMarkerGenerator.bMarkerGenerator import bMarkerGenerator

    os.environ.setdefault("MPLCONFIGDIR", "/tmp/hlagent-mpl-" + str(os.getuid()))
    import matplotlib.pyplot
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "matplotlib")}
    r_version = subprocess.run(["Rscript", "--version"], check=True, capture_output=True, text=True)
    versions.update(python=sys.version.split()[0], plink=version, plink_path=plink,
                    plink2=version2, plink2_path=plink2,
                    R=(r_version.stdout + r_version.stderr).strip())
    root = source.parent.parent
    versions["source_sha256"] = image_source_hashes(root)
    from hlagent.analysis.hatk_phasing import BEAGLE_SHA256
    jar = root / "dependency/beagle.jar"
    if hashlib.sha256(jar.read_bytes()).hexdigest() != BEAGLE_SHA256:
        raise ValueError("BEAGLE binary differs from the pinned 3.3.2 build.")
    java_version = subprocess.run(["java", "-version"], check=True, capture_output=True, text=True)
    versions["java"] = (java_version.stdout + java_version.stderr).strip()
    versions["beagle_sha256"] = BEAGLE_SHA256
    if args.phasing:
        from hlagent.analysis.hatk_phasing import run_phasing
        run_phasing(Path.cwd(), jar)
        return
    if args.check:
        print(json.dumps(versions))
        return
    if args.imgt2seq:
        from hlagent.analysis.imgt_dictionary import run_dictionary
        run_dictionary(Path.cwd(), source)
        return
    if args.markers_custom:
        from hlagent.analysis.hatk_custom_markers import run_custom_markers
        run_custom_markers(Path.cwd(), source)
        return
    if args.stepwise:
        from hlagent.analysis.hatk_stepwise import run_stepwise
        run_stepwise(Path.cwd())
        return
    if args.conditional_custom:
        from hlagent.analysis.hatk_custom_conditional import run_custom_conditional
        run_custom_conditional(Path.cwd())
        return
    if args.association_custom:
        from hlagent.analysis.hatk_custom_association import run_custom_association
        run_custom_association(Path.cwd())
        return
    if args.meta_custom:
        from hlagent.analysis.hatk_custom_meta import run_custom_meta
        run_custom_meta(Path.cwd())
        return
    if args.meta:
        from hlagent.analysis.hatk_meta import run_meta
        run_meta(Path.cwd())
        return
    if args.omnibus_custom:
        from hlagent.analysis.hatk_custom_omnibus import run_custom_omnibus
        run_custom_omnibus(Path.cwd(), source / "src/tools/HATK/OmnibusTest/OmnibusTest_BHv5.R")
        return
    if args.omnibus:
        from hlagent.analysis.hatk_omnibus import run_omnibus
        run_omnibus(Path.cwd(), source / "src/tools/HATK/OmnibusTest/OmnibusTest_BHv5.R")
        return
    if args.conditional:
        from hlagent.analysis.hatk_conditional import run_conditional
        run_conditional(Path.cwd())
        return
    if args.association:
        from hlagent.analysis.hatk_association import run_association
        run_association(Path.cwd())
        return
    work = Path.cwd()
    dictionary = work / "dictionary"
    prefix = str(work / "output/markers")
    cleaned = str(work / "output/cohort")
    NomenCleaner(str(work / "input/cohort.hped"),
                 str(dictionary / "HLA_ALLELE_TABLE.imgt3320.hat"), cleaned, 2, _HLA_target=GENES)
    bMarkerGenerator(CHPED(cleaned + ".chped"), prefix, "18",
                     str(dictionary / "HLA_DICTIONARY_AA.hg18.imgt3320.txt"),
                     str(dictionary / "HLA_DICTIONARY_AA.hg18.imgt3320.map"),
                     str(dictionary / "HLA_DICTIONARY_SNPS.hg18.imgt3320.txt"),
                     str(dictionary / "HLA_DICTIONARY_SNPS.hg18.imgt3320.map"),
                     _variants=str(work / "input/genotypes"), _f_save_intermediates=True,
                     _plink="plink", _config={"_qc_parameters": QC})


if __name__ == "__main__":
    main()
