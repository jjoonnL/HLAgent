"""Acquire an IMGT release on the host after explicit user approval."""

from hlagent.paths import new_run_id

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

from hlagent.analysis.imgt_dictionary import release_tag, source_files, source_names, source_settings


API = 'https://api.github.com/repos/ANHIG/IMGTHLA/releases'


def download_request(value):
    value = value.strip()
    return 'latest' if value.lower() == 'latest' else release_tag(value)


def _json(url):
    request = Request(url, headers={'User-Agent': 'HLAgent', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=60) as stream:
        return json.load(stream)


def _release(requested):
    if requested == 'latest':
        return _json(API + '/latest')
    page = 1
    while True:
        releases = _json(f'{API}?per_page=100&page={page}')
        if not releases:
            raise ValueError('Requested IMGT release was not found on ANHIG/IMGTHLA.')
        for item in releases:
            if item.get('draft'):
                continue
            try:
                matches = release_tag(item['tag_name']) == requested
            except ValueError:
                continue
            if matches:
                return item
        page += 1


def download_source(requested, destination):
    """Download only source data. Never build a dictionary or overwrite a snapshot."""
    requested = download_request(requested)
    release = _release(requested)
    tag = release['tag_name']
    expected = release_tag(tag)
    if requested != 'latest' and expected != requested:
        raise ValueError('Resolved IMGT release does not match the request.')
    url = 'https://codeload.github.com/ANHIG/IMGTHLA/zip/refs/tags/' + quote(tag, safe='')
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (new_run_id('imgthla-' + tag, destination))
    # Extract only the 28 inputs into a temporary directory. ZIP paths never become local paths.
    with tempfile.TemporaryDirectory(prefix='.imgt-download-', dir=destination) as temporary:
        temporary = Path(temporary)
        archive_path = temporary / 'source.zip'
        request = Request(url, headers={'User-Agent': 'HLAgent'})
        checksum = hashlib.sha256()
        with urlopen(request, timeout=60) as stream, archive_path.open('xb') as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                checksum.update(chunk)
        source = temporary / 'source'
        try:
            archive = zipfile.ZipFile(archive_path)
        except zipfile.BadZipFile as error:
            raise ValueError('Downloaded IMGT archive is not a valid ZIP file.') from error
        with archive:
            members = archive.infolist()
            for name in source_names():
                matches = [item for item in members if item.filename.split('/', 1)[-1] == name]
                if len(matches) != 1 or matches[0].is_dir():
                    raise ValueError(f'Archive must contain one source file: {name}')
                item = matches[0]
                if ((item.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('Linked IMGT archive inputs are unsupported.')
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as stream, path.open('xb') as output:
                    shutil.copyfileobj(stream, output)
        settings = source_settings(source)
        if settings['imgt'] != expected:
            raise ValueError('Downloaded source headers do not match the selected release.')
        files = source_files(source, settings)
        provenance = {'repository': 'ANHIG/IMGTHLA', 'requested': requested, 'tag': tag,
                      'release_id': release.get('id'), 'archive_url': url,
                      'archive_sha256': checksum.hexdigest(),
                      'files': [{'path': item['path'], 'sha256': item['sha256']} for item in files]}
        (source / 'acquisition.json').write_text(json.dumps(provenance, indent=2) + '\n')
        source.rename(target)
    return target
