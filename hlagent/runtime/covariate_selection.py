"""Reviewable covariate header hints. No coding or clinical meaning is inferred."""

import hashlib
from pathlib import Path
import re


def proposals(source, phenotype_name):
    raw = Path(source).read_bytes()
    lines = raw.decode('utf-8').splitlines()
    header = lines[0].split() if lines else []
    if header[:2] != ['FID', 'IID'] or len(set(header)) != len(header):
        raise ValueError('Expected a unique header starting with FID IID.')
    columns = [name for name in header[2:] if name != phenotype_name]
    hints = {}
    groups = {}
    for name in columns:
        normalized = re.sub(r'[^a-z0-9]', '', name.casefold())
        role = ('age' if normalized in ('age', 'ageyears', 'ageatbaseline', 'baselineage') else
                'sex' if normalized in ('sex', 'gender', 'biologicalsex') else None)
        if role:
            groups.setdefault(role, []).append(name)
            hints[name] = role + ' candidate'
        elif re.fullmatch(r'(?:pc|principalcomponent)0*[1-9][0-9]*', normalized):
            hints[name] = 'PC candidate. Select explicitly'
    ambiguous = [role for role, names in groups.items() if len(names) > 1]
    return {'source_sha256': hashlib.sha256(raw).hexdigest(), 'columns': columns, 'hints': hints, 'ambiguous': ambiguous}


def listing(proposal):
    lines = ['Choose covariate columns', 'Header hints are shown below. No columns are preselected.', '']
    for i, name in enumerate(proposal['columns'], 1):
        hint = proposal['hints'].get(name)
        lines.append(f'{i}. {name}' + (f' ({hint})' if hint else ''))
    lines.append('')
    if proposal['ambiguous']:
        lines.append('Multiple candidates for ' + ', '.join(proposal['ambiguous']) + '. Confirm their meaning and coding.')
    if not proposal['columns']:
        lines.append('No covariate columns are available. Type files to choose another table, or none to omit covariates.')
    else:
        lines.append('Select: enter every column to include, using numbers or names separated by spaces or commas.')
        indices = [0, 2] if len(proposal['columns']) >= 3 else list(range(len(proposal['columns'])))
        lines.append('Example: ' + ' '.join(str(i+1) for i in indices) + ' selects only ' +
                     ', '.join(proposal['columns'][i] for i in indices) + '.')
        lines.append('Only the columns you enter will be used.')
    return '\n'.join(lines)


def selected_columns(text, proposal):
    tokens = text.replace(',', ' ').split()
    names = []
    for token in tokens:
        if token.isdigit():
            number = int(token)
            if not 1 <= number <= len(proposal['columns']):
                raise ValueError('Choose a column number from the displayed list.')
            token = proposal['columns'][number - 1]
        if token not in proposal['columns']:
            raise ValueError('Enter the complete selection using displayed column numbers or names. Nothing is added automatically.')
        names.append(token)
    if not names:
        raise ValueError('No columns selected. Enter column numbers or names, or none to remove all covariates.')
    if len(names) != len(set(names)):
        raise ValueError('Select each covariate only once.')
    return names
