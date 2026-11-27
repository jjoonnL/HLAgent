"""Prepare checked two-field HATK dictionaries from a local IMGT snapshot."""

import hashlib
import json
from pathlib import Path
import re

from hlagent.analysis.hatk_custom_markers import check_dictionaries, dictionary_names
from hlagent.analysis.hatk_worker import GENES


def release_tag(value):
    value = str(value).strip()
    if re.fullmatch(r'3[0-9]{2,3}', value):
        return value
    match = re.fullmatch(r'v?(3)\.([0-9]{1,2})\.([0-9])(?:-alpha)?', value)
    if not match:
        raise ValueError('Enter an IMGT version such as 3.32.0 or 3320.')
    return ''.join(match.groups())


def release_name(tag):
    tag = release_tag(tag)
    return f'{tag[0]}.{tag[1:-1]}.{tag[-1]}'


def source_settings(directory):
    path = Path(directory).expanduser().resolve() / 'Allelelist.txt'
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError(f'Missing, empty or linked IMGT source file: {path}')
    with path.open() as stream:
        header = ''.join(stream.readline() for _ in range(10))
    match = re.search(r'#\s*version:\s*IPD-IMGT/HLA\s+(3\.[0-9]{1,2}\.[0-9])\s*(?:\n|$)', header)
    if not match:
        raise ValueError('Missing or unsupported IMGT version header in Allelelist.txt.')
    return {'genome_build': 18, 'imgt': release_tag(match.group(1))}


def source_names():
    names = ['Allelelist.txt', 'Nomenclature_2009.txt', 'wmda/hla_nom_g.txt', 'wmda/hla_nom_p.txt']
    for gene in GENES:
        for kind in ('prot', 'nuc', 'gen'):
            label = 'DRB' if gene == 'DRB1' and kind != 'gen' else gene
            names.append(f'alignments/{label}_{kind}.txt')
    return names


def source_files(directory, settings=None):
    directory = Path(directory).expanduser().resolve()
    settings = settings or source_settings(directory)
    release = release_name(settings['imgt'])
    files = []
    names = source_names()
    if (directory / 'acquisition.json').exists():
        names.append('acquisition.json')
    for name in names:
        path = directory / name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError(f'Missing, empty or linked IMGT source file: {path}')
        if not path.resolve().is_relative_to(directory):
            raise ValueError('IMGT source files must remain within the selected directory.')
        if name not in ('Nomenclature_2009.txt', 'acquisition.json'):
            with path.open() as stream:
                header = ''.join(stream.readline() for _ in range(10))
            if not re.search(rf'#\s*version:\s*IPD-IMGT/HLA\s+{re.escape(release)}\s*(?:\n|$)', header):
                raise ValueError(f'Expected an IMGT {release} version header: {path}')
        files.append({'path': 'input/imgthla/' + name, 'source': str(path),
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return files


def inspect_outputs(work):
    work = Path(work)
    record = json.loads((work / 'run.json').read_text())
    data = record['plan']['data']
    settings = data.get('settings', {})
    if (data.get('stage') != 'imgt2seq' or settings.get('genome_build') != 18
            or release_tag(settings.get('imgt')) != settings.get('imgt')):
        raise ValueError('Unsupported dictionary preparation settings.')
    staged = source_files(work / 'input/imgthla', settings)
    if [(r['path'], r['sha256']) for r in staged] != [(r['path'], r['sha256']) for r in data['files']]:
        raise ValueError('Staged IMGT source files changed.')
    keys, sizes = check_dictionaries(work / 'output', settings)
    for gene in GENES:
        path = work / 'output' / f"HLA_MAPTABLE_{gene}.hg18.imgt{settings['imgt']}.txt"
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f'Missing generated heatmap dictionary: {path.name}')
    return {'imgt': settings['imgt'], 'genome_build': 18, 'field_format': 2, 'genes': list(GENES),
            'alleles_by_dictionary': {kind: len(values) for kind, values in keys.items()},
            'positions_by_dictionary': {kind: sum(counts.values()) for kind, counts in sizes.items()},
            'source_files': len(staged), 'dictionary_files': len(dictionary_names(settings)) + len(GENES)}


def run_dictionary(work, vendor):
    from src.tools.HATK.IMGT2Seq.IMGT2Seq import IMGT2Seq
    work = Path(work)
    settings = json.loads((work / 'run.json').read_text())['plan']['data']['settings']
    IMGT2Seq(settings['imgt'], '18', str(work / 'output'), _imgt_dir=str(work / 'input/imgthla'),
             _MultiP=False, _f_save_intermediates=False, __Nfield_OUTPUT_FORMAT=2,
             _p_data=str(Path(vendor) / 'src/tools/HATK/IMGT2Seq/data_for_imgt2seq'), _HLA_target=GENES)
    summary = inspect_outputs(work)
    (work / 'output/imgt2seq.summary.json').write_text(json.dumps(summary, indent=2) + '\n')


def validate_dictionary_outputs(work):
    summary = inspect_outputs(work)
    saved = json.loads((Path(work) / 'output/imgt2seq.summary.json').read_text())
    if saved != summary:
        raise ValueError('Generated dictionary summary differs from checked files.')
    return summary
