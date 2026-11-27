"""Reviewable local header proposals for binary association summaries."""

from collections import Counter
import json
from pathlib import Path
import re

from hlagent.analysis.hatk_custom_association import check_settings
from hlagent.runtime.phenotype_preparation import digest, read_table

ALIASES = {
    'CHR': {'chr', 'chrom', 'chromosome'},
    'SNP': {'snp', 'marker', 'markerid', 'variant', 'variantid', 'rsid'},
    'BP': {'bp', 'pos', 'position', 'basepair', 'basepairlocation'},
    'A1': {'a1', 'ea', 'effectallele'},
    'A2': {'a2', 'oa', 'nea', 'otherallele', 'noneffectallele'},
    'NMISS': {'nmiss', 'n', 'samplecount', 'nsamples', 'samplesize'},
    'EFFECT': {'or', 'oddsratio', 'logor', 'lnor', 'logoddsratio', 'beta', 'effect', 'estimate'},
    'SE': {'se', 'stderr', 'standarderror', 'error', 'logorse', 'selogor', 'lnorse', 'selnor', 'logoddsratiose'},
    'ESTIMABLE': {'estimable', 'isestimable', 'valid', 'flag'},
}
OR_NAMES = {'or', 'oddsratio'}
LOG_OR_NAMES = {'logor', 'lnor', 'logoddsratio'}
LOG_OR_SE_NAMES = {'logorse', 'selogor', 'lnorse', 'selnor', 'logoddsratiose'}


def normalized_name(name):
    return re.sub(r'[^a-z0-9]', '', name.casefold())


def prior_metadata(source, fingerprint, run_root):
    declarations = {}
    for record_path in Path(run_root).expanduser().resolve().glob('*/run.json'):
        try:
            record = json.loads(record_path.read_text())
            plan = record['plan']
            if record.get('status') != 'completed' or plan.get('stage') != 'summary_preparation':
                continue
            item, = plan['files']
            if (item['path'] != 'input/summary.table' or Path(item['source']).resolve() != source
                    or item['sha256'] != fingerprint):
                continue
            settings = plan['settings']
            check_settings(settings['phenotype_name'], settings['genome_build'])
            pair = (settings['phenotype_name'], settings['genome_build'])
            declarations.setdefault(pair, []).append(record_path.parent.name)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return [{'phenotype_name': trait, 'genome_build': build, 'runs': sorted(runs)}
            for (trait, build), runs in sorted(declarations.items())]


def suggest(source, delimiter, run_root=None):
    source = Path(source).expanduser().resolve()
    header, rows = read_table(source, delimiter)
    fingerprint = digest(source)
    candidates = {role: [name for name in header if normalized_name(name) in aliases]
                  for role, aliases in ALIASES.items()}
    columns = {role: names[0] for role, names in candidates.items() if len(names) == 1}
    counts = Counter(columns.values())
    columns = {role: name for role, name in columns.items() if counts[name] == 1}
    if not candidates['ESTIMABLE'] and set(columns.values()) == set(header):
        columns['ESTIMABLE'] = None
    proposed = {'columns': columns}
    effect = normalized_name(columns.get('EFFECT', ''))
    if effect in OR_NAMES:
        proposed['effect_scale'] = 'or'
    elif effect in LOG_OR_NAMES:
        proposed['effect_scale'] = 'log_or'
    if normalized_name(columns.get('SE', '')) in LOG_OR_SE_NAMES:
        proposed['se_scale'] = 'log_or'
    metadata = prior_metadata(source, fingerprint, run_root) if run_root is not None else []
    if len(metadata) == 1:
        proposed.update({key: metadata[0][key] for key in ('phenotype_name', 'genome_build')})
    unresolved = [role for role in ALIASES if role not in columns]
    unresolved += [key for key in ('effect_scale', 'se_scale', 'phenotype_name', 'genome_build') if key not in proposed]
    return {'method': 'local_header_proposal', 'delimiter': delimiter, 'header': header, 'rows': len(rows),
            'source_sha256': fingerprint, 'column_candidates': candidates, 'proposed': proposed,
            'unresolved': unresolved, 'prior_declarations': metadata}


def render(report):
    lines = ['Local summary proposals (review required, no files have been written):',
             f"Detected delimiter: {report['delimiter']} | Source rows: {report['rows']}", '']
    for role, name in report['proposed']['columns'].items():
        lines.append(f'- {role}: {name if name is not None else "none (no flag column, input statistics checks only)"}')
    for key in ('effect_scale', 'se_scale', 'phenotype_name', 'genome_build'):
        if key in report['proposed']:
            lines.append(f"- {key}: {report['proposed'][key]}")
    for role, names in report['column_candidates'].items():
        if len(names) > 1:
            lines.append(f'Ambiguous {role}: ' + ', '.join(names) + '. Choose manually.')
    if len(report['prior_declarations']) == 1:
        lines.append('Trait/build are prior user declarations for this exact source path and hash, not independent verification. Runs: ' +
                     ', '.join(report['prior_declarations'][0]['runs']))
    elif len(report['prior_declarations']) > 1:
        lines.append('Prior trait/build declarations conflict. Neither is proposed.')
    lines.append('Unresolved: ' + (', '.join(report['unresolved']) or 'none'))
    lines.append('\nHeader names suggest meaning only.\n'
                 'BETA/Estimate do not establish log(OR). Generic SE does not establish its scale.\n'
                 'Trait/build are not guessed from filenames, values or chromosome coordinates.')
    lines.append('\nChoosing accept does not approve file creation. The final plan still requires y.')
    return '\n'.join(lines)
