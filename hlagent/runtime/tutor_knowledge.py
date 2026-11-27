"""Page-aware local document retrieval with SQLite FTS5 BM25."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from hlagent.paths import ROOT

def default_index(root=ROOT):
    index = Path(root) / 'knowledge/tutor.sqlite3'
    legacy = Path(root) / 'knowledge/teacher.sqlite3'
    return legacy if not index.exists() and legacy.is_file() else index


DEFAULT_INDEX = default_index()
STOP_WORDS = set('a an the is are was were what why how do does can could in on of for to and or with from my please explain'.split())


def document_pages(path):
    path = Path(path)
    if path.suffix.lower() == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError('PDF indexing requires pypdf: python -m pip install -r requirements/tutor.txt') from None
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('Encrypted PDFs are not supported.')
        title = str((reader.metadata or {}).get('/Title', '')).strip() or path.stem
        return title[:200], [(i + 1, page.extract_text() or '') for i, page in enumerate(reader.pages)]
    if path.suffix.lower() in ('.txt', '.md'):
        return path.stem, [(None, path.read_text(encoding='utf-8'))]
    raise ValueError('Knowledge documents must be PDF, UTF-8 TXT or Markdown.')


def text_chunks(text, size=1200, overlap=200):
    text = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(' ', start + size // 2, end)
            if boundary > start:
                end = boundary
        yield text[start:end]
        if end == len(text):
            break
        start = end - overlap


def build_index(paths, index=DEFAULT_INDEX, replace=False):
    index = Path(index)
    sources = [Path(path).resolve(strict=True) for path in paths]
    if not sources or len(set(sources)) != len(sources):
        raise ValueError('Provide one or more distinct source documents.')
    if index.resolve() in sources:
        raise ValueError('The index cannot overwrite a source document.')
    if index.exists() and not replace:
        raise ValueError('The index already exists. Use --replace to rebuild it.')
    index.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.tutor-', suffix='.sqlite3', dir=index.parent)
    os.close(descriptor)
    manifest = {'version': 1, 'retriever': 'SQLite FTS5 BM25', 'documents': [], 'chunks': 0}
    connection = None
    try:
        connection = sqlite3.connect(temporary)
        connection.execute('CREATE TABLE metadata (manifest TEXT NOT NULL)')
        connection.execute('CREATE VIRTUAL TABLE chunks USING fts5(text, title, source UNINDEXED, '
                           'page UNINDEXED, part UNINDEXED, sha256 UNINDEXED)')
        for path in sources:
            original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            title, pages = document_pages(path)
            count = 0
            for page, text in pages:
                for part, chunk in enumerate(text_chunks(text), 1):
                    connection.execute('INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)',
                                       (chunk, title, path.name, page, part, original_hash))
                    count += 1
            if not count:
                raise ValueError(f'No extractable text in {path.name}; scanned PDFs require OCR before indexing.')
            if hashlib.sha256(path.read_bytes()).hexdigest() != original_hash:
                raise ValueError(f'Source changed while indexing: {path.name}')
            manifest['documents'].append({'source': path.name, 'title': title, 'sha256': original_hash,
                                          'pages': len(pages), 'text_pages': sum(bool(t.strip()) for _, t in pages),
                                          'chunks': count})
            manifest['chunks'] += count
        connection.execute('INSERT INTO metadata VALUES (?)', (json.dumps(manifest),))
        connection.commit()
        connection.close()
        connection = None
        os.replace(temporary, index)
    except sqlite3.Error:
        raise RuntimeError('Could not build the knowledge index. Python SQLite must support FTS5.') from None
    finally:
        if connection is not None:
            connection.close()
        if os.path.exists(temporary):
            os.unlink(temporary)
    return manifest


def open_index(index):
    index = Path(index)
    if not index.is_file():
        raise RuntimeError('No Tutor index. Build one with python scripts/tools/tutor_knowledge.py build DOCUMENT...')
    connection = sqlite3.connect(index.resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def index_status(index=DEFAULT_INDEX):
    try:
        with closing(open_index(index)) as connection:
            manifest = json.loads(connection.execute('SELECT manifest FROM metadata').fetchone()[0])
            if manifest['version'] != 1:
                raise ValueError('Unsupported Tutor index version.')
            return manifest
    except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('Invalid Tutor knowledge index; rebuild from source documents.') from None


def search_documents(query, index=DEFAULT_INDEX, limit=4, sources=None, row_ids=None):
    if not 1 <= limit <= 10:
        raise ValueError('Retrieval limit must be between 1 and 10.')
    if row_ids is not None and any(type(key) is not int or key < 1 for key in row_ids):
        raise ValueError('Chunk row IDs must be positive integers.')
    terms = list(dict.fromkeys(term.lower() for term in re.findall(r'\w+', query)
                               if len(term) > 1 and term.lower() not in STOP_WORDS))[:24]
    if not terms:
        return []
    # Quote terms and bind parameters; user text is never SQL or FTS syntax.
    match = ' OR '.join('"' + term + '"' for term in terms)
    if (sources is not None and not sources) or (row_ids is not None and not row_ids):
        return []
    scope = '' if sources is None else ' AND source IN (' + ','.join('?' for _ in sources) + ')'
    scope += '' if row_ids is None else ' AND rowid IN (' + ','.join('?' for _ in row_ids) + ')'
    try:
        with closing(open_index(index)) as connection:
            rows = connection.execute('SELECT rowid, text, title, source, page, part, sha256, bm25(chunks) AS rank '
                                      'FROM chunks WHERE chunks MATCH ?' + scope + ' ORDER BY rank, rowid LIMIT ?',
                                      (match, *(sources or []), *(row_ids or []), limit)).fetchall()
            return [{'id': f"paper_{row['rowid']}", 'kind': 'document', 'title': row['title'],
                     'source': row['source'], 'page': row['page'], 'chunk': row['part'],
                     'sha256': row['sha256'], 'text': row['text'], 'score': row['rank']} for row in rows]
    except sqlite3.Error:
        raise RuntimeError('Could not search the Tutor index; rebuild it with FTS5 support.') from None
