"""Phase a HATK marker panel using checked BEAGLE 3.3.2 input/output mappings."""

from hlagent.paths import ROOT

import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

from hlagent.analysis.cookhla_inputs import check_plink
from hlagent.analysis.hatk_association import marker_table
from hlagent.analysis.hatk_worker import GENES


BEAGLE_SHA256 = 'c20bb952626a06cae044062de37a036a1a8bb23bb395105a16eb764c64a88ff0'
BEAGLE_URL = 'https://faculty.washington.edu/browning/beagle/beagle.jar'
AA_PATTERN = re.compile(r'(AA_[A-Z0-9]+_-?\d+)_(\d+)_exon\d+(?:_([A-Z.*-]+))?')


def phase_settings(genome_build, memory='4g', seed=1):
    if type(genome_build) is not int or genome_build not in (18, 19, 38):
        raise ValueError('Declare panel genome build 18, 19, or 38.')
    if not isinstance(memory, str) or not re.fullmatch(r'[1-9]\d*[mg]', memory):
        raise ValueError('Java memory must be a positive integer followed by m or g.')
    if type(seed) is not int or not -2147483648 <= seed <= 2147483647:
        raise ValueError('BEAGLE seed must be a signed 32-bit integer.')
    return {'genome_build': genome_build, 'memory': memory, 'seed': seed}


def phase_inputs(prefix):
    panel = check_plink(prefix)
    prefix = panel['prefix']
    markers = marker_table(prefix + '.bim')
    fam = [line.split() for line in Path(prefix + '.fam').read_text().splitlines()]
    if len({row[1] for row in fam}) != len(fam) or any(row[2:4] != ['0', '0'] for row in fam):
        raise ValueError('This phasing route requires unique IIDs and founder-only FAM (PID=MID=0).')
    if any(row[4] not in ('0', '1', '2') for row in fam):
        raise ValueError('FAM sex codes must be 0/1/2.')
    groups = {}
    group_coordinates = {}
    for name, row in markers.items():
        if row['BP'] <= 0 or row['A1'] in ('0', 'NA') or row['A2'] in ('0', 'NA') or row['A1'] == row['A2']:
            raise ValueError('Phasing requires positive coordinates and two distinct nonmissing BIM allele labels.')
        if name.startswith('AA_'):
            match = AA_PATTERN.fullmatch(name)
            if match is None or int(match[2]) != row['BP'] or match[1].split('_')[1] not in GENES:
                raise ValueError('AA marker name/coordinate does not match the BIM.')
            group_coordinates.setdefault(match[1], set()).add(row['BP'])
            if match[3] is None:
                valid_labels = all(re.fullmatch(r'[A-Z.*-]', row[key]) for key in ('A1', 'A2'))
            else:
                valid_labels = {row['A1'], row['A2']} == {'p', 'a'}
            if not valid_labels:
                raise ValueError('AA allele labels do not match direct-residue or presence/absence coding.')
            groups.setdefault(match[1], []).append(match[3])
    if not groups or len(markers) < 2:
        raise ValueError('A HATK panel containing AA markers and at least two markers is required.')
    for name, residues in groups.items():
        if None in residues:
            valid = len(residues) == 1
        else:
            singles = [r for r in residues if len(r) == 1]
            valid = len(singles) >= 2 and len(singles) == len(set(singles))
        if not valid or len(group_coordinates[name]) != 1:
            raise ValueError('AA position lacks complete residue definitions after panel QC; cannot prepare Node4 input.')
    return panel, fam, markers


def bed_rows(prefix, samples, variants):
    """Yield PLINK two-bit codes: 00=A1/A1, 01=missing, 10=A1/A2, 11=A2/A2."""
    stride = (samples + 3) // 4
    with Path(str(prefix) + '.bed').open('rb') as stream:
        if stream.read(3) != b'\x6c\x1b\x01':
            raise ValueError('Expected variant-major BED.')
        for _ in range(variants):
            block = stream.read(stride)
            if len(block) != stride:
                raise ValueError('Truncated BED block.')
            yield [(block[i // 4] >> (2 * (i % 4))) & 3 for i in range(samples)]
        if stream.read(1):
            raise ValueError('Unexpected trailing BED data.')


def phase_input_text(prefix):
    panel, fam, markers = phase_inputs(prefix)
    # Match HATK's G=A2/C=A1 coding and first-free coordinate adjustment.
    occupied = set()
    rows = []
    codes = ('CC', '00', 'CG', 'GG')
    header = 'I id ' + ' '.join(row[1] for row in fam for _ in (0, 1)) + '\n'
    for (name, marker), genotypes in zip(markers.items(), bed_rows(prefix, len(fam), len(markers))):
        bp = marker['BP']
        while bp in occupied:
            bp += 1
        occupied.add(bp)
        rows.append((bp, name, ' '.join(allele for code in genotypes for allele in codes[code])))
    rows.sort()
    bgl = header + ''.join(f'M {name} {calls}\n' for _, name, calls in rows)
    positions = ''.join(f'{name} {bp} G C\n' for bp, name, _ in rows)
    return bgl, positions, sum(bp != markers[name]['BP'] for bp, name, _ in rows)


def settings_from_run(work):
    work = Path(work)
    plan = json.loads((work / 'run.json').read_text())['plan']
    settings = json.loads((work / 'input/phasing-settings.json').read_text())
    if (plan.get('example') != 'hatk_phasing' or plan.get('data', {}).get('settings') != settings
            or settings != phase_settings(**settings)):
        raise ValueError('Phasing settings differ from the reviewed plan.')
    files = plan['data']['files']
    if len(files) != 3 or {item['path'] for item in files} != {'input/markers' + ext for ext in ('.bed', '.bim', '.fam')}:
        raise ValueError('Invalid phasing input record.')
    for item in files:
        if hashlib.sha256((work / item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Phasing input differs from its reviewed hash.')
    return settings


def phase_command(work, jar, settings=None):
    settings = settings_from_run(work) if settings is None else settings
    output = Path(work).resolve() / 'output'
    return ['java', '-Xmx' + settings['memory'], '-jar', str(jar),
            'unphased=' + str(output / 'unphased.bgl'), 'markers=' + str(output / 'beagle.markers'),
            'out=' + str(output / 'phased'), 'missing=0', 'seed=' + str(settings['seed']), 'gprobs=false']


def decoded_phase(work):
    work = Path(work)
    panel, fam, markers = phase_inputs(work / 'input/markers')
    bgl, positions, moved = phase_input_text(work / 'input/markers')
    if ((work / 'output/unphased.bgl').read_text() != bgl
            or (work / 'output/beagle.markers').read_text() != positions):
        raise ValueError('BEAGLE input differs from the reviewed BED/coding/positions.')
    by_name = {}
    missing_genotypes = 0
    for (name, marker), codes in zip(markers.items(), bed_rows(work / 'input/markers', len(fam), len(markers))):
        by_name[name] = codes
        missing_genotypes += codes.count(1)
    headers = [' '.join([a, b] + [row[index] for row in fam for _ in (0, 1)]) + '\n'
               for a, b, index in (('P', 'pedigree', 0), ('I', 'id', 1), ('fID', 'father', 2),
                                   ('mID', 'mother', 3), ('C', 'gender', 4))]
    full, aa, seen = [], [], set()
    with gzip.open(work / 'output/phased.unphased.bgl.phased.gz', 'rt') as stream:
        if stream.readline().strip() != bgl.splitlines()[0]:
            raise ValueError('BEAGLE output sample-pair order differs from the FAM.')
        for line in stream:
            row = line.split()
            if (len(row) != 2 + len(fam) * 2 or row[0] != 'M' or row[1] not in markers
                    or row[1] in seen or any(a not in ('G', 'C') for a in row[2:])):
                raise ValueError('Malformed, missing, or unexpected phased marker/allele.')
            name = row[1]
            seen.add(name)
            for index, code in enumerate(by_name[name]):
                observed = sorted(row[2 + 2 * index:4 + 2 * index])
                expected = {0: ['C', 'C'], 2: ['C', 'G'], 3: ['G', 'G']}.get(code)
                if expected is not None and observed != expected:
                    raise ValueError('BEAGLE output changed a nonmissing input genotype.')
            marker = markers[name]
            decoded = [marker['A2'] if a == 'G' else marker['A1'] for a in row[2:]]
            text = ' '.join(['M', name] + decoded) + '\n'
            full.append(text)
            if name.startswith('AA_'):
                aa.append(text)
    if seen != set(markers):
        raise ValueError('BEAGLE output does not cover every input marker.')
    summary = {'samples': len(fam), 'markers': len(markers), 'aa_markers': len(aa),
               'imputed_missing_genotypes': missing_genotypes, 'adjusted_phasing_coordinates': moved,
               'engine': 'BEAGLE 3.3.2', 'original_coordinates_retained_in_marker_ids': True,
               'observed_genotypes_preserved': True, 'genome_build': settings_from_run(work)['genome_build']}
    return ''.join(headers + full), ''.join(headers + aa), summary


def run_phasing(work, jar=None):
    work = Path(work).resolve()
    settings_from_run(work)
    jar = Path(jar) if jar is not None else ROOT / 'dependency/beagle.jar'
    if hashlib.sha256(jar.read_bytes()).hexdigest() != BEAGLE_SHA256:
        raise ValueError('BEAGLE jar differs from the pinned original HLAgent BEAGLE 3.3.2 binary.')
    bgl, markers, _ = phase_input_text(work / 'input/markers')
    (work / 'output/unphased.bgl').write_text(bgl)
    (work / 'output/beagle.markers').write_text(markers)
    command = phase_command(work, jar)
    with (work / 'output/beagle.log').open('w') as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=subprocess.STDOUT)
    full, aa, summary = decoded_phase(work)
    (work / 'output/phased.bgl').write_text(full)
    (work / 'output/phased-aa.bgl').write_text(aa)
    (work / 'output/cohort.fam').write_bytes((work / 'input/markers.fam').read_bytes())
    (work / 'output/phasing.summary.json').write_text(json.dumps(
        {'summary': summary, 'command': command, 'beagle_sha256': BEAGLE_SHA256}, indent=2) + '\n')


def validate_phasing_outputs(work):
    work = Path(work)
    full, aa, summary = decoded_phase(work)
    saved = json.loads((work / 'output/phasing.summary.json').read_text())
    if (saved.get('summary') != summary or saved.get('beagle_sha256') != BEAGLE_SHA256
            or (work / 'output/phased.bgl').read_text() != full
            or (work / 'output/phased-aa.bgl').read_text() != aa
            or (work / 'output/cohort.fam').read_bytes() != (work / 'input/markers.fam').read_bytes()):
        raise ValueError('Phasing outputs differ from the checked BEAGLE calls/FAM.')
    command = saved.get('command', [])
    # Native validation can use the original jar path; all arguments except that location are fixed.
    if len(command) != 10 or command != phase_command(work, command[3]):
        if command != phase_command('/work', '/opt/hlagent/dependency/beagle.jar', settings_from_run(work)):
            raise ValueError('Phasing command differs from the reviewed settings.')
    return summary


def completed_phasing_inputs(run):
    run = Path(run).expanduser().resolve()
    record = json.loads((run / 'run.json').read_text())
    if record.get('status') != 'completed':
        raise ValueError('Select a completed HATK phasing run.')
    summary = validate_phasing_outputs(run)
    saved = {item['path']: item['sha256'] for item in record['outputs']}
    for name in ('output/phased-aa.bgl', 'output/cohort.fam'):
        if saved.get(name) != hashlib.sha256((run / name).read_bytes()).hexdigest():
            raise ValueError('Phasing result differs from its recorded output hash.')
    phased = omnibus_input_with_insertions(run, record, summary['genome_build'])
    return phased, run / 'output/cohort.fam', summary['genome_build']


def omnibus_input_with_insertions(run, record, genome_build):
    """Derive a separate Node4 input without rewriting the completed phasing run."""
    from hlagent.analysis.hatk_omnibus import insertion_coordinate

    run = Path(run)
    full = run / 'output/phased.bgl'
    insertions = [line for line in full.read_text().splitlines(keepends=True)
                  if line.startswith('M INS_AA_')]
    if not insertions:
        return run / 'output/phased-aa.bgl'
    saved = {item['path']: item['sha256'] for item in record['outputs']}
    if saved.get('output/phased.bgl') != hashlib.sha256(full.read_bytes()).hexdigest():
        raise ValueError('Full phasing result differs from its recorded output hash.')
    markers = marker_table(run / 'input/markers.bim')
    for line in insertions:
        row = line.split()
        if (insertion_coordinate(row[1]) != markers[row[1]]['BP']
                or {markers[row[1]]['A1'], markers[row[1]]['A2']} != {'z', 'Z'}
                or any(call not in ('z', 'Z') for call in row[2:])):
            raise ValueError('Insertion coordinate or z/Z states differ from the phased marker panel.')
    content = (run / 'output/phased-aa.bgl').read_bytes() + ''.join(insertions).encode()
    source_names = ('run.json', 'output/phased.bgl', 'output/phased-aa.bgl', 'output/cohort.fam')
    provenance = {'kind': 'phased_AA_and_insertions', 'source_run': str(run),
                  'genome_build': genome_build,
                  'sources': {name: hashlib.sha256((run / name).read_bytes()).hexdigest() for name in source_names},
                  'derived_sha256': hashlib.sha256(content).hexdigest(),
                  'insertion_ids': [line.split()[1] for line in insertions]}
    directory = ROOT / 'datasets/omnibus-inputs' / (run.name + '-' + provenance['derived_sha256'][:12])
    directory.mkdir(parents=True, exist_ok=True)
    phased = directory / 'phased-aa-insertions.bgl'
    for path, data in ((phased, content), (phased.with_suffix('.provenance.json'),
                       (json.dumps(provenance, indent=2) + '\n').encode())):
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError('Prepared omnibus input or provenance changed. Existing files were preserved.')
        else:
            with path.open('xb') as stream:
                stream.write(data)
    return phased


def checked_omnibus_provenance(phased, fam, genome_build):
    """Copy the checked source lineage into a new Node4 plan when a sidecar exists."""
    sidecar = Path(phased).with_suffix('.provenance.json')
    if not sidecar.exists():
        return None
    data = json.loads(sidecar.read_text())
    if (data.get('kind') != 'phased_AA_and_insertions' or data.get('genome_build') != genome_build
            or data.get('derived_sha256') != hashlib.sha256(Path(phased).read_bytes()).hexdigest()):
        raise ValueError('Derived omnibus input differs from its phasing provenance.')
    run = Path(data['source_run'])
    names = ('run.json', 'output/phased.bgl', 'output/phased-aa.bgl', 'output/cohort.fam')
    if set(data['sources']) != set(names):
        raise ValueError('Incomplete phasing provenance.')
    for name, digest in data['sources'].items():
        if hashlib.sha256((run / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Phasing provenance source changed after input preparation.')
    if hashlib.sha256(Path(fam).read_bytes()).hexdigest() != data['sources']['output/cohort.fam']:
        raise ValueError('Supplied FAM differs from the phasing provenance.')
    return {**data, 'manifest_sha256': hashlib.sha256(sidecar.read_bytes()).hexdigest()}
