"""Local execution stages and recovery guidance for approved chat analyses."""

from pathlib import Path
import re
from shlex import quote
from threading import Event, RLock, Thread
from time import monotonic


STAGES = {
    'inputs': 'Rechecking input files and recorded hashes',
    'environment': 'Checking Docker and the analysis image',
    'staging': 'Creating the run directory and copying checked inputs',
    'execution': 'Running the analysis tool. Inspect its log for internal steps',
    'validation': 'Checking generated results and recording output hashes',
}


class ToolProgress:
    """Read only recognized progress lines from the current run's tool log."""

    def __init__(self, plan):
        self.directory = Path(plan.run_dir)
        self.limit = None
        if getattr(plan, 'dictionary', False):
            self.kind, self.path = 'imgt2seq', self.directory / 'hatk.log'
        elif getattr(plan, 'phasing', False):
            self.kind, self.path = 'phasing', self.directory / 'output/beagle.log'
        elif getattr(plan, 'stepwise', False):
            self.kind, self.path = 'stepwise', self.directory / 'hatk.log'
            self.limit = plan.data['selection']['max_iterations']
        elif not hasattr(plan, 'phasing'):
            self.kind, self.path = 'cookhla', self.directory / 'cookhla.log'
        else:
            self.kind, self.path = None, None
        self.offset = 0
        self.partial = b''
        self.last = None

    def read(self):
        if self.path is None:
            return []
        try:
            with self.path.open('rb') as stream:
                if stream.seek(0, 2) < self.offset:
                    self.offset, self.partial = 0, b''
                stream.seek(self.offset)
                data = stream.read(65536)
                self.offset = stream.tell()
        except OSError:
            return []
        lines = (self.partial + data).split(b'\n')
        self.partial = lines.pop()[-4096:]
        messages = []
        for line in lines:
            message = self.parse(line.decode('utf-8', errors='replace').strip())
            if message and message != self.last:
                self.last = message
                messages.append(message)
        return messages

    def parse(self, line):
        if self.kind == 'imgt2seq':
            match = re.search(r'Generating sequence information dictionary for HLA-(A|B|C|DPA1|DPB1|DQA1|DQB1|DRB1)\.$', line)
            if match:
                return f'IMGT2Seq: processing HLA-{match[1]}.'
        if self.kind == 'stepwise':
            match = re.fullmatch(r'Stepwise scan (\d+)\. Conditioning markers: (\d+)\.', line)
            if match:
                scan, count = map(int, match.groups())
                if 1 <= scan <= self.limit and count == scan - 1:
                    return f'Stepwise scan {scan} (limit {self.limit}). Conditioning markers: {count}.'
        elif self.kind == 'phasing':
            match = re.fullmatch(r'Phasing: iteration (\d+)', line)
            if match:
                return f'BEAGLE phasing iteration {int(match[1])}.'
        elif self.kind == 'cookhla':
            stages = {
                '[1] Extracting SNPs from the MHC.': 'CookHLA: extracting MHC SNPs.',
                '[2] Performing SNP quality control.': 'CookHLA: SNP quality control.',
                '[3] Converting data to beagle format.': 'CookHLA: preparing imputation input.',
                '[5] Converting out imputation result(s).': 'CookHLA: converting imputation results.',
                '[6] Clean Up.': 'CookHLA: cleaning up intermediate files.',
            }
            if line in stages:
                return stages[line]
            match = re.fullmatch(r'\[4\] Performing HLA imputation\(exon([234]) / overlap:([0-9.]+)\)\.', line)
            if match:
                return f'CookHLA: imputation job started (exon {match[1]}, overlap {match[2]}).'
        return None


class ExecutionFeedback:
    """Report actual host stages and elapsed waiting time, never percent complete."""

    def __init__(self, plan, interval=30, ui=None):
        self.ui = ui
        self.plan = plan
        self.stage = None
        self.interval = interval
        self.stopped = Event()
        self.tool_progress = ToolProgress(plan)
        self.last_tool_update = None
        self.progress_lock = RLock()

    def __enter__(self):
        self.started = monotonic()
        self.write('Progress is reported by stage, without an estimated percentage. '
              'Press Ctrl+C to request cancellation.')
        if self.ui is not None:
            self.ui.progress_start()
        self.thread = Thread(target=self.waiting, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exception):
        self.stopped.set()
        self.thread.join()
        self.poll_tool()
        if self.ui is not None:
            self.ui.progress_stop()

    def update(self, stage):
        with self.progress_lock:
            if self.stage == 'execution' and stage != 'execution':
                self.poll_tool()
            self.stage = stage
            self.write('Stage: ' + STAGES[stage] + '.')
            if self.ui is not None:
                self.ui.progress_update(STAGES[stage])

    def poll_tool(self):
        with self.progress_lock:
            if self.stage != 'execution':
                return False
            messages = self.tool_progress.read()
            for message in messages:
                self.write('Log update: ' + message)
                self.last_tool_update = message
            if messages and self.ui is not None:
                self.ui.progress_update('Last log update: ' + messages[-1])
            return bool(messages)

    def waiting(self):
        heartbeat = self.started
        while not self.stopped.wait(min(self.interval, 1)):
            changed = self.poll_tool()
            now = monotonic()
            if changed:
                heartbeat = now
            if now - heartbeat >= self.interval:
                elapsed = int(now - self.started)
                if self.stage == 'execution' and self.last_tool_update:
                    self.write(f'Still waiting ({elapsed}s elapsed). Last log update: '
                               + self.last_tool_update + ' No newer tool update has been read.')
                else:
                    self.write(f'Still waiting ({elapsed}s elapsed): '
                               + STAGES.get(self.stage, 'Preparing the approved analysis')
                               + '. This does not confirm tool progress.')
                heartbeat = now

    def write(self, message):
        if self.ui is None:
            print(message, flush=True)
        else:
            self.ui.line(message)

    def show_recovery(self, result=None, error=None):
        status = (result.get('status') if isinstance(result, dict)
                  else getattr(result, 'status', None))
        if error is None and status not in ('failed', 'cancelled', 'cancellation_unconfirmed'):
            return
        directory = Path(self.plan.run_dir)
        if status == 'cancellation_unconfirmed':
            print('Container termination is unconfirmed. Check the named container '
                  'with docker ps -a before starting another analysis.')
            print('Container name: ' + getattr(self.plan, 'container_name', directory.name))
        elif status == 'cancelled':
            print('The run was cancelled. Partial outputs are not completed results.')
        else:
            print('Stopped during: ' + STAGES.get(self.stage, 'Preparing the approved analysis') + '.')
            if isinstance(error, PermissionError):
                print('File access: check read permissions on inputs and write permissions on the run directory.')
            elif isinstance(error, FileNotFoundError) and self.stage != 'environment':
                print('File access: check that the selected input paths still exist.')
            elif self.stage == 'inputs':
                print('Input check: correct the reported file, format or changed source. Select inputs again.')
            elif self.stage == 'environment':
                print('Environment check: inspect the reported Docker access/image error. '
                      'No analysis worker has started in this stage. '
                      'If Docker reports a seccomp-related startup error, use the container-only --seccomp-unconfined option.')
            elif self.stage == 'staging':
                print('Input staging: check source access, free disk space and write permissions; '
                      'a source may also have changed during copying.')
            elif self.stage == 'validation':
                print('Result check: the tool returned, but its outputs did not pass verification. '
                      'Inspect the reported check and tool log before interpreting any output.')
            else:
                print('Tool execution: inspect the exit error and log. '
                      'The exact cause cannot be determined from the exit code alone.')
        for name in ('run.json', 'hatk.log', 'cookhla.log', 'output/beagle.log'):
            path = directory / name
            if path.is_file():
                print(('Run record: ' if name == 'run.json' else 'Log: ') + str(path))
                if name != 'run.json':
                    print('Inspect in another terminal: tail -n 60 -- ' + quote(str(path)))
        if not (directory / 'run.json').is_file():
            print('No run record was created. Planned directory: ' + str(directory))
        print('Partial files are preserved and must not be reused as completed results.')
        print('Retry: at Supervisor, enter ' + retry_command(self.plan)
              + ', reselect/review the inputs, then approve the new plan with y. '
              'The previous approval has been discarded. Nothing restarts automatically.')


def retry_command(plan):
    if not hasattr(plan, 'phasing'):
        return 'imputation'
    if plan.phasing:
        return 'phasing'
    for flag, command in (
            ('stepwise', 'stepwise'), ('custom_meta', 'node5-custom'), ('meta', 'node5'),
            ('custom_omnibus', 'node4-custom'), ('omnibus', 'node4'),
            ('custom_conditional', 'node3-custom'), ('conditional', 'node3'),
            ('custom_association', 'node2-custom'), ('parent_run', 'node2'),
            ('custom_markers', 'node1-custom')):
        if getattr(plan, flag, False):
            return command
    return 'node1'
