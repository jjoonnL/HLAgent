"""Session-owned adapter around the existing Supervisor and analysis plans."""

from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass, replace
import hashlib
import io
import json
from pathlib import Path
import re

from hlagent.agents.supervisor import SupervisorSession
from hlagent.analysis.cookhla import RunPlan
from hlagent.analysis.hatk import Node1Plan
from hlagent.runtime.chat_types import ChatReply
from hlagent.runtime.llm import CloudClient
from hlagent.web.ollama import WebOllamaClient
from hlagent.runtime import ra_example
from hlagent.runtime.presentation import compact_plan, result_view
from hlagent.runtime.settings import ENV_PATH, read_settings
from hlagent.web.settings import MODEL_SUGGESTIONS, UnconfiguredClient, save_defaults
from hlagent.web.presentation import example_summary


def encoded(plan):
    if hasattr(plan, 'to_dict'):
        return plan.to_dict()
    return asdict(plan) if is_dataclass(plan) else plan


def fingerprint(value):
    return hashlib.sha256(json.dumps(encoded(value), sort_keys=True, default=str).encode()).hexdigest()


def plan_text(plan):
    """Reuse the CLI's existing plan descriptions inside a dedicated UI process."""
    from hlagent.cli.cookhla_example import show_plan as cookhla
    from hlagent.cli.hatk_example import show_plan as hatk
    from hlagent.cli.hatk_phasing_run import show_plan as phasing
    from hlagent.cli.ra_example import show_plan as example
    from hlagent.analysis.cookhla_to_hped import show_plan as conversion
    from hlagent.runtime import phenotype_preparation, summary_preparation
    renderer = (example if isinstance(plan, ra_example.ExamplePlan) else
                phasing if isinstance(plan, Node1Plan) and plan.phasing else
                hatk if isinstance(plan, Node1Plan) else
                cookhla if isinstance(plan, RunPlan) else
                phenotype_preparation.show_plan if plan.get('stage') == 'phenotype_preparation' else
                summary_preparation.show_plan if plan.get('stage') == 'summary_preparation' else conversion)
    output = io.StringIO()
    with redirect_stdout(output):
        if isinstance(plan, ra_example.ExamplePlan):
            renderer(plan, detailed=True)
        else:
            renderer(plan)
    return output.getvalue()


class WebBackend:
    """One instance belongs to one browser session's worker process."""

    def __init__(self, root, client=None, config=None, seccomp_unconfined=False, settings_path=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.config = dict(read_settings() if config is None else config)
        self.settings_path = Path(settings_path) if settings_path is not None else ENV_PATH
        self.entered_secrets = set()
        self.seccomp = seccomp_unconfined
        provider = self.config.get('DEFAULT_LLM_PROVIDER', 'ollama')
        try:
            self.client = client or self.make_client(provider, None)
        except ValueError:
            if provider not in MODEL_SUGGESTIONS:
                raise
            self.client = UnconfiguredClient(provider, self.config.get(provider.upper() + '_MODEL') or MODEL_SUGGESTIONS[provider])
        self.example = None
        self.revision = 0
        self.reply = ChatReply('Choose a task or describe your analysis goal. No analysis starts without approval.')
        self.result = None
        self.new_session()

    def make_client(self, provider, model):
        if provider == 'ollama':
            return WebOllamaClient(model=model, config=self.config)
        return CloudClient(provider, model=model, config=self.config)

    def new_session(self):
        self.session = SupervisorSession(self.client, self.seccomp, checkpoint_root=self.root / 'checkpoints')
        for role in ('finemapping', 'conversion', 'phasing'):
            self.session.agents[role].run_root = self.root
        self.session.tutor.record_root = self.root / 'tutor'
        self.session.tutor.config = dict(self.config)

    def redact(self, text):
        text = str(text)
        secrets = list(self.entered_secrets) + [str(value) for key, value in self.config.items()
                   if re.search(r'API_KEY|(?:^|_)(?:TOKEN|PASSWORD|SECRET)(?:_|$)', key.upper()) and value]
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, '[redacted]')
        return re.sub(r'\b(?:sk-|tvly-)[A-Za-z0-9_-]{12,}', '[redacted]', text)

    @property
    def approval(self):
        if self.example is not None:
            return 'example', self.example
        if self.session.pending_plan is not None:
            return 'analysis', self.session.pending_plan
        if self.session.finemapping.input_field == 'imgt_download_approval' and self.session.active_agent == 'finemapping':
            return 'download', {'release': self.session.finemapping.imgt_download}
        if self.session.report_proposed:
            return 'report', {'runs': [str(p) for p in self.session.report_paths], 'hashes': self.session.report_hashes}
        return None, None

    def snapshot(self):
        kind, plan = self.approval
        detail = plan_text(plan) if kind in ('analysis', 'example') else self.reply.message if kind else ''
        role = self.session.agents.get(self.session.active_agent)
        field = getattr(role, 'input_field', None)
        choices = []
        if role is not None and field in ('run', 'parent_run', 'pheno_node1', 'marker_conversion_run',
                                         'association_node1_run', 'association_prepared_run', 'omnibus_phasing_run',
                                         'omnibus_prepared_run', 'meta_prepared_run'):
            choices = [{'label': name, 'value': str(i)} for i, name in enumerate(role.run_names, 1)]
        if self.session.recovery_paths is not None:
            choices = [{'label': str(path), 'value': str(i)} for i, path in enumerate(self.session.recovery_paths, 1)]
        state = {'role': self.session.active_agent or 'supervisor', 'prompt': self.session.input_prompt,
                 'tutor_mode': self.session.tutor.mode,
                 'field': getattr(role, 'input_field', None), 'message': self.reply.message,
                 'choices': choices, 'revision': self.revision,
                 'selection_details': self.session.selection_details(),
                 'resources': {'memory': role.memory, 'processes': role.processes}
                 if self.session.active_agent == 'imputation' else None,
                 'actions': list(self.reply.actions), 'closed': self.session.closed,
                 'provider': getattr(self.client, 'provider', 'ollama'), 'model': self.client.model,
                 'models': {provider: self.config.get(provider.upper() + '_MODEL') or suggestion
                            for provider, suggestion in MODEL_SUGGESTIONS.items()},
                 'approval_kind': kind, 'approval_token': f'{self.revision}:{fingerprint(plan)}' if kind else None,
                 'can_cancel': kind == 'example' or kind == 'analysis' and isinstance(plan, (RunPlan, Node1Plan)),
                 'plan_text': example_summary(plan.directory) if kind == 'example' else compact_plan(detail), 'plan_details': detail,
                 'result': self.result,
                 'settings_available': {provider: bool(self.config.get(provider.upper() + '_API_KEY'))
                                        for provider in ('openai', 'anthropic', 'gemini', 'tavily')}}
        # Transport only these display fields, never clients, credentials or conversation internals.
        def clean(value):
            if isinstance(value, str):
                return self.redact(value)
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value
        return clean(state)

    def collect(self, reply):
        if reply.plan is not None:
            reply.message = reply.message.replace('Enter y to start or n to discard.',
                                                  'Use the plan buttons below to approve or cancel.')
        # CookHLA's agent has no run_root argument. Change only its output location.
        # Source checks, settings, commands and the execution runner remain unchanged.
        if isinstance(reply.plan, RunPlan):
            plan = replace(reply.plan, run_dir=self.root / reply.plan.run_dir.name)
            self.session.imputation.pending_plan = reply.plan = plan
            self.session.record_checkpoint(reply=reply)
        self.reply = reply
        if reply.result is not None:
            record = encoded(reply.result)
            status = record.get('status')
            summary, metrics = result_view(reply.result)
            self.result = {'kind': 'analysis', 'status': status, 'metrics': metrics,
                           'details': json.dumps(record, indent=2),
                           'text': summary if status in ('completed', 'incomplete') else
                           'Analysis did not complete. Inspect the saved status and log.',
                           'run_dir': record.get('run_dir') or record.get('plan', {}).get('run_dir')}
            if status in ('cancelled', 'cancellation_unconfirmed'):
                self.result['text'] = ('Analysis stopped. Partial files are preserved.' if status == 'cancelled' else
                                       'The tool stop could not be confirmed. Inspect its saved record and container before retrying.')

    def cancelled_preparation(self, kind, plan):
        """Handle a stop at an input/environment boundary before a runner owns it."""
        directory = Path(plan.directory if kind == 'example' else plan.run_dir)
        self.example = None
        self.session.respond('cancel')
        status = 'cancellation_unconfirmed' if (directory / 'run.json').exists() else 'cancelled'
        text = ('Stopped before tool execution.' if status == 'cancelled' else
                'Stop requested. Inspect the existing run record before retrying.')
        self.result = {'kind': 'example' if kind == 'example' else 'analysis',
                       'status': status, 'text': text, 'metrics': {}, 'run_dir': str(directory)}
        self.reply = ChatReply(text)
        return self.snapshot()

    def handle(self, request, progress=lambda stage, plan: None):
        """Only approve requests with the current displayed token may run a task."""
        operation = request.get('operation', 'message')
        kind, plan = self.approval
        if operation == 'resources':
            from hlagent.analysis.cookhla import check_resources
            if self.session.active_agent != 'imputation':
                raise ValueError('CookHLA settings apply only to the imputation task.')
            self.revision += 1
            self.session.record_checkpoint(status='superseded')
            self.session.imputation.discard_plan()
            check_resources(request['memory'], request['processes'])
            self.session.imputation.memory = request['memory']
            self.session.imputation.processes = request['processes']
            self.collect(self.session.imputation.prepare_plan())
            return self.snapshot()
        if operation == 'prepare_report':
            from hlagent.web.artifacts import saved_runs
            from hlagent.runtime.report_workflow import report_record_hashes
            paths = [Path(path).resolve() for path in request['runs']]
            allowed = {path for path, _ in saved_runs(self.root, include_cli=True, include_web=True)}
            if not paths or any(path not in allowed for path in paths):
                raise ValueError('Select recorded runs from the saved Web or CLI run list.')
            hashes = report_record_hashes(paths)
            self.session.respond('cancel')
            self.example = None
            self.session.report_paths = paths
            self.session.report_hashes = hashes
            self.session.report_proposed = True
            self.revision += 1
            self.reply = ChatReply('Review the selected runs for an offline HTML report. No analysis is rerun.\n\n' +
                                  '\n'.join(str(path) for path in paths))
            return self.snapshot()
        if operation == 'approve':
            expected = f'{self.revision}:{fingerprint(plan)}'
            if kind is None or request.get('token') != expected:
                raise ValueError('This approval is no longer current. Review the new plan.')
            self.revision += 1
            if kind == 'example':
                self.example = None
                def execute(label, stage):
                    progress(label, stage)
                    from hlagent.analysis import hatk
                    return hatk.run_example(stage, progress=lambda phase: progress(phase, stage))
                record = ra_example.run_example(plan, execute_stage=execute)
                self.reply = ChatReply('Example status: ' + record['status'] + '\nWorkflow: ' + str(plan.directory))
                self.result = {'kind': 'example', 'status': record['status'], 'text': self.reply.message, 'metrics': {},
                               'run_dir': str(plan.directory), 'report': record.get('report'),
                               'details': json.dumps(record, indent=2)}
            else:
                role = self.session.agents.get(self.session.active_agent)
                if role is not None and kind == 'analysis':
                    progress('starting', plan)
                    role.execution_progress = lambda phase: progress(phase, plan)
                try:
                    self.collect(self.session.respond('y'))
                finally:
                    if role is not None:
                        role.execution_progress = None
            return self.snapshot()
        if operation in ('configure', 'configure_search'):
            if operation == 'configure' or kind is not None or self.session.active_agent != 'tutor':
                self.session.respond('cancel')
            self.example = None
            self.revision += 1
            key = str(request.get('api_key', '')).strip()
            if key:
                self.entered_secrets.add(key)
            saved = read_settings(self.settings_path)
            credentials = {name: saved[name] for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'TAVILY_API_KEY')
                           if saved.get(name)}
            self.entered_secrets.update(credentials.values())
            if operation == 'configure_search':
                key = key or credentials.get('TAVILY_API_KEY') or self.config.get('TAVILY_API_KEY')
                if not key:
                    raise ValueError('Enter a Tavily API key. The existing key was not changed.')
                updates = {'TAVILY_API_KEY': key}
            else:
                provider = request['provider']
                if provider not in MODEL_SUGGESTIONS:
                    raise ValueError('Choose a supported provider.')
                updates = {'DEFAULT_LLM_PROVIDER': provider,
                           provider.upper() + '_MODEL': request.get('model', '').strip() or
                           self.config.get(provider.upper() + '_MODEL') or MODEL_SUGGESTIONS[provider]}
                if key and provider != 'ollama':
                    updates[provider.upper() + '_API_KEY'] = key
                candidate = {**self.config, **credentials, **updates}
                client = (WebOllamaClient(model=updates['OLLAMA_MODEL'], config=candidate) if provider == 'ollama' else
                          CloudClient(provider, model=updates[provider.upper() + '_MODEL'], config=candidate))
            if request.get('save'):
                save_defaults(self.settings_path, updates)
            self.config.update({**credentials, **updates})
            if operation == 'configure':
                self.client = client
                self.new_session()
            else:
                self.session.tutor.config = dict(self.config)
            scope = 'Settings saved.' if request.get('save') else 'Settings applied to this session.'
            self.reply = ChatReply(scope + (' Pending plan cancelled.' if kind else ''))
            return self.snapshot()
        text = str(request.get('text', '')).strip()
        if self.redact(text) != text:
            raise ValueError('Do not enter API keys in chat or task inputs. Use the password fields in Settings.')
        if kind and text.lower() in ('y', 'yes'):
            raise ValueError('Use the approval button after reviewing the displayed plan.')
        self.revision += 1
        if self.example is not None:
            if text.lower() in ('cancel', 'back', 'n', 'no', 'exit'):
                self.example = None
                self.reply = ChatReply('Example plan discarded. No analysis was started.')
            else:
                self.reply = ChatReply('Review the example plan, or cancel to choose another task.')
        elif text.lower() == 'example' and self.session.active_agent is None and self.session.input_prompt == 'Supervisor> ':
            self.example = ra_example.prepare_example(self.root, self.seccomp)
            self.reply = ChatReply('Review all four analysis stages before approving the fixed RA example.')
        elif text.lower() == 'details' and self.session.pending_plan is not None:
            self.reply = ChatReply(plan_text(self.session.pending_plan), plan=self.session.pending_plan)
        elif text.lower() == 'details' and self.session.active_agent != 'tutor':
            detail = self.session.selection_details()
            if detail is None and self.session.input_prompt == 'Supervisor> ' and self.result:
                detail = self.result.get('details') or self.result['text']
            self.reply = ChatReply(detail or 'No additional details are available for the current task.')
        elif text.lower() == 'help' and self.session.active_agent is None:
            self.reply = ChatReply('Choose imputation, hatk, phasing, convert, tutor or workflow. '
                                  'Use next for a supported follow-up, resume for saved task plans, or report for HTML reports.')
        elif text.lower() in ('config', 'settings') and self.session.active_agent is None:
            self.reply = ChatReply('Use Model settings in the sidebar or Search settings in Tutor. Enter keys only in their password fields.')
        else:
            self.collect(self.session.respond(text))
            if self.reply.configure:
                self.reply = ChatReply('Use Model settings in the sidebar or Search settings in Tutor.')
        return self.snapshot()
