"""Bounded local artifact access for the UI, not an arbitrary file server."""

import base64
import hashlib
from html import escape
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import shutil
import zipfile
from urllib.parse import unquote

from hlagent.paths import ROOT
from hlagent.runtime.presentation import stage_title
from hlagent.runtime.run_metadata import read_metadata, utc_text, utc_timestamp
from hlagent.runtime.run_visibility import is_archived


MAX_DOWNLOAD = 100 * 1024 * 1024
TABLES = {'RA.results.tsv', 'association.results.tsv', 'conditional.comparison.tsv', 'RA.comparison.tsv',
          'stepwise.selection.tsv', 'stepwise.selected-markers.txt', 'omnibus', 'meta.tsv', 'RA.meta.tsv',
          'phenotype.phe', 'study.tsv', 'cohort.hped'}


def contained(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('This file is outside the selected result directory.')
    return path


def saved_runs(session_root, include_cli=False, include_web=False):
    roots = [Path(session_root)] + ([ROOT / 'runs'] if include_cli else [])
    if include_web:
        roots.extend(path for path in (ROOT / 'runs/web-sessions').glob('session-*')
                     if path.is_dir() and not path.is_symlink())
    entries = {}
    for root in roots:
        for record_path in root.glob('*/run.json'):
            try:
                contained(root, record_path)
                if is_archived(record_path.parent):
                    continue
                record = read_metadata(record_path)
                plan = record.get('plan', {})
                stage = plan.get('stage') or plan.get('example') or 'cookhla'
                label = (stage_title(stage) + ' | ' + utc_text(record.get('started_at'), date_only=True)
                         + ' | ' + record.get('status', 'unknown') + ' | ' + record_path.parent.name)
                entries[record_path.parent.resolve()] = label
            except (OSError, ValueError, TypeError):
                continue
    return sorted(entries.items(), key=lambda row: row[0].stat().st_mtime, reverse=True)


def analysis_fingerprint(directory):
    return hashlib.sha256(read_file(directory, Path(directory) / 'run.json', 4 * 1024 * 1024)).hexdigest()


def delete_analysis(session_root, directory, expected_fingerprint, include_cli=False, include_web=False):
    directory = Path(directory)
    allowed = dict(saved_runs(session_root, include_cli, include_web))
    if directory.is_symlink() or directory.resolve() not in allowed:
        raise ValueError('This analysis is no longer available in the selected list.')
    directory = directory.resolve()
    if analysis_fingerprint(directory) != expected_fingerprint:
        raise ValueError('The analysis record changed. Review it again before deleting.')
    record = read_metadata(directory / 'run.json')
    if record.get('status') not in {'completed', 'failed', 'cancelled', 'native_validation'}:
        raise ValueError('Only finished analyses can be deleted. Running or uncertain runs must be kept.')
    if any(path != directory / 'run.json' for path in directory.rglob('run.json')) or any(directory.rglob('report.json')):
        raise ValueError('This directory contains other analyses or reports and cannot be deleted as one analysis.')
    shutil.rmtree(directory)


def result_files(run):
    run = Path(run)
    record = read_metadata(run / 'run.json')
    paths = []
    for item in record.get('outputs', []):
        name = item.get('path', '')
        path = contained(run, run / name)
        if path.is_file() and (path.suffix.lower() in ('.png', '.pdf') or path.name in TABLES):
            paths.append(path)
    return paths


def read_file(root, path, limit=MAX_DOWNLOAD):
    path = contained(root, path)
    if path.stat().st_size > limit:
        raise ValueError('This file is too large for a browser download. Open it on the server.')
    return path.read_bytes()


def log_tail(run):
    lines = []
    for name in ('hatk.log', 'cookhla.log', 'output/beagle.log'):
        path = contained(run, Path(run) / name)
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                lines.append(name + '\n' + stream.read(65536).decode('utf-8', errors='replace'))
    text = '\n\n'.join(lines) or 'No tool log is available yet.'
    from hlagent.runtime.settings import read_settings
    for key, value in read_settings().items():
        if value and re.search(r'API_KEY|(?:^|_)(?:TOKEN|PASSWORD|SECRET)(?:_|$)', key.upper()):
            text = text.replace(str(value), '[redacted]')
    return text


def reports(session_root, include_cli=False, include_web=False):
    roots = [Path(session_root)] + ([ROOT / 'runs'] if include_cli else [])
    if include_web:
        web_root = ROOT / 'runs/web-sessions'
        roots.extend(path for path in web_root.glob('session-*') if path.is_dir() and not path.is_symlink())
    found = set()
    for root in roots:
        for pattern in ('reports/*/report.json', 'ra-example-*/report/report.json'):
            for path in root.glob(pattern):
                try:
                    contained(root, path)
                    if not any(is_archived(directory) for directory in (path.parent, path.parent.parent)) and (path.parent / 'index.html').is_file():
                        found.add(path.parent)
                except ValueError:
                    continue
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def report_labels(directories, session_root):
    labels, counts = {}, {}
    for directory in directories:
        record = read_metadata(directory / 'report.json')
        title = 'RA example' if directory.parent.name.startswith('ra-example-') else 'Analysis report'
        stamp = utc_timestamp(record.get('created_at'))
        date = stamp.strftime('%Y-%m-%d %H:%M UTC') if stamp else 'Date not recorded'
        count = len(record.get('runs', []))
        origin = ('This session' if directory.is_relative_to(session_root) else
                  'Previous Web session' if directory.is_relative_to(ROOT / 'runs/web-sessions') else 'CLI')
        label = f'{title} | {date} | {count} ' + ('analysis' if count == 1 else 'analyses') + ' | ' + origin
        counts[label] = counts.get(label, 0) + 1
        labels[directory] = label + (f' #{counts[label]}' if counts[label] > 1 else '')
    return labels


def report_fingerprint(directory):
    return hashlib.sha256(read_file(directory, Path(directory) / 'report.json', 4 * 1024 * 1024)).hexdigest()


def delete_report(session_root, directory, expected_fingerprint, include_cli=False, include_web=False):
    directory = Path(directory)
    allowed = reports(session_root, include_cli, include_web)
    if directory not in allowed or directory.is_symlink():
        raise ValueError('This report is no longer available in the selected list.')
    if report_fingerprint(directory) != expected_fingerprint:
        raise ValueError('The report changed. Review it again before deleting.')
    target = directory.resolve()
    record = read_metadata(directory / 'report.json')
    if any(directory.rglob('run.json')):
        raise ValueError('This directory contains analysis records and cannot be deleted as a report.')
    for run in record.get('runs', []):
        if run.get('run_directory'):
            source = Path(run['run_directory']).resolve()
            if source.is_relative_to(target) or target.is_relative_to(source):
                raise ValueError('This report overlaps an analysis directory and cannot be deleted.')
    shutil.rmtree(directory)


def report_files(directory):
    directory = Path(directory)
    manifest = json.loads(read_file(directory, directory / 'report.json', 4 * 1024 * 1024))
    files = [directory / 'index.html', directory / 'report.json']
    for index, run in enumerate(manifest['runs'], 1):
        for item in run.get('artifacts', []):
            path = directory / 'assets' / str(index) / Path(item['path']).name
            files.append(contained(directory, path))
    return list(dict.fromkeys(files))


class ReportPreview(HTMLParser):
    """Display saved HTML without scripts, remote resources or local-file links."""

    tags = {'html', 'head', 'body', 'main', 'header', 'footer', 'section', 'nav', 'div', 'span', 'p',
            'h1', 'h2', 'h3', 'h4', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'li',
            'a', 'img', 'figure', 'figcaption', 'details', 'summary', 'pre', 'code', 'br', 'strong', 'style'}

    def __init__(self, directory):
        super().__init__(convert_charrefs=True)
        self.directory = Path(directory)
        self.allowed = set(report_files(directory))
        self.parts = ['<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                      'img-src data:; style-src \'unsafe-inline\'">'
                      '<base href="about:srcdoc">']
        self.skip = 0
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'iframe', 'object', 'svg', 'title'):
            self.skip += 1
        if self.skip or tag not in self.tags:
            return
        values = []
        download_name = None
        for name, value in attrs:
            if value is None:
                continue
            if name in ('class', 'id', 'title', 'alt'):
                values.append((name, value))
            elif tag == 'img' and name == 'src' and value.startswith('data:image/png;base64,'):
                values.append((name, value))
            elif tag == 'a' and name == 'href':
                href = value if value.startswith('#') else '#'
                if value.startswith('assets/'):
                    try:
                        path = contained(self.directory / 'assets', self.directory / unquote(value))
                    except ValueError:
                        path = None
                    if path in self.allowed:
                        raw = read_file(self.directory, path, 8 * 1024 * 1024)
                        download_name = 'omnibus.results.tsv' if path.name == 'omnibus' else path.name
                        mime = {'.pdf': 'application/pdf', '.tsv': 'text/tab-separated-values;charset=utf-8',
                                '.png': 'image/png'}.get(Path(download_name).suffix.lower(), 'application/octet-stream')
                        href = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode()
                values.append((name, href))
        if download_name is not None:
            values.append(('download', download_name))
        self.parts.append('<' + tag + ''.join(' ' + key + '="' + escape(val, quote=True) + '"' for key, val in values) + '>')
        self.in_style = tag == 'style' or self.in_style

    def handle_endtag(self, tag):
        if self.skip:
            if tag in ('script', 'iframe', 'object', 'svg', 'title'):
                self.skip -= 1
            return
        if tag in self.tags:
            self.parts.append('</' + tag + '>')
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data if self.in_style else escape(data))


def preview_report(directory):
    parser = ReportPreview(directory)
    text = read_file(directory, Path(directory) / 'index.html', 16 * 1024 * 1024).decode('utf-8')
    # Apply presentation fixes to older reports without rewriting saved files.
    def move_pdf_links(match):
        section = match.group(0)
        pdfs = {}

        def caption(match):
            content = match.group(0)
            for href in re.findall(r'href="([^"]+\.pdf)"', content):
                pdfs[href] = Path(unquote(href)).name
            return re.sub(r'\s*<a\b[^>]*>Download PDF</a>', '', content)

        section = re.sub(r'<figcaption\b[^>]*>.*?</figcaption>', caption, section, flags=re.DOTALL)
        missing = ''.join(f'<li><a href="{escape(href, quote=True)}">{escape(name)}</a></li>'
                          for href, name in pdfs.items() if f'href="{href}"' not in section)
        if missing:
            if '<h3>Result files</h3><ul>' in section:
                section = section.replace('<h3>Result files</h3><ul>', '<h3>Result files</h3><ul>' + missing, 1)
            else:
                section = section.replace('</section>', '<h3>Result files</h3><ul>' + missing + '</ul></section>', 1)
        return section

    text = re.sub(r'<section\b[^>]*>.*?</section>', move_pdf_links, text, flags=re.DOTALL)
    text = text.replace('>omnibus</a>', '>omnibus.results.tsv</a>')
    parser.feed(text)
    return ''.join(parser.parts)


def report_zip(directory):
    files = report_files(directory)
    if sum(path.stat().st_size for path in files) > MAX_DOWNLOAD:
        raise ValueError('This report is too large for a browser ZIP. Copy its directory on the server.')
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.writestr(str(path.relative_to(directory)), read_file(directory, path))
    return result.getvalue()
