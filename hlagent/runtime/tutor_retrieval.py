"""Scope local documents and fuse lexical and optional local semantic rankings."""

from contextlib import closing
import hashlib
from pathlib import Path
import re

from hlagent.runtime.tutor_knowledge import DEFAULT_INDEX, STOP_WORDS, open_index, search_documents


def words(text):
    return set(re.findall(r'[a-z][a-z0-9]+', text.lower()))


def document_scope(question, chunks):
    """Match explicitly named document identities, not incidental body mentions."""
    query = words(question)
    generic = {'paper', 'article', 'methods', 'method', 'study', 'document', 'review', 'supplement'}
    matched = set()
    for row in chunks:
        identity = words(Path(row['source']).stem) - generic
        if any(len(term) >= 4 and term in query for term in identity):
            matched.add(row['source'])
    return sorted(matched)


def boilerplate(text):
    lowered = text.lower()
    legal = sum(term in lowered for term in ('license', 'creativecommons', 're-use',
                                              'reproduction', 'permissions@', 'rights reserved',
                                              'copyright', 'creative commons'))
    explanatory = re.search(r'\b(methods?|analys[ei]s|algorithm|software|results|we present|we introduce)\b', lowered)
    years = len(re.findall(r'\((?:19|20)\d{2}\)', text))
    entries = len(re.findall(r'(?:^|\s)\d{1,3}\.\s+[A-Z]', text))
    return (legal >= 3 and not explanatory) or (years >= 3 and entries >= 3)


def overview_strength(text):
    """Prefer explicit method summaries only for overview requests."""
    text = text.lower()
    introduces = bool(re.search(r'\b(we (present|introduce|propose)|our (method|software))\b', text))
    enumerates = bool(re.search(r'\bfirst\b', text) and re.search(r'\b(second|third)\b', text))
    return int(introduces) + int(enumerates)


def fuse_rankings(lexical, semantic, chunks, query, limit):
    """RRF uses ranks rather than adding incompatible BM25/cosine scores."""
    by_id = {row['id']: row for row in chunks}
    scores, ranks = {}, {}
    for name, ranking in (('bm25', lexical), ('embedding', semantic)):
        for rank, row in enumerate(ranking, 1):
            key = row['id']
            # Exact scientific terms remain the stronger signal; semantics adds recall.
            weight = 2 if name == 'bm25' else 1
            scores[key] = scores.get(key, 0.) + weight / (60 + rank)
            ranks.setdefault(key, {})[name] = rank
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    # Retain the direct lexical match; use fusion to find complementary evidence.
    if lexical:
        key = lexical[0]['id']
        ordered = [key] + [other for other in ordered if other != key]
    # Reserve one slot for a genuine summary, rather than hard-coding method facts.
    overview = bool(re.search(r'\b(overview|main (approach|method)|summari[sz]e|'
                              r'key methodological changes|methodology)\b', query.lower()))
    summary = []
    if overview and ordered:
        named = words(query)
        summary = [row for row in chunks if overview_strength(row['text']) == 2
                   and (words(row['text']) & named)]
        summary.sort(key=lambda row: (-scores.get(row['id'], 0.), row['id']))
    if summary:
        key = summary[0]['id']
        ordered = [key] + [other for other in ordered if other != key]
        ranks.setdefault(key, {})['overview'] = 1
    # A comparison naming multiple documents must not spend every slot on one.
    scope = document_scope(query, chunks)
    if 1 < len(scope) <= limit:
        heads = []
        for key in ordered:
            if by_id[key]['source'] not in {by_id[head]['source'] for head in heads}:
                heads.append(key)
        ordered = heads + [key for key in ordered if key not in heads]
    selected, seen = [], []
    for key in ordered:
        row = by_id[key]
        tokens = words(row['text'])
        if any(len(tokens & old) / max(1, min(len(tokens), len(old))) > .85 for old in seen):
            continue
        selected.append(dict(row, score=scores.get(key, 0.), retrieval_ranks=ranks[key]))
        seen.append(tokens)
        if len(selected) == limit:
            break
    return selected


class DocumentRetriever:
    def __init__(self, index=DEFAULT_INDEX, model_dir=None, model=None):
        self.index = Path(index)
        self.model_dir = Path(model_dir).resolve() if model_dir else None
        self.model = model
        self.fingerprint = None
        self.chunks = []
        self.vectors = None
        self.windows = []
        self.last_info = {}
        self.model_hashes = {}

    def prepare(self):
        fingerprint = hashlib.sha256(self.index.read_bytes()).hexdigest()
        if fingerprint == self.fingerprint:
            return
        with closing(open_index(self.index)) as connection:
            rows = connection.execute('SELECT rowid, text, title, source, page, part, sha256 '
                                      'FROM chunks ORDER BY rowid').fetchall()
        chunks = [{'id': f"paper_{row['rowid']}", 'kind': 'document', 'text': row['text'],
                   'title': row['title'], 'source': row['source'], 'page': row['page'],
                   'chunk': row['part'], 'sha256': row['sha256']} for row in rows]
        if self.model_dir and self.model is None:
            if not self.model_dir.is_dir():
                raise RuntimeError('Tutor embedding model directory does not exist. No model was downloaded.')
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise RuntimeError('Local embeddings require requirements/retrieval.txt in an isolated environment.') from None
            self.model = SentenceTransformer(str(self.model_dir), device='cpu', local_files_only=True,
                                             trust_remote_code=False)
            self.model_hashes = {str(path.relative_to(self.model_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted(self.model_dir.rglob('*'))
                                 if path.is_file() and '.cache' not in path.parts}
        windows, vectors = [], None
        if self.model is not None:
            size = self.model.max_seq_length - 32
            if size <= 32:
                raise ValueError('Embedding model context must exceed 64 tokens.')
            for row in chunks:
                offsets = self.model.tokenizer(row['text'], add_special_tokens=False,
                                               return_offsets_mapping=True, verbose=False)['offset_mapping']
                for start in range(0, len(offsets), size - 32):
                    end = min(start + size, len(offsets))
                    left = offsets[start][0] if start else 0
                    right = offsets[end - 1][1] if end < len(offsets) else len(row['text'])
                    passage = row['text'][left:right]
                    if len(self.model.tokenizer.encode(passage, truncation=False, verbose=False)) > self.model.max_seq_length:
                        raise ValueError('Embedding window exceeds model context; no silent truncation is allowed.')
                    windows.append({'id': row['id'], 'text': passage, 'start': left, 'end': right})
                    if end == len(offsets):
                        break
            if windows:
                vectors = self.model.encode([row['text'] for row in windows], normalize_embeddings=True,
                                            show_progress_bar=False, convert_to_numpy=True)
        if hashlib.sha256(self.index.read_bytes()).hexdigest() != fingerprint:
            raise ValueError('Tutor index changed while preparing retrieval; retry the question.')
        self.chunks, self.windows, self.vectors, self.fingerprint = chunks, windows, vectors, fingerprint

    def search(self, query, limit=3, question=''):
        if not 1 <= limit <= 10:
            raise ValueError('Retrieval limit must be between 1 and 10.')
        self.prepare()
        scope = document_scope(question or query, self.chunks)
        if not scope:
            scope = document_scope(query, self.chunks)
        candidates = [row for row in self.chunks if (not scope or row['source'] in scope)
                      and not boilerplate(row['text'])]
        allowed = {row['id'] for row in candidates}
        names = set().union(*(words(Path(source).stem) for source in scope)) if scope else set()
        generic = STOP_WORDS | {'find', 'original', 'publication', 'main', 'approach', 'evaluate', 'need', 'before'}
        focused = ' '.join(term for term in re.findall(r'\w+', query.lower()) if term not in generic | names)
        lexical = [row for row in search_documents(focused or query, self.index, 10, sources=scope or None,
                                                  row_ids=[int(row['id'][6:]) for row in candidates])
                   if row['id'] in allowed]
        semantic = []
        if self.vectors is not None:
            if len(self.model.tokenizer.encode(query, truncation=False, verbose=False)) > self.model.max_seq_length:
                raise ValueError('Retrieval query exceeds embedding model context; shorten the question.')
            vector = self.model.encode([query], normalize_embeddings=True,
                                        show_progress_bar=False, convert_to_numpy=True)[0]
            scores = self.vectors @ vector
            best = {}
            for window, score in zip(self.windows, scores):
                if window['id'] in allowed and (window['id'] not in best or score > best[window['id']]):
                    best[window['id']] = float(score)
            semantic = [dict(row, score=best[row['id']]) for row in candidates if row['id'] in best]
            semantic.sort(key=lambda row: (-row['score'], row['id']))
            semantic = semantic[:10]
        result = fuse_rankings(lexical, semantic, candidates, query, limit)
        if hashlib.sha256(self.index.read_bytes()).hexdigest() != self.fingerprint:
            raise ValueError('Tutor index changed during retrieval; retry the question.')
        self.last_info = {'version': 1, 'backend': 'BM25+embedding RRF' if self.model is not None else 'BM25 scoped',
                          'index_sha256': self.fingerprint, 'scoped_sources': scope,
                          'fusion': {'rank_constant': 60, 'bm25_weight': 2, 'embedding_weight': 1,
                                     'lexical_anchor': True, 'overview_summary_slot': True},
                          'lexical_query': focused or query,
                          'candidate_rankings': {'bm25': [row['id'] for row in lexical],
                                                 'embedding': [row['id'] for row in semantic]},
                          'excluded_boilerplate_ids': [row['id'] for row in self.chunks if boilerplate(row['text'])],
                          'embedding_windows': len(self.windows), 'model_files_sha256': self.model_hashes,
                          'embedding_max_tokens': self.model.max_seq_length if self.model is not None else None,
                          'selected_ids': [row['id'] for row in result]}
        return result
