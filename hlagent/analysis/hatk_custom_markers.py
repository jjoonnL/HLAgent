"""Generate two-field HATK markers from supplied HPED and existing dictionaries."""

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from hlagent.analysis.cookhla_inputs import check_plink, require_file
from hlagent.analysis.hatk_worker import GENES, QC

def pedigree(path, header=False):
    with Path(path).open() as stream:
        if header:
            expected = ["FID", "IID", "PID", "MID", "Sex", "Phe"] + [f"{g}_{i}" for g in GENES for i in (1, 2)]
            if stream.readline().split() != expected:
                raise ValueError("Unexpected CHPED header or HLA gene order.")
        rows = [line.split() for line in stream]
    if not rows or any(len(row) != 22 for row in rows):
        raise ValueError("Expected a nonempty HLA pedigree with 22 columns.")
    ids = [tuple(row[:2]) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate FID/IID pairs in HLA pedigree.")
    return rows


def marker_settings(genome_build, imgt):
    if type(genome_build) is not int or genome_build not in (18, 19, 38):
        raise ValueError("Specify genome build 18, 19, or 38.")
    if not isinstance(imgt, str) or not re.fullmatch(r"[0-9]{3,6}", imgt):
        raise ValueError("Specify the numeric IMGT filename tag, for example 3320.")
    return {"genome_build": genome_build, "imgt": imgt}


def dictionary_names(settings):
    build, imgt = settings['genome_build'], settings['imgt']
    return [f'HLA_ALLELE_TABLE.imgt{imgt}.hat'] + [
        f'HLA_DICTIONARY_{kind}.hg{build}.imgt{imgt}.{extension}'
        for kind in ('AA', 'SNPS') for extension in ('txt', 'map')]


def check_dictionaries(directory, settings):
    directory = Path(directory)
    for name in dictionary_names(settings):
        require_file(directory / name)
    table = directory / dictionary_names(settings)[0]
    with table.open() as stream:
        header = stream.readline().split()
        if (not header or header[0] != 'HLA' or len(set(header)) != len(header)
                or not {'AlleleID', 'STANDARD', 'OLD', 'Ggroup', 'Pgroup'}.issubset(header)):
            raise ValueError("HAT dictionary needs HLA, AlleleID, STANDARD, OLD, Ggroup and Pgroup columns.")
        genes = set()
        for line in stream:
            row = line.split()
            if len(row) != len(header):
                raise ValueError("Malformed HLA allele table row.")
            genes.add(row[0])
    if not set(GENES).issubset(genes):
        raise ValueError("Allele table must cover all eight standard HLA genes.")
    keys = {}
    sizes = {}
    for kind in ('AA', 'SNPS'):
        lengths = defaultdict(set)
        alleles = set()
        prefix = f'HLA_DICTIONARY_{kind}.hg{settings["genome_build"]}.imgt{settings["imgt"]}'
        for line in (directory / (prefix + '.txt')).read_text().splitlines():
            row = line.split()
            if len(row) != 2 or row[0] in alleles or '*' not in row[0]:
                raise ValueError("Sequence dictionary requires unique allele IDs and sequences.")
            allele, sequence = row
            alleles.add(allele)
            gene = allele.split('*')[0]
            if gene in GENES:
                if not re.fullmatch(re.escape(gene) + r'\*\d{2,}:\d{2,}[A-Z]?', allele):
                    raise ValueError("Use dictionaries with two-field allele keys.")
                lengths[gene].add(len(sequence))
        counts = Counter()
        labels = set()
        for line in (directory / (prefix + '.map')).read_text().splitlines():
            row = line.split()
            if len(row) != 4 or row[0] not in ('6', 'chr6') or row[1] in labels:
                raise ValueError("Dictionary MAP requires unique chromosome 6 marker IDs and four columns.")
            label = row[1].removeprefix('INS_').split('_')
            if len(label) < 3 or label[0] != kind or label[1] not in GENES or int(row[3]) <= 0:
                raise ValueError("Dictionary MAP marker type, gene, or coordinate is invalid.")
            labels.add(row[1])
            counts[label[1]] += 1
        if any(lengths[gene] != {counts[gene]} or counts[gene] <= 0 for gene in GENES):
            raise ValueError("Dictionary sequence lengths must match MAP row counts for each HLA gene.")
        keys[kind] = alleles
        sizes[kind] = dict(counts)
    return keys, sizes


def check_marker_inputs(hped, dictionary_dir, settings, snp_prefix=None):
    rows = pedigree(hped)
    if any(row[4] not in ('0', '1', '2') for row in rows):
        raise ValueError("HPED sex codes must be 0, 1, or 2.")
    for row in rows:
        for index, allele in enumerate(row[6:]):
            if '*' in allele and allele.split('*')[0] != GENES[index // 2]:
                raise ValueError("HPED allele gene prefix differs from the standard gene-column order.")
    if not any(allele != '0' for row in rows for allele in row[6:]):
        raise ValueError("HPED has no nonmissing HLA alleles.")
    _, sizes = check_dictionaries(dictionary_dir, settings)
    panel = None
    if snp_prefix is not None:
        panel = check_plink(snp_prefix)
        labels = set()
        for line in Path(panel['prefix'] + '.bim').read_text().splitlines():
            fields = line.split()
            if fields[0] not in ('6', 'chr6') or fields[1] in labels:
                raise ValueError("Optional SNP panel must have unique chromosome 6 marker IDs.")
            labels.add(fields[1])
        fam = [line.split() for line in Path(panel['prefix'] + '.fam').read_text().splitlines()]
        if {tuple(row[:2]): row[:6] for row in fam} != {tuple(row[:2]): row[:6] for row in rows}:
            raise ValueError("HPED and optional SNP FAM must have matching sample IDs and all six metadata fields.")
    return {'input_samples': len(rows), 'dictionary_positions': sizes, 'snp_panel': panel}


def settings_from_run(work):
    work = Path(work)
    settings = json.loads((work / 'input/marker-settings.json').read_text())
    if not isinstance(settings, dict) or set(settings) != {'genome_build', 'imgt'}:
        raise ValueError("Invalid marker-generation settings.")
    marker_settings(settings['genome_build'], settings['imgt'])
    plan = json.loads((work / 'run.json').read_text())['plan']
    if (plan.get('example') != 'hatk_node1_custom' or plan.get('data', {}).get('settings') != settings
            or plan.get('genome_build') != settings['genome_build'] or plan.get('imgt') != settings['imgt']
            or plan.get('field_format') != 2 or plan.get('qc') != QC or plan.get('genes') != list(GENES)):
        raise ValueError("Marker-generation settings differ from the reviewed plan.")
    required = {'input/cohort.hped', *('dictionary/' + name for name in dictionary_names(settings))}
    if plan['data'].get('snp_prefix') is not None:
        required.update('input/genotypes' + ext for ext in ('.bed', '.bim', '.fam'))
    files = plan['data'].get('files', [])
    if len(files) != len(required) or {item['path'] for item in files} != required:
        raise ValueError("Incomplete marker-generation input record.")
    for item in files:
        if hashlib.sha256((work / item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError("Marker-generation input differs from its reviewed hash.")
    return settings, plan['data'].get('snp_prefix') is not None


def checked_cleaned(work, settings):
    work = Path(work)
    original = pedigree(work / 'input/cohort.hped')
    cleaned = pedigree(work / 'output/cohort.chped', header=True)
    if [r[:6] for r in original] != [r[:6] for r in cleaned]:
        raise ValueError("NomenCleaner changed sample metadata/order.")
    sequences, _ = check_dictionaries(work / 'dictionary', settings)
    coverage = {kind: {'missing_allele_calls': 0, 'uncovered_sample_gene_pairs': 0, 'missing_alleles': {}}
                for kind in ('AA', 'SNPS')}
    for before, after in zip(original, cleaned):
        for index, allele in enumerate(after[6:]):
            if before[index + 6] != '0' and allele == '0':
                raise ValueError("NomenCleaner could not resolve a nonmissing allele; inspect its log and dictionary version.")
            if allele != '0':
                if not re.fullmatch(re.escape(GENES[index // 2]) + r'\*\d{2,}:\d{2,}[A-Z]?', allele):
                    raise ValueError("Unexpected cleaned allele format or gene column.")
        for kind in ('AA', 'SNPS'):
            for gene in range(len(GENES)):
                pair = after[6 + 2 * gene:8 + 2 * gene]
                missing = [allele for allele in pair if allele != '0' and allele not in sequences[kind]]
                coverage[kind]['missing_allele_calls'] += len(missing)
                coverage[kind]['uncovered_sample_gene_pairs'] += bool(missing)
                for allele in missing:
                    counts = coverage[kind]['missing_alleles']
                    counts[allele] = counts.get(allele, 0) + 1
    return original, cleaned, coverage


def call_spec(settings, has_snps):
    return {'modules': ['NomenCleaner', 'bMarkerGenerator'], 'field_format': 2,
            'settings': settings, 'genes': list(GENES), 'qc': QC,
            'dictionary_files': dictionary_names(settings), 'merge_supplied_snps': has_snps}


def checked_marker_outputs(work):
    work = Path(work).resolve()
    settings, has_snps = settings_from_run(work)
    check_marker_inputs(work / 'input/cohort.hped', work / 'dictionary', settings,
                        work / 'input/genotypes' if has_snps else None)
    original, _, coverage = checked_cleaned(work, settings)
    if json.loads((work / 'output/dictionary-coverage.json').read_text()) != coverage:
        raise ValueError("Dictionary coverage report differs from the supplied genotypes/dictionaries.")
    panel = check_plink(work / 'output/markers')
    metadata = {tuple(row[:2]): row[:5] for row in original}
    for line in (work / 'output/markers.fam').read_text().splitlines():
        row = line.split()
        if tuple(row[:2]) not in metadata or row[:5] != metadata[tuple(row[:2])]:
            raise ValueError("Generated panel has unexpected sample/pedigree/sex metadata.")
        if row[2:4] != ['0', '0']:
            raise ValueError("Generated panel contains a nonfounder after founder-only filtering.")
    counts = Counter()
    seen = set()
    for line in (work / 'output/markers.bim').read_text().splitlines():
        row = line.split()
        if row[0] not in ('6', 'chr6') or row[1] in seen:
            raise ValueError("Generated panel must have unique chromosome 6 markers.")
        seen.add(row[1])
        counts[next((kind for kind in ('HLA', 'AA', 'SNPS', 'INS') if row[1].startswith(kind + '_')), 'SNP')] += 1
    if json.loads((work / 'output/marker-generation.json').read_text()) != call_spec(settings, has_snps):
        raise ValueError("Recorded generation settings differ from the reviewed plan.")
    return {'input_samples': len(original), 'output_samples': panel['samples'],
            'removed_samples': len(original) - panel['samples'], 'markers': panel['variants'],
            'marker_classes': dict(counts), 'genome_build': settings['genome_build'], 'imgt': settings['imgt'],
            'field_format': 2, 'merged_supplied_snps': has_snps, 'qc': QC,
            'dictionary_coverage': {kind: {key: value for key, value in report.items() if key != 'missing_alleles'}
                                    for kind, report in coverage.items()}}


def run_custom_markers(work, source):
    work, source = Path(work).resolve(), Path(source).resolve()
    settings, has_snps = settings_from_run(work)
    check_marker_inputs(work / 'input/cohort.hped', work / 'dictionary', settings,
                        work / 'input/genotypes' if has_snps else None)
    os.environ['HLAGENT_MAIN_DIRECTORY'] = str(source)
    sys.path.insert(0, str(source))
    from src.tools.HATK.NomenCleaner.NomenCleaner import NomenCleaner
    from src.tools.HATK.NomenCleaner.src.CHPED import CHPED
    from src.tools.HATK.bMarkerGenerator.bMarkerGenerator import bMarkerGenerator
    names = dictionary_names(settings)
    dictionary = work / 'dictionary'
    NomenCleaner(str(work / 'input/cohort.hped'), str(dictionary / names[0]),
                 str(work / 'output/cohort'), 2, _HLA_target=GENES)
    _, _, coverage = checked_cleaned(work, settings)
    (work / 'output/dictionary-coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
    (work / 'output/marker-generation.json').write_text(json.dumps(call_spec(settings, has_snps), indent=2) + '\n')
    bMarkerGenerator(CHPED(str(work / 'output/cohort.chped')), str(work / 'output/markers'),
                     str(settings['genome_build']), *(str(dictionary / name) for name in names[1:]),
                     _variants=str(work / 'input/genotypes') if has_snps else None,
                     _f_save_intermediates=True, _plink='plink', _config={'_qc_parameters': QC})
    summary = checked_marker_outputs(work)
    (work / 'output/markers.summary.json').write_text(json.dumps(summary, indent=2) + '\n')


def validate_custom_marker_outputs(work):
    work = Path(work).resolve()
    summary = checked_marker_outputs(work)
    if json.loads((work / 'output/markers.summary.json').read_text()) != summary:
        raise ValueError("Marker summary differs from the checked output.")
    return summary
