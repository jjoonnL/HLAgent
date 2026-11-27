"""Check user-supplied CookHLA inputs without starting an analysis."""

import argparse
from pathlib import Path
import sys


REFERENCE_SUFFIXES = (".bed", ".bim", ".fam", ".bgl.phased", ".markers", ".FRQ.frq")


def require_file(path):
    path = Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f"Missing or empty file: {path}")
    return path


def check_plink(prefix):
    """Check variant-major BED dimensions and basic BIM/FAM structure."""
    prefix = str(Path(prefix).expanduser().resolve())
    bed, bim, fam = [require_file(prefix + suffix) for suffix in (".bed", ".bim", ".fam")]
    samples = set()
    with fam.open() as stream:
        for number, line in enumerate(stream, 1):
            fields = line.split()
            if len(fields) != 6:
                raise ValueError(f"FAM line {number} must have 6 columns: {fam}")
            sample = tuple(fields[:2])
            if sample in samples:
                raise ValueError(f"Duplicate FID/IID pair at FAM line {number}: {fam}")
            samples.add(sample)
    variants = chromosome6 = 0
    genes = set()
    with bim.open() as stream:
        for number, line in enumerate(stream, 1):
            fields = line.split()
            if len(fields) != 6:
                raise ValueError(f"BIM line {number} must have 6 columns: {bim}")
            try:
                if int(fields[3]) < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(f"Invalid base position at BIM line {number}: {bim}") from None
            variants += 1
            if fields[0].lower() in ("6", "chr6"):
                chromosome6 += 1
            parts = fields[1].split("_")
            if len(parts) == 3 and parts[0] == "HLA" and parts[2].isdigit():
                genes.add(parts[1])
    if not samples or not variants:
        raise ValueError("The PLINK dataset must contain samples and variants.")
    if not chromosome6:
        raise ValueError(f"No chromosome 6 variants were found: {bim}")
    with bed.open("rb") as stream:
        if stream.read(3) != b"\x6c\x1b\x01":
            raise ValueError(f"Expected a variant-major PLINK BED file: {bed}")
    expected_size = 3 + variants * ((len(samples) + 3) // 4)
    if bed.stat().st_size != expected_size:
        raise ValueError(f"BED size does not match BIM/FAM dimensions: {bed}")
    return {"prefix": prefix, "samples": len(samples), "variants": variants,
            "chromosome6_variants": chromosome6, "hla_marker_genes": sorted(genes)}


def check_inputs(input_prefix, reference_prefix, genome_build, genetic_map=None, error_rate=None):
    """Check supported file structure, not genomic build or scientific suitability."""
    if genome_build not in (18, 19, 38):
        raise ValueError("Specify target genome build 18, 19, or 38.")
    if (genetic_map is None) != (error_rate is None):
        raise ValueError("Provide both genetic map and error-rate files, or neither.")
    target = check_plink(input_prefix)
    reference = check_plink(reference_prefix)
    for suffix in REFERENCE_SUFFIXES[3:]:
        require_file(reference["prefix"] + suffix)
    if not reference["hla_marker_genes"]:
        raise ValueError("No HLA_<gene>_<allele> markers were found in the reference BIM.")
    if genetic_map is not None:
        genetic_map = str(require_file(Path(genetic_map).expanduser().resolve()))
        error_rate = str(require_file(Path(error_rate).expanduser().resolve()))
        agm = "supplied"
    else:
        required_reference = 100 if target["samples"] >= 100 else 200
        if reference["samples"] < required_reference:
            raise ValueError(f"Automatic AGM needs at least {required_reference} reference samples for this target.")
        agm = "generate"
    return {"target": target, "target_genome_build": genome_build, "reference": reference,
            "reference_genome_build_assumed": 18, "agm": agm,
            "genetic_map": genetic_map, "error_rate": error_rate}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-prefix", required=True, help="Target PLINK prefix without .bed/.bim/.fam.")
    parser.add_argument("--reference-prefix", required=True, help="SNP2HLA-format reference panel prefix.")
    parser.add_argument("--hg", type=int, choices=(18, 19, 38), required=True, help="Declared target genome build.")
    parser.add_argument("--genetic-map", help="Previously generated map for this target/reference pair.")
    parser.add_argument("--error-rate", help="Matching AGM error-rate file.")
    args = parser.parse_args()
    try:
        checked = check_inputs(args.input_prefix, args.reference_prefix, args.hg,
                               args.genetic_map, args.error_rate)
    except (OSError, ValueError) as error:
        print(f"Input check failed: {error}", file=sys.stderr)
        return 1
    for label in ("target", "reference"):
        data = checked[label]
        print(f"{label.capitalize()}: {data['prefix']}")
        print(f"  Samples: {data['samples']}; variants: {data['variants']}; chromosome 6: {data['chromosome6_variants']}")
    print(f"Declared target build: hg{args.hg}; reference build assumed: hg18")
    print("HLA marker genes in reference: " + ", ".join(checked["reference"]["hla_marker_genes"]))
    if checked["agm"] == "supplied":
        print("AGM: supplied map and error rates; their suitability for this target/reference pair is not verified.")
    else:
        print("AGM: a new map would be generated during analysis; this can take substantial time.")
    print("Basic input checks passed. Genomic builds, marker alignment, phasing content, and prediction accuracy are not verified.")
    print("Check only. No files were changed and no analysis was started.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
