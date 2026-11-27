"""Conservative, exact exclusions for missing ordinary-logistic test statistics."""

from collections import Counter
from pathlib import Path


def read_columns(work, names, sample_count, prefix=None):
    """Decode BIM A1 integer dosages without changing any input file."""
    prefix = Path(prefix) if prefix is not None else Path(work) / 'input/markers'
    markers = [line.split() for line in Path(str(prefix) + '.bim').read_text().splitlines()]
    indices = {row[1]: i for i, row in enumerate(markers)}
    width = (sample_count + 3) // 4
    path = Path(str(prefix) + '.bed')
    if path.stat().st_size != 3 + len(markers) * width:
        raise ValueError('Stepwise diagnostic BED dimensions differ from BIM/FAM.')
    columns = {}
    with path.open('rb') as stream:
        if stream.read(3) != b'\x6c\x1b\x01':
            raise ValueError('Stepwise diagnostics require variant-major BED.')
        for name in dict.fromkeys(names):
            stream.seek(3 + indices[name] * width)
            block = stream.read(width)
            columns[name] = [(2, None, 1, 0)[(block[i // 4] >> (2 * (i % 4))) & 3]
                             for i in range(sample_count)]
    return columns


def classify(values, outcomes, reported_n):
    """Certify only simple structural exclusions. All other failures stay unresolved."""
    result = {'complete_samples': len(values), 'reason': 'unresolved'}
    if len(values) != reported_n:
        result['reason'] = 'sample_mask_mismatch'
        return result
    groups = [[g for g, y in zip(values, outcomes) if y == label] for label in (0, 1)]
    if not all(groups):
        result['reason'] = 'no_samples_or_single_outcome'
    elif len(set(values)) == 1:
        result['reason'] = 'constant_target'
    else:
        direction = None
        if max(groups[0]) <= min(groups[1]):
            direction = [-(max(groups[0]) + min(groups[1])), 2]
        elif max(groups[1]) <= min(groups[0]):
            direction = [max(groups[1]) + min(groups[0]), -2]
        if direction is not None:
            margins = [(2 * y - 1) * (direction[0] + direction[1] * g)
                       for g, y in zip(values, outcomes)]
            if min(margins) >= 0 and max(margins) > 0:
                result.update(reason='exact_marginal_separation', integer_direction=direction,
                              strict_margin_samples=sum(m > 0 for m in margins))
    return result


def diagnose(work, rows, conditions, aligned):
    failed = [row for row in rows if not row['ESTIMABLE']]
    if not failed:
        return {'counts': {}, 'unresolved_marker_ids': [], 'markers': {}}
    columns = read_columns(work, [*conditions, *(row['SNP'] for row in failed)], len(aligned))
    condition_keep = [i for i in range(len(aligned)) if all(columns[n][i] is not None for n in conditions)]
    outcomes = [int(row[2]) - 1 for row in aligned]
    markers = {}
    for row in failed:
        used = [i for i in condition_keep if columns[row['SNP']][i] is not None]
        markers[row['SNP']] = classify([columns[row['SNP']][i] for i in used],
                                       [outcomes[i] for i in used], int(float(row['NMISS'])))
    return {'counts': dict(Counter(item['reason'] for item in markers.values())),
            'unresolved_marker_ids': [name for name, item in markers.items()
                                      if item['reason'] in ('unresolved', 'sample_mask_mismatch')],
            'markers': markers}
