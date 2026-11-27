"""Build, inspect and query a local Tutor document index."""

import argparse
import json
import sys
from pathlib import Path

from hlagent.runtime.tutor_knowledge import DEFAULT_INDEX, build_index, index_status, search_documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=DEFAULT_INDEX)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='Index explicitly selected PDF/TXT/Markdown files locally.')
    build.add_argument('documents', nargs='+', type=Path)
    build.add_argument('--replace', action='store_true')
    commands.add_parser('status', help='Show source hashes and page/chunk counts.')
    search = commands.add_parser('search', help='Retrieve excerpts without using an LLM or the network.')
    search.add_argument('query')
    search.add_argument('--limit', type=int, default=4)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build_index(args.documents, args.index, args.replace)
        elif args.command == 'status':
            result = index_status(args.index)
        else:
            result = search_documents(args.query, args.index, args.limit)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (RuntimeError, ValueError, OSError) as error:
        print(f'Tutor knowledge: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
