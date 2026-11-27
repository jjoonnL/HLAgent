"""Optional Rich presentation for the existing line-oriented chat interface."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import sys

from hlagent.runtime.presentation import align_labels, compact_input, compact_plan, concise_guidance, display_value, result_view, short_paths

try:
    from rich import box
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    Console = None


LOGO = """██╗  ██╗██╗      █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██║  ██║██║     ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
███████║██║     ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
██╔══██║██║     ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
██║  ██║███████╗██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝"""

COMMANDS = (
    ('Analyze', (
        ('imputation', 'Infer HLA alleles'),
        ('hatk', 'HLA association and fine-mapping'),
        ('phasing', 'Phase genotype markers'),
        ('workflow', 'Choose an analysis goal and the input stage you already have'),
        ('example', 'Run the full RA example and create an HTML report'),
    )),
    ('Prepare', (
        ('convert', 'Open file preparation options: HPED, phenotype and study summaries'),
        ('prepare-dictionary', 'Build HATK dictionaries from an IMGT release'),
    )),
    ('Learn', (
        ('tutor', 'Ask about HLA methods and workflows'),
    )),
    ('Session', (
        ('config', 'Change model or search settings'),
        ('next', 'Continue using the last supported completed result'),
        ('resume', 'Recheck a saved run or recover a waiting plan with new approval'),
        ('report', 'Create an offline HTML report from selected saved runs'),
        ('help', 'Show commands and shortcuts'),
        ('exit', 'Leave HLAgent'),
    )),
)


COMMAND_STYLE = '#5f8700'
COMMAND_WORDS = {command for _, commands in COMMANDS for command, _ in commands} | {
    *(f'node{i}' for i in range(1, 6)), *(f'node{i}-custom' for i in range(1, 6)),
    'stepwise', 'prepare-phenotype', 'prepare-summary', 'run', 'files', 'panel',
    'phenotype', 'summary', 'local', 'rag', 'web', 'hybrid', 'llm', 'search',
    'ollama', 'openai', 'anthropic', 'gemini',
    'back', 'cancel', 'y', 'n', 'yes', 'no', 'accept', 'assist', 'manual', 'auto',
    'csv', 'tsv', 'none', 'settings', 'inputs', 'conditions', 'candidates',
    'covariates', 'details', 'edit', 'hped', 'build', 'imgt', 'dictionary', 'snps', 'prepare-dictionary', 'download',
}
COMMAND_ATOM = '(?:' + '|'.join(re.escape(word) for word in sorted(COMMAND_WORDS, key=len, reverse=True)) + ')'
COMMAND_TOKEN = r'(?<![\w./-])' + COMMAND_ATOM + '(?:/' + COMMAND_ATOM + r')*(?![\w./-])'


def highlight_commands(content):
    """Color input tokens in command guidance, leaving descriptions and paths alone."""
    offset = 0
    for line in content.plain.splitlines(keepends=True):
        regions = []
        menu = re.match(r'^\s*(?:Example|Your data|Single scan|Automatic selection)\s*:\s*(.*?)(?=\s*\(|$)', line.rstrip())
        if menu:
            regions.append(menu.span(1))
        for match in re.finditer(r'(?:^|\|)\s*(' + COMMAND_TOKEN + r'(?:\s*/\s*' + COMMAND_TOKEN + r')*)\s*[:=]', line):
            regions.append(match.span(1))
        for match in re.finditer(r'(?i:\b(?:type|enter|press))\s+["`]?(' + COMMAND_TOKEN
                                 + r'(?:\s*(?:,\s*(?:or\s+)?|or\s+)' + COMMAND_TOKEN + r')*)', line):
            regions.append(match.span(1))
        if re.search(r'(?i:\b(?:type|enter|press))', line):
            for match in re.finditer(r'\bor\s+(' + COMMAND_TOKEN + r')', line):
                regions.append(match.span(1))
            for match in re.finditer(r'(?<!\w)[1-5](?:/[1-5])+(?!\w)', line):
                content.stylize(COMMAND_STYLE, offset + match.start(), offset + match.end())
        for match in re.finditer(r'\[([^]\n]+)\]', line):
            options = match[1].split('/')
            if all(option in COMMAND_WORDS or option.isdigit() for option in options):
                regions.append(match.span(1))
        for start, end in regions:
            for match in re.finditer(COMMAND_TOKEN, line[start:end]):
                content.stylize(COMMAND_STYLE, offset + start + match.start(), offset + start + match.end())
        offset += len(line)
    return content


class TerminalUI:
    def __init__(self, plain=False, console=None):
        self.console = None if plain or Console is None else console or Console(
            highlight=False, markup=False,
            color_system=None if 'NO_COLOR' in os.environ else 'auto',
        )
        if console is None and self.console is not None and not self.console.is_terminal:
            self.console = None
        self.status = None
        self.plan_details = None
        self.result_details = None
        self.input_details = None

    def welcome(self, client):
        if self.console is None:
            print('HLAgent | HLA research assistant')
            print(f'Model: {client.model}; API: {client.base_url}')
            self.help(detailed=False)
            return
        logo_width = max(map(len, LOGO.splitlines())) + 6
        heading = Text(LOGO if self.console.width >= logo_width else 'HLAgent', style='bold cyan')
        metadata = Text(f'HLA research assistant\nModel: {client.model}\n'
                        f'API: {client.base_url}\nWorkspace: {Path.cwd()}')
        self.console.print(Panel(Group(heading, Text(''), metadata), border_style='cyan', padding=(1, 2)))
        self.help(detailed=False)

    def help(self, detailed=True):
        sections = COMMANDS
        if detailed:
            sections += (('Preparation shortcuts', (
                ('prepare-phenotype', 'Convert a clinical CSV/TSV to a binary phenotype'),
                ('prepare-summary', 'Prepare study summaries for meta-analysis (HATK Node5)'),
            )),)
        introduction = 'Choose a task - type a command below.'
        if self.console is None:
            print(introduction)
            for label, commands in sections:
                print('\n---' if label == 'Session' else '')
                print(label)
                for command, description in commands:
                    print(f'  {command:<18} {description}')
        else:
            parts = [Text(introduction)]
            command_width = max(len(command) for _, commands in sections for command, _ in commands)
            for label, commands in sections:
                parts.append(Text(''))
                if label == 'Session':
                    parts.append(Rule(style='dim'))
                parts.append(Text(label, style='bold cyan'))
                table = Table.grid(padding=(0, 2))
                table.add_column(width=command_width + 2, no_wrap=True)
                table.add_column(overflow='fold')
                for command, description in commands:
                    table.add_row(Text('  ' + command, style=COMMAND_STYLE), Text(description))
                parts.append(table)
            self.console.print(Panel(Group(*parts), title='Commands', border_style='dim'))
        self.line('Analysis requires a reviewed plan and approval with y.')
        self.line('back / cancel: return to Supervisor')

    def ask(self, prompt):
        if self.console is None or not self.console.is_terminal:
            return input('\n' + prompt)
        self.console.print()
        self.console.print(highlight_commands(Text(prompt, style='bold cyan')), end='')
        return input()

    def line(self, message, style=None):
        message = short_paths(message)
        if self.console is None:
            print(message, flush=True)
        else:
            self.console.print(highlight_commands(Text(message, style=style)), soft_wrap=False)

    def _text(self, message):
        content = Text(align_labels(message), overflow='fold')
        content.highlight_regex(r'(?m)^\d+\.[^\n]*', style='bold cyan')
        content.highlight_regex(r'(?m)^\d+\.(?=\s)', style=COMMAND_STYLE)
        content.highlight_regex(
            r'(?m)^(?:Input|Reuse|Format|Gene order|Required|Select|Commands|Model|Limits|Output|Next|'
            r'Alternative|Modes|Change mode|Current mode|Example|Available phenotype columns)\s*:', style='bold cyan')
        content.highlight_regex(r'(?m)^(?:Selected marker panel|Required format|Recorded files|Inputs|Analysis|Before approval|Example)$', style='bold cyan')
        return highlight_commands(content)

    def _footer(self, actions, navigation):
        lines = [*actions, *([navigation] if navigation else [])]
        if not lines:
            return []
        if self.console is None:
            print('\n---')
            print('\n'.join(lines))
            return []
        return [Text(''), Rule(style='dim'), highlight_commands(Text('\n'.join(lines), overflow='fold'))]

    def _body(self, message, handoff):
        prefix = f'Handoff to {handoff}.\n'
        if self.console is not None and handoff and message.startswith(prefix):
            message = message[len(prefix):].lstrip('\n')
        return message.rstrip()

    def message(self, message, title='Input', error=False, actions=(), navigation='', handoff=''):
        message = self._body(message, handoff)
        hint = 'details: timestamps, run IDs and paths'
        if hint in message.splitlines():
            message = '\n'.join(line for line in message.splitlines() if line != hint).rstrip()
            actions = (hint, *actions)
        if not error and title in ('Input', 'HLA Imputation', 'HLA Fine-mapping', 'File Converter', 'Genotype Phasing'):
            self.input_details = message
            compact = compact_input(message)
            if compact != concise_guidance(message):
                actions = ('details: full input requirements', *actions)
            message = compact
        message = concise_guidance(message)
        if (title == 'Supervisor' and message.startswith('Returned to Supervisor.')
                and 'help: show commands' not in actions):
            actions = (*actions, 'help: show commands')
        if not message and not actions and not navigation:
            return
        if self.console is None:
            print(align_labels(message))
            self._footer(actions, navigation)
            return
        content = Group(self._text(message), *self._footer(actions, navigation))
        self.console.print(Panel(content, title=Text(title),
                                 border_style='red' if error else 'dim', padding=(0, 1)))

    def render(self, renderer, *args, title, summary=None, border_style="cyan",
               message='', actions=(), navigation='', handoff='', detail_kind=None, **kwargs):
        """Reuse authoritative renderers without discarding warnings or paths."""
        if detail_kind:
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = renderer(*args, **kwargs)
            full = stdout.getvalue().rstrip()
            setattr(self, detail_kind + '_details', full)
            compact = compact_plan(full)
            if detail_kind == 'result':
                record = args[0] if isinstance(args[0], dict) else args[0].to_dict()
                if record.get('status') == 'completed' or (record.get('status') == 'incomplete' and record.get('plan', {}).get('example') == 'hatk_stepwise'):
                    compact, summary = result_view(args[0])
                    summary = {key: display_value(value) for key, value in summary.items()}
                    metrics = '\n'.join(key.replace('_', ' ') + ': ' + value
                                        for key, value in summary.items())
                    compact += '\n' + (json.dumps(summary, indent=2) if self.console is not None else metrics)
                else:
                    compact, summary = full, None
            self.render(lambda: print(compact), title=title, summary=summary,
                        border_style=border_style, message=message,
                        actions=('details: full ' + detail_kind + ' information', *actions),
                        navigation=navigation, handoff=handoff)
            if stderr.getvalue():
                self.error(stderr.getvalue().rstrip())
            return result
        if self.console is None:
            if message:
                print(align_labels(message))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = renderer(*args, **kwargs)
            print(align_labels(stdout.getvalue()), end='')
            self._footer(actions, navigation)
            return result
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = renderer(*args, **kwargs)
        text = stdout.getvalue().rstrip()
        message = self._body(message, handoff)
        parts = [self._text(message), Text('')] if message else []
        if summary and isinstance(summary, dict):
            payload = json.dumps(summary, indent=2)
            # Only replace an exact standalone JSON block, leaving prose intact.
            lines = ('\n' + text + '\n')
            if '\n' + payload + '\n' in lines:
                text = lines.replace('\n' + payload + '\n', '\n', 1).strip()
                table = Table('Metric', 'Value', box=box.SIMPLE)
                table.columns[0].style = 'cyan'
                for column in table.columns:
                    column.overflow = 'fold'
                for key, value in summary.items():
                    value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
                    table.add_row(Text(key.replace('_', ' ')), Text(value))
                parts.append(table)
        if text:
            content = self._text(text)
            content.highlight_regex(r'(?m)^[A-Za-z][^:\n]{0,45}:', style='bold cyan')
            parts.append(highlight_commands(content))
        if parts:
            self.console.print(Panel(Group(*parts, *self._footer(actions, navigation)), title=Text(title), border_style=border_style, padding=(0, 1)))
        if stderr.getvalue():
            self.error(stderr.getvalue().rstrip())
        return result

    def details(self, kind):
        text = getattr(self, kind + '_details')
        if not text:
            self.message('No analysis ' + kind + ' details are available in this session.', title='Details')
            return
        # Full original paths, values and caveats remain available locally.
        self.render(lambda: print(text), title=kind.title() + ' details',
                    actions=('Continue at the current prompt. Details does not approve or start a task.',))

    def error(self, message):
        if self.console is None:
            print(message, file=sys.stderr)
        else:
            Console(stderr=True, highlight=False, markup=False,
                    color_system=None if 'NO_COLOR' in os.environ else 'auto').print(
                Panel(Text(message), title='Error', border_style='red'))

    def progress_start(self):
        if self.console is not None and self.console.is_terminal:
            self.status = self.console.status(Text('Preparing the approved analysis'), spinner='line')
            self.status.start()

    def progress_update(self, message):
        if self.status is not None:
            self.status.update(Text(message))

    def progress_stop(self):
        if self.status is not None:
            self.status.stop()
            self.status = None
