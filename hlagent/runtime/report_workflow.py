"""Suggest saved analysis steps using exact artifact hashes, without running tools."""

from datetime import datetime
from pathlib import Path

from hlagent.analysis.hatk import digest
from hlagent.runtime.run_visibility import is_archived
from hlagent.runtime.workflow_runs import checked_files, read_run


NODE1 = ('hatk_node1', 'hatk_node1_custom')
NODE2 = ('hatk_node2', 'hatk_node2_custom')
NODE3 = ('hatk_node3', 'hatk_node3_custom', 'hatk_stepwise')
NODE4 = ('hatk_node4', 'hatk_node4_custom')


def _stage(record):
    return record['plan'].get('example') or record['plan'].get('stage')


def _files(record, outputs=False):
    plan = record['plan']
    return record.get('outputs', []) if outputs else plan.get('data', {}).get('files', plan.get('files', []))


def _signature(record, names, outputs=False):
    values = []
    for name in names:
        found = [item['sha256'] for item in _files(record, outputs) if Path(item['path']).name == name]
        if len(found) != 1:
            return None
        values.append(found[0])
    return tuple(values)


def _panel(record, outputs=False):
    return _signature(record, ('markers.bed', 'markers.bim', 'markers.fam'), outputs)


def _trait(record):
    files = [item['sha256'] for item in _files(record)
             if Path(item['path']).name in ('phenotype.txt', 'RA.phe')]
    name = record['plan'].get('phenotype') or record['plan'].get('data', {}).get('settings', {}).get('phenotype_name')
    return (files[0], name) if len(files) == 1 and name else None


def _time(path, record, start=False):
    stamp = record.get('started_at') if start else record.get('finished_at') or record.get('started_at')
    try:
        return datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return (path / 'run.json').stat().st_mtime


def suggest_report_runs(root, anchor=None):
    """Propose one recent branch. The user reviews included runs before creation."""
    root = Path(root).expanduser().resolve()
    records = {}
    for path in root.glob('*/run.json'):
        try:
            run, record = read_run(path.parent)
            if run.is_relative_to(root) and record.get('status') == 'completed' and not record.get('checkpoint'):
                records[run] = record
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if anchor is None:
        visible = [run for run in records if not is_archived(run)]
        if not visible:
            return []
        anchor = max(visible, key=lambda run: (_time(run, records[run]), run.name))
    anchor = Path(anchor).expanduser().resolve()
    if anchor not in records:
        return []
    record = records[anchor]
    stage = _stage(record)
    build = record['plan'].get('genome_build')
    cutoff = _time(anchor, record, start=True)

    def latest(stages, predicate, before=cutoff):
        matches = [run for run, item in records.items() if _stage(item) in stages
                   and item['plan'].get('genome_build') == build and _time(run, item) <= before
                   and predicate(item)]
        return max(matches, key=lambda run: (_time(run, records[run]), run.name)) if matches else None

    phase = anchor if stage == 'hatk_phasing' else None
    if stage in NODE4:
        signature = _signature(record, ('phased-aa.bgl', 'cohort.fam'))
        if signature:
            phase = latest(('hatk_phasing',), lambda item: _signature(item, ('phased-aa.bgl', 'cohort.fam'), True) == signature)
    panel_record = records[phase] if phase else record
    panel = _panel(panel_record)
    parent = None
    if panel and (phase or stage in NODE2 + NODE3):
        before = _time(phase or anchor, panel_record, start=True)
        parent = latest(NODE1, lambda item: _panel(item, True) == panel, before)
    association = anchor if stage in NODE2 else None
    trait = _trait(record)
    if panel and trait and stage in NODE3 + NODE4:
        def same_adjustment(item):
            if stage not in NODE3:
                return True
            return (item['plan'].get('covariates', []) == record['plan'].get('covariates', [])
                    and _signature(item, ('covariates.txt',)) == _signature(record, ('covariates.txt',)))
        association = latest(NODE2, lambda item: _panel(item) == panel and _trait(item) == trait and same_adjustment(item))
    selected = list(dict.fromkeys(run for run in (parent, association, phase, anchor) if run))
    # Verify selected snapshots before offering them. Creation rechecks them again.
    for run in selected:
        item = records[run]
        checked_files(run, _files(item))
        checked_files(run, item.get('outputs', []))
        if not item.get('outputs'):
            raise ValueError('A proposed completed run has no recorded outputs.')
    return selected


def report_record_hashes(runs):
    return {str(run): digest(Path(run) / 'run.json') for run in runs}
