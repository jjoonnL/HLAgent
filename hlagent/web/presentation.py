"""Display CLI instructions as readable browser text without changing agent behavior."""

import re


def example_summary(directory):
    from hlagent.runtime.presentation import short_paths
    return ('Input: Bundled RA data. 600 people, 300 cases and 300 controls.\n\n'
            'Steps:\n'
            '1. HLA marker generation\n'
            '2. Marker association\n'
            '3. Genotype phasing\n'
            '4. Amino-acid omnibus test\n'
            '5. HTML report\n\n'
            'Starts from HLA genotypes. CookHLA, conditional association and meta-analysis are not included.\n\n'
            'Save to: ' + short_paths(str(directory)))


def conversation_expanded(state):
    tutor_controls = state['role'] == 'tutor' and 'Current mode:' in state['message'] and (
        'Change mode:' in state['message'] or state['message'].startswith('Current mode:'))
    return state['field'] is None and not state['approval_kind'] and not tutor_controls


def instructions(text):
    lines = []
    fence = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            lines.append(line)
        elif fence is not None or stripped.startswith('|'):
            lines.append(line)
        else:
            line = re.sub(r'^(\s*)([A-Za-z][A-Za-z /()-]{1,35}?)\s*:(?:[ \t]+|$)', r'\1**\2:** ', line)
            if stripped.endswith(':') and not stripped.startswith(('-', '*', '#')) and '**' not in line:
                line = '**' + stripped + '**'
            lines.append(line + '  ' if line.strip() else '')
    return '\n'.join(lines)


def message(text, role):
    if text.startswith('Handoff to '):
        text = text.partition('\n')[2]
    if role == 'tutor' and 'Change mode:' in text and 'Ask your question about HLA research' in text:
        return 'Ask a question about HLA research or supported HLAgent workflows. Choose the answer sources above.'
    return instructions(text)
