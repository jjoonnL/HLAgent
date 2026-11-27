"""Local phenotype guidance; analysis validation and coding remain unchanged."""

from pathlib import Path

from hlagent.analysis.hatk_association import phenotype_rows


PHENOTYPE_BRIEF = (
    'Choose a phenotype by number, or enter its file path.\n'
    'Use the phenotype table from your clinical/research data.\n\n'
    'Required format\n'
    '  IDs: FID/IID must match the selected panel/FAM.\n'
    '  Trait: a named column with 1=control, 2=case.\n'
    '  Both groups are required. Missing values are unsupported.'
)


PHENOTYPE_HELP = (
    'Supply a whitespace-delimited phenotype table from your clinical/research data.\n'
    'HLAgent does not generate disease labels from genotypes.\n\n'
    'Format example only:\nFID IID Trait\nfamily1 sample1 1\nfamily2 sample2 2\n\n'
    'Required:\n'
    '- The header must start with FID IID, followed by named phenotype columns.\n'
    '- IDs must match the selected panel/FAM.\n'
    '- 1=control and 2=case. Both groups are required.\n'
    '- Missing phenotype values are not supported.\n'
    'Other coding must be prepared from known clinical definitions, not guessed.\n\n'
    'Prepare: use prepare-phenotype at Supervisor to create a table from CSV/TSV.\n'
    'File selection: enter a listed number, or a path including its directory.\n'
    'A displayed filename alone does not select a candidate.\n'
    'No file or trait is selected automatically.\n\n'

)


def phenotype_columns(path):
    with Path(path).open() as stream:
        header = stream.readline().split()
    if len(header) < 3 or header[:2] != ['FID', 'IID'] or len(set(header)) != len(header):
        raise ValueError('Use a whitespace-delimited header FID IID followed by unique phenotype column names. CSV headers are not supported.')
    return header[2:]


def review_phenotype(fam, path, name):
    columns = phenotype_columns(path)
    if name not in columns:
        raise ValueError('Unknown phenotype column. Choose one of: ' + ', '.join(columns) + '.')
    index = columns.index(name) + 2
    values = {}
    with Path(path).open() as stream:
        next(stream)
        for number, line in enumerate(stream, 2):
            row = line.split()
            if len(row) != len(columns) + 2:
                raise ValueError(f'Phenotype row {number} has a different column count from the header. Correct the table and retry.')
            key = tuple(row[:2])
            if key in values:
                raise ValueError(f'Duplicate FID/IID pair at phenotype row {number}. Resolve duplicate records; no row is selected automatically.')
            if row[index] not in ('1', '2'):
                raise ValueError(f'Phenotype row {number} must use 1=control or 2=case. Missing/other codes are not accepted; correct coding from the clinical source.')
            values[key] = row[index]
    people = [line.split() for line in Path(fam).read_text().splitlines()]
    missing = sum(tuple(row[:2]) not in values for row in people)
    if missing:
        raise ValueError(f'{missing} panel/FAM samples have no matching phenotype. Use the phenotype from the same cohort and correct FID/IID; no samples are dropped automatically.')
    return phenotype_rows(fam, path, name)
