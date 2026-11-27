"""Collect and review HATK marker phasing through a dedicated dialogue role."""

import json
from pathlib import Path
import subprocess

from hlagent.agents.prompt_rules import COMMON_RULES
from hlagent.runtime.analysis_run_selection import completed_runs, selected_run, run_listing
from hlagent.runtime.presentation import stage_title
from hlagent.runtime.chat_types import BACK_WORDS, CANCEL_WORDS, EXIT_WORDS, ChatReply, result_context, validate_decision
from hlagent.analysis.hatk import ROOT, prepare_phasing, run_example
from hlagent.analysis.hatk_phasing import phase_inputs, phase_settings
from hlagent.cli.hatk_phasing_run import node1_panel


SYSTEM_PROMPT = COMMON_RULES + """You are HLAgent's Genotype Phasing agent. Phase a chromosome 6
HATK marker panel with BEAGLE 3.3.2 to prepare full and AA-only phased BGL plus FAM.
The program locally selects a completed Node1 run or collects a supplied BED/BIM/FAM
prefix and explicit build. Unique IIDs, founder-only FAM and complete AA definitions
are required. All marker classes are phased and missing genotypes are inferred;
there is no reference panel or new QC. Defaults are Java heap 4g, seed 1, single thread.
This does not evaluate phase accuracy. Node2/3 do not need phasing. Node4 is a separate
analysis and still needs a matching phenotype; it does not start automatically here.
Actions: choose_inputs asks for input mode; run lists completed Node1 runs; panel
collects supplied panel/build; settings asks for Java heap and seed locally; plan
prepares/retries selected inputs with new approval; answer explains/clarifies;
back/cancel returns to Supervisor; exit ends the chat only on a direct exit request;
unsupported covers other phasing tools/data or arbitrary commands.
Never extract paths/settings, choose files or approve analysis yourself. Even run-now
requests only prepare a plan; exact local y/yes is required to start. Unrecognized input while approval is pending keeps the plan and asks for exact local approval. Only actual last_result establishes execution.
For an ambiguous stop ask for clarification; do not act on negated/quoted exit requests.
Answer briefly in the user's language. Return JSON with action and message only.
"""

DECISION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'action': {'type': 'string', 'enum': [
        'choose_inputs', 'run', 'panel', 'settings', 'plan', 'answer', 'back', 'cancel', 'exit', 'unsupported']},
                   'message': {'type': 'string'}},
    'required': ['action', 'message'],
}


class PhasingAgent:
    def __init__(self, client, seccomp_unconfined=False, run_root=None):
        self.client = client
        self.seccomp_unconfined = seccomp_unconfined
        self.run_root = Path(run_root) if run_root is not None else ROOT / 'runs'
        self.pending_plan = None
        self.last_result = None
        self.input_field = None
        self.mode = None
        self.inputs = {}
        self.memory = '4g'
        self.seed = 1
        self.run_names = []
        self.history = []
        self.closed = False

    @property
    def input_prompt(self):
        if self.pending_plan is not None:
            return 'Start genotype phasing? [y/n]> '
        return {'mode': 'Phasing input [run/panel]> ', 'run': 'Marker generation run [number/name/directory]> ',
                'prefix': 'Marker PLINK prefix> ', 'build': 'Panel genome build [18/19/38]> ',
                'memory': f'Java memory ({self.memory})> ', 'seed': f'Random seed ({self.seed})> '}.get(
                    self.input_field, 'Genotype Phasing> ')

    def discard_plan(self):
        self.pending_plan = None

    def leave(self):
        self.discard_plan()
        self.input_field = None
        return ChatReply('Returned to Supervisor. No phasing was started.', return_to_supervisor=True)

    def end_session(self):
        self.discard_plan()
        self.input_field = None
        self.closed = True
        return ChatReply('Session ended.', end_session=True)

    def start_task(self, request):
        """Collect fresh inputs for a task already classified by Supervisor."""
        reply = self.choose_inputs()
        self.history = (self.history + [{'role': 'user', 'content': request},
                                       {'role': 'assistant', 'content': reply.message}])[-8:]
        return reply

    def choose_inputs(self, mode=None):
        self.discard_plan()
        self.mode = mode
        self.inputs = {}
        self.input_field = mode or 'mode'
        if mode == 'run':
            self.run_names = completed_runs(self.run_root, 'node1')
            return ChatReply(run_listing(self.run_names, stage_title('hatk_node1'), self.run_root))
        if mode == 'panel':
            self.input_field = 'prefix'
            return ChatReply('Input: enter a chromosome 6 HATK marker PLINK prefix.\n'
                             'Format: prefix without .bed/.bim/.fam. The panel must contain AA markers.\n\n')
        return ChatReply('Choose genotype phasing input:\n\n'
                         '1. A completed HLA marker generation run (HATK Node1).\n\n'
                         '2. A supplied marker PLINK panel.\n\n'
                         'Select: enter run/panel or 1/2.\n')

    def prepare_plan(self):
        self.discard_plan()
        if self.mode == 'run' and 'run' in self.inputs:
            prefix, build = node1_panel(self.inputs['run'])
        elif self.mode == 'panel' and set(self.inputs) == {'prefix', 'build'}:
            prefix, build = self.inputs['prefix'], self.inputs['build']
        else:
            return self.choose_inputs()
        plan = prepare_phasing(prefix, build, self.memory, self.seed, self.run_root, self.seccomp_unconfined)
        self.pending_plan = plan
        self.input_field = None
        return ChatReply('Status: waiting for approval. Phasing has not started.\n\n'
                         'Review the inputs and settings below. Enter y to start or n to discard.',
                         plan=plan, actions=('settings: change Java memory/seed | inputs: select other data',))

    def choose_settings(self):
        self.discard_plan()
        self.input_field = 'memory'
        return ChatReply('Input: enter Java heap memory, for example 4g, or press Enter to keep it.\n'
                         'Next: enter a random seed. Enter also keeps its value.\n\n'
                         'These settings do not cap total container memory or CPU use.')

    def collect_input(self, text):
        field = self.input_field
        if not text and field not in ('memory', 'seed'):
            return ChatReply('Enter the requested value.')
        try:
            if field == 'run':
                run = selected_run(text, self.run_names, self.run_root)
                node1_panel(run)
                self.inputs = {'run': str(run)}
                return self.prepare_plan()
            if field == 'prefix':
                panel, _, _ = phase_inputs(text)
                self.inputs = {'prefix': panel['prefix']}
                self.input_field = 'build'
                return ChatReply('Enter the panel genome build: 18, 19, or 38. It is not inferred from names.')
            if field == 'build':
                self.inputs['build'] = phase_settings(int(text), self.memory, self.seed)['genome_build']
                return self.prepare_plan()
            if field == 'memory':
                self.memory = phase_settings(18, text or self.memory, self.seed)['memory']
                self.input_field = 'seed'
                return ChatReply('Enter a signed 32-bit random seed. Enter keeps the displayed value.')
            if field == 'seed':
                self.seed = phase_settings(18, self.memory, int(text) if text else self.seed)['seed']
                return self.prepare_plan()
        except (OSError, ValueError, KeyError) as error:
            self.discard_plan()
            self.input_field = field
            return ChatReply(f'Input check failed: {error}\nEnter a corrected value, '
                             'type inputs to start input selection again, or back to return.')

    def respond(self, text):
        text = text.strip()
        lowered = text.lower()
        if self.closed or lowered in EXIT_WORDS:
            return self.end_session()
        if lowered in BACK_WORDS or lowered in CANCEL_WORDS or lowered in ('n', 'no'):
            return self.leave()
        if lowered in ('phase', 'phasing', '/phasing', 'genotypephasing', 'genotype-phasing', 'inputs'):
            return self.choose_inputs()
        if lowered == 'settings':
            return self.choose_settings()
        if lowered in ('run', 'panel') and self.input_field in (None, 'mode'):
            return self.choose_inputs(lowered)
        if self.input_field == 'mode':
            if lowered in ('1', '2'):
                return self.choose_inputs('run' if lowered == '1' else 'panel')
            return ChatReply('Enter run/panel or 1/2.')
        if self.input_field is not None:
            return self.collect_input(text)
        if lowered in ('y', 'yes'):
            if self.pending_plan is None:
                return ChatReply('There is no plan to approve. Type inputs to select phasing data.')
            plan = self.pending_plan
            self.discard_plan()
            try:
                progress = getattr(self, "execution_progress", None)
                self.last_result = run_example(plan, progress=progress) if progress else run_example(plan)
            except KeyboardInterrupt:
                self.last_result = {'status': 'cancelled', 'plan': plan.to_dict()}
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                self.last_result = {'status': 'failed', 'plan': plan.to_dict(), 'error': str(error)}
            return ChatReply('Returned to Supervisor.', result=self.last_result, return_to_supervisor=True)
        if lowered in ('plan', 'retry'):
            return self.prepare_plan()
        if self.pending_plan is not None:
            return ChatReply('Unknown input. Phasing has not started. Enter y to start or n to discard. '
                             'Use details to inspect the plan, settings to change it, or inputs to select other data.',
                             plan=self.pending_plan,
                             actions=('settings: change Java memory/seed | inputs: select other data',))
        if not text:
            return ChatReply('Type inputs to select phasing data, settings to review settings.')
        self.discard_plan()
        state = {'input_mode': self.mode, 'has_selected_inputs': bool(self.inputs),
                 'memory': self.memory, 'seed': self.seed, 'last_result': result_context(self.last_result)}
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'system', 'content': 'Current state: ' + json.dumps(state)}]
        messages += self.history[-8:] + [{'role': 'user', 'content': text}]
        decision = self.client.decide(messages, DECISION_SCHEMA)
        validate_decision(decision, DECISION_SCHEMA)
        self.history = (self.history + [{'role': 'user', 'content': text},
                                       {'role': 'assistant', 'content': json.dumps(decision)}])[-8:]
        action = decision['action']
        if action in ('choose_inputs', 'run', 'panel'):
            return self.choose_inputs(None if action == 'choose_inputs' else action)
        if action == 'settings':
            return self.choose_settings()
        if action == 'plan':
            return self.prepare_plan()
        if action in ('back', 'cancel'):
            return self.leave()
        if action == 'exit':
            return self.end_session()
        return ChatReply(decision['message'])
