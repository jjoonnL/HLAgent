"""Validate explicitly selected numeric covariates and align them by FID/IID."""

import math
from pathlib import Path
import re


def covariate_names(names, phenotype_name):
    if (not isinstance(names, (list, tuple)) or not names or len(set(names)) != len(names)
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
                   or name in ('FID', 'IID', phenotype_name) for name in names)):
        raise ValueError('Select unique covariate names using letters, digits and underscores. Exclude IDs and the trait.')
    return list(names)


def aligned_covariates(fam, source, names, phenotype_name):
    names = covariate_names(names, phenotype_name)
    rows = [line.split() for line in Path(source).read_text().splitlines()]
    if (not rows or rows[0][:2] != ['FID', 'IID'] or len(set(rows[0])) != len(rows[0])
            or not set(names).issubset(rows[0][2:])):
        raise ValueError('Covariate table must start with FID IID and include every selected column.')
    indices = [rows[0].index(name) for name in names]
    values = {}
    for row in rows[1:]:
        if len(row) != len(rows[0]) or tuple(row[:2]) in values:
            raise ValueError('Covariate rows must have matching column counts and unique FID/IID pairs.')
        values[tuple(row[:2])] = [row[i] for i in indices]
    ids = [line.split()[:2] for line in Path(fam).read_text().splitlines()]
    if not ids or any(len(pair) != 2 for pair in ids) or len({tuple(pair) for pair in ids}) != len(ids):
        raise ValueError('Empty or duplicate FAM sample IDs.')
    aligned, numeric = [], []
    for pair in ids:
        if tuple(pair) not in values:
            raise ValueError('Every panel sample must have matching covariates. No samples are dropped automatically.')
        selected = values[tuple(pair)]
        try:
            numbers = [float(value) for value in selected]
        except ValueError:
            raise ValueError('Selected covariates must be numeric. Encode categorical variables explicitly before use.') from None
        if any(not math.isfinite(value) or value == -9 for value in numbers):
            raise ValueError('Missing or nonfinite covariates are unsupported, including the PLINK missing code -9.')
        aligned.append(pair + selected)
        numeric.append(numbers)
    if len(ids) <= len(names) + 2:
        raise ValueError('Too few samples for the selected covariates and a marker effect.')
    # Check the intercept/covariate design without fitting any association model.
    basis = [[1 / math.sqrt(len(ids))] * len(ids)]
    for column in zip(*numeric):
        scale = max(abs(value) for value in column)
        vector = [value / scale for value in column] if scale else list(column)
        for _ in range(2):
            for base in basis:
                projection = sum(a * b for a, b in zip(vector, base))
                vector = [a - projection * b for a, b in zip(vector, base)]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 1e-10 * math.sqrt(len(ids)):
            raise ValueError('Constant or linearly dependent covariates. Review the selected columns and coding.')
        basis.append([value / norm for value in vector])
    text = 'FID IID ' + ' '.join(names) + '\n' + ''.join(' '.join(row) + '\n' for row in aligned)
    return text


def plan_covariates(data):
    """Rebuild keyword arguments while preserving legacy plans without covariates."""
    if not data.get('settings', {}).get('covariates'):
        return {}
    return {'covariate_file': data['covariate_file'], 'covariates': data['settings']['covariates']}
