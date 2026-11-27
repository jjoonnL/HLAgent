"""Tavily search excerpts; no page fetching or automatic retries."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PRIMARY_DOMAINS = ['pubmed.ncbi.nlm.nih.gov', 'pmc.ncbi.nlm.nih.gov', 'nature.com',
                   'academic.oup.com', 'github.com', 'ebi.ac.uk']


def search_web(query, api_key, limit=4):
    if not api_key or not api_key.strip():
        raise RuntimeError('Tavily is not configured. Run python configure.py --search to save TAVILY_API_KEY.')
    if not query.strip() or len(query) > 500:
        raise ValueError('The search query must contain 1 to 500 characters.')
    if not 1 <= limit <= 5:
        raise ValueError('Web search limit must be between 1 and 5.')
    request = Request('https://api.tavily.com/search', method='POST',
                      headers={'Authorization': 'Bearer ' + api_key.strip(), 'Content-Type': 'application/json'},
                      data=json.dumps({'query': query, 'search_depth': 'basic', 'topic': 'general',
                                       'max_results': limit, 'include_answer': False, 'include_raw_content': False,
                                       'include_domains': PRIMARY_DOMAINS}).encode())
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('Search response exceeded the size limit.')
            body = json.loads(raw)
    except HTTPError as error:
        code = error.code
        error.close()
        guidance = ('Check the Tavily key.' if code in (401, 403) else
                    'Check Tavily credits and rate limits.' if code in (429, 432, 433) else
                    'Check Tavily availability and settings.')
        raise RuntimeError(f'Tavily search failed (HTTP {code}). {guidance} No retry was attempted.') from None
    except (URLError, OSError):
        raise RuntimeError('Could not reach Tavily. No retry was attempted.') from None
    except json.JSONDecodeError:
        raise ValueError('Tavily returned invalid JSON.') from None
    if not isinstance(body, dict) or not isinstance(body.get('results'), list):
        raise ValueError('Tavily returned an invalid result list.')
    results = []
    for row in body['results'][:limit]:
        if not isinstance(row, dict):
            continue
        url, text = row.get('url'), row.get('content')
        if not isinstance(url, str) or not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or '').lower()
            allowed = any(host == domain or host.endswith('.' + domain) for domain in PRIMARY_DOMAINS)
            if parsed.scheme != 'https' or not allowed or parsed.username or parsed.password:
                continue
        except ValueError:
            continue
        results.append({'id': 'web_' + str(len(results) + 1), 'kind': 'web',
                        'title': str(row.get('title') or host)[:200], 'url': url,
                        'text': text[:1800]})
    return results
