"""Private Tutor evidence records and frozen answer-request fingerprints."""

from hlagent.paths import new_run_id

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from hlagent.paths import ROOT

RECORD_ROOT = ROOT / 'runs/tutor'


def request_digest(request):
    payload = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def model_identity(client):
    provider = getattr(client, 'provider', None)
    model = getattr(client, 'model', None)
    return {'provider': provider if isinstance(provider, str) else
                        'ollama' if type(client).__name__ == 'OllamaClient' else 'unknown',
            'model': model if isinstance(model, str) else 'unknown'}


def save_record(record, root=RECORD_ROOT):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (new_run_id('tutor', root, '.json') + '.json')
    data = dict(record, version=1, created_at=datetime.now(timezone.utc).isoformat())
    if data.get('answer_request') is not None:
        data['answer_request_sha256'] = request_digest(data['answer_request'])
    # Only explicit evidence/response fields are passed here, never client/config dictionaries.
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(payload)
    return path


def load_record(path, require_answer=True):
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    request = record.get('answer_request') if isinstance(record, dict) else None
    if (not isinstance(record, dict) or record.get('version') != 1
            or not isinstance(record.get('question'), str) or record.get('mode') not in ('local', 'rag', 'web', 'hybrid')):
        raise ValueError('Invalid Tutor record.')
    if request is None and not require_answer:
        return record
    if (not isinstance(request, dict) or set(request) != {'messages', 'schema', 'sources'}
            or record.get('answer_request_sha256') != request_digest(request)):
        raise ValueError('Missing or changed frozen answer request. Use a Tutor record containing an answer request.')
    if (not isinstance(request['messages'], list) or not request['messages']
            or not isinstance(request['schema'], dict) or not isinstance(request['sources'], dict)):
        raise ValueError('Invalid frozen Tutor request.')
    if any(not isinstance(row, dict) or row.get('role') not in ('system', 'user', 'assistant')
           or not isinstance(row.get('content'), str) for row in request['messages']):
        raise ValueError('Frozen Tutor messages must contain text only.')
    if request['messages'][-1] != {'role': 'user', 'content': record['question']}:
        raise ValueError('The recorded question differs from the frozen answer request.')
    return record
