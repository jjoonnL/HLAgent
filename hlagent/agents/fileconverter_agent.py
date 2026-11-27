"""Collect and review local HPED conversion and clinical phenotype preparation."""

import json
from pathlib import Path

from hlagent.agents.prompt_rules import COMMON_RULES
from hlagent.runtime.analysis_run_selection import completed_runs, selected_run, run_listing
from hlagent.runtime.presentation import stage_title
from hlagent.runtime.chat_types import BACK_WORDS, CANCEL_WORDS, EXIT_WORDS, ChatReply, result_context, validate_decision
from hlagent.analysis.cookhla_inputs import require_file
from hlagent.analysis.cookhla_to_hped import cookhla_run_inputs, prepare_conversion, run_conversion
from hlagent.analysis.hatk import ROOT
from hlagent.runtime import phenotype_preparation, summary_preparation
from hlagent.runtime import phenotype_suggestions, summary_suggestions
from hlagent.runtime.recorded_input_selection import recorded_candidates, recorded_listing, selected_recorded_path
from hlagent.cli.hatk_phasing_run import node1_panel


SYSTEM_PROMPT = COMMON_RULES + """You are HLAgent's File Converter. Convert CookHLA eight-column
.alleles hard calls and the matching FAM to standard eight-gene HPED. The program
collects a completed CookHLA run or explicit files locally. Never extract paths,
select a run, change settings, approve execution, or invent results yourself.
FAM order and metadata are preserved. Genes absent from CookHLA output become
0/0, not new predictions. This does not recover phase or apply confidence filters.
Explicit phenotype preparation from CSV/TSV is also available: the local program
collects ID/trait columns, control/case values and a matching FAM. Never infer
these mappings or generate clinical labels. Action phenotype opens that local form.
Explicit binary association CSV/TSV normalization is also available: action summary opens local column/effect-scale/trait/build inputs. No allele, scale or study compatibility is inferred.
No other file conversions or analysis tools are available in this role.
Actions: choose_inputs asks which input mode; run selects a completed CookHLA
run locally; files collects .alleles/FAM locally; plan prepares or retries the
selected conversion with new approval; answer explains or clarifies; back/cancel
returns to Supervisor; exit ends the chat only on a direct exit request;
unsupported covers other conversions. A natural request to run only prepares a
plan. Only exact local y/yes executes a reviewed plan. Questions invalidate pending
approval. Use actual last_result only, never infer completion from a plan.
For an ambiguous stop request ask for clarification; do not treat negated or quoted
exit requests as commands. Respond briefly in the user's language, keeping technical
names. Return JSON with action and message only.
"""

DECISION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'action': {'type': 'string', 'enum': [
        'choose_inputs', 'run', 'files', 'phenotype', 'summary', 'plan', 'answer', 'back', 'cancel', 'exit', 'unsupported']},
                   'message': {'type': 'string'}},
    'required': ['action', 'message'],
}


class FileConverterAgent:
    def __init__(self, client, run_root=None):
        self.client = client
        self.run_root = Path(run_root) if run_root is not None else ROOT / 'runs'
        self.pending_plan = None
        self.last_result = None
        self.input_field = None
        self.mode = None
        self.inputs = {}
        self.run_names = []
        self.history = []
        self.closed = False
        self.columns = []
        self.auto_requested = False
        self.auto_report = None
        self.source_candidates = []
        self.summary_report = None

    @property
    def input_prompt(self):
        if self.pending_plan is not None:
            return ('Prepare summary? [y/n]> ' if self.mode == 'summary' else 'Prepare phenotype? [y/n]> ' if self.mode == 'phenotype' else 'Start HPED conversion? [y/n]> ')
        if self.mode == 'summary':
            if (self.input_field or '').startswith('summary_column_'):
                return 'Column for ' + self.input_field.removeprefix('summary_column_') + '> '
            return {'summary_source': 'Association CSV/TSV path> ', 'summary_delimiter': 'Delimiter [csv/tsv/auto]> ',
                    'summary_proposals': 'Proposal [accept/manual/cancel]> ', 'summary_effect': 'Effect scale [or/log_or]> ',
                    'summary_se': 'SE scale [log_or]> ', 'summary_trait': 'Binary trait name> ',
                    'summary_build': 'Genome build [18/19/38]> '}.get(self.input_field, 'File Converter> ')
        if self.mode == 'phenotype':
            return {'pheno_source': 'Clinical CSV/TSV path> ', 'pheno_delimiter': 'Delimiter [csv/tsv/auto]> ',
                    'pheno_suggestions': 'Proposal [accept/assist/manual/cancel]> ',
                    'pheno_fid': 'FID column name or number> ', 'pheno_iid': 'IID column name or number> ',
                    'pheno_trait': 'Trait column name or number> ', 'pheno_control': 'Source control value> ',
                    'pheno_case': 'Source case value> ', 'pheno_name': 'Output phenotype name> ',
                    'pheno_fam': 'Matching FAM path or run> ',
                    'pheno_node1': 'Marker generation run [number/name/directory]> '}.get(self.input_field, 'File Converter> ')
        return {'mode': 'Conversion input [run/files/phenotype/summary]> ', 'run': 'CookHLA run name, number, or directory> ',
                'alleles': 'CookHLA .alleles file path> ', 'fam': 'Target FAM file path> '}.get(
                    self.input_field, 'File Converter> ')

    def discard_plan(self):
        self.pending_plan = None

    def leave(self):
        self.discard_plan()
        self.input_field = None
        return ChatReply('Returned to Supervisor. No conversion was started.', return_to_supervisor=True)

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
        if mode == 'summary':
            self.summary_report = None
            self.input_field = 'summary_source'
            self.columns = []
            return ChatReply('Input: enter the source association CSV/TSV path.\n'
                             'Next: review header proposals and confirm the column mapping.\n\n'
                             'Output: a study TSV for meta-analysis (HATK Node5).\n'
                             'Required: both allele labels and per-marker sample counts. Binary logistic effects only.\n\n')
        if mode == 'phenotype':
            self.input_field = 'pheno_source'
            self.columns = []
            self.auto_requested = False
            self.auto_report = None
            return ChatReply('Input: enter the clinical UTF-8 CSV/TSV path, or select a candidate below.\n'
                             'Output: a binary phenotype. The source is never overwritten.\n\n'
                             'Next: choose auto for reviewable mapping proposals, or csv/tsv for manual mapping.\n'
                             'Required: a matching FAM or completed marker-generation run (HATK Node1).\n\n' + self.source_listing(),
                             actions=('candidates: refresh the clinical source list',))
        if mode == 'run':
            self.run_names = completed_runs(self.run_root, 'cookhla')
            return ChatReply(run_listing(self.run_names, 'CookHLA', self.run_root))
        if mode == 'files':
            self.input_field = 'alleles'
            return ChatReply('Input: enter the CookHLA .alleles file path on this machine.\n'
                             'Next: supply the matching original target FAM.\n\n')
        return ChatReply('Choose file preparation input:\n\n'
                         '1. Completed CookHLA run to HPED\n\n'
                         '2. Supplied .alleles and FAM files to HPED\n\n'
                         '3. Clinical CSV/TSV to phenotype\n\n'
                         '4. Association CSV/TSV to a meta-analysis study TSV (HATK Node5)\n\n'
                         'Select: enter run/files/phenotype/summary or 1/2/3/4.\n')

    def source_listing(self):
        self.source_candidates = recorded_candidates(self.run_root, 'clinical')
        return recorded_listing(self.source_candidates, 'clinical source')

    def prepare_plan(self):
        self.discard_plan()
        if self.mode == 'summary':
            settings = {key: self.inputs[key] for key in ('delimiter', 'columns', 'effect_scale', 'se_scale', 'phenotype_name', 'genome_build')}
            self.pending_plan = summary_preparation.prepare(self.inputs['source'], settings, self.run_root)
            if self.summary_report is not None:
                self.pending_plan['mapping_proposals'] = self.summary_report
            self.input_field = None
            return ChatReply('Status: waiting for approval. No files have been written.\n\n'
                             'Review columns, effect/SE scales, declared trait/build and unestimable rows.\n'
                             'Enter y to write a new summary, or n to discard.', plan=self.pending_plan,
                             actions=('inputs: start again',))
        if self.mode == 'phenotype':
            settings = {key: self.inputs[key] for key in ('delimiter', 'fid_column', 'iid_column', 'trait_column',
                                                         'control_value', 'case_value', 'phenotype_name')}
            self.pending_plan = phenotype_preparation.prepare(self.inputs['source'], self.inputs['fam'], settings, self.run_root)
            if self.auto_report is not None:
                self.pending_plan['mapping_proposals'] = self.auto_report
            self.input_field = None
            return ChatReply('Status: waiting for approval. No files have been written.\n\n'
                             'Review the explicit mappings, matched counts and clinical rows outside FAM.\n'
                             'Enter y to write new files, or n to discard.', plan=self.pending_plan,
                             actions=('inputs: start again',))
        if self.mode == 'run' and 'run' in self.inputs:
            alleles, fam = cookhla_run_inputs(self.inputs['run'])
        elif self.mode == 'files' and set(self.inputs) == {'alleles', 'fam'}:
            alleles, fam = self.inputs['alleles'], self.inputs['fam']
        else:
            return self.choose_inputs()
        self.pending_plan = prepare_conversion(alleles, fam, self.run_root)
        self.input_field = None
        return ChatReply('Status: waiting for approval. Conversion has not started.\n\n'
                         'Review the inputs and settings below. Enter y to convert or n to discard.',
                         plan=self.pending_plan, actions=('inputs: select different files',))

    def collect_input(self, text):
        if not text:
            return ChatReply('Enter the requested value.')
        field = self.input_field
        try:
            if field == 'run':
                run = selected_run(text, self.run_names, self.run_root)
                cookhla_run_inputs(run)
                self.inputs = {'run': str(run)}
                return self.prepare_plan()
            path = require_file(Path(text).expanduser().resolve())
            self.inputs[field] = str(path)
            if field == 'alleles':
                self.input_field = 'fam'
                return ChatReply('Enter the original target FAM path. FID/IID and all six metadata fields are preserved.')
            return self.prepare_plan()
        except (OSError, ValueError, KeyError, StopIteration) as error:
            self.discard_plan()
            self.input_field = field
            return ChatReply(f'Input check failed: {error}\nEnter a corrected value, '
                             'type inputs to start input selection again, or back to return.')

    def collect_summary(self, text):
        field = self.input_field
        try:
            if field == 'summary_source':
                self.inputs['source'] = str(require_file(Path(text).expanduser().resolve()))
                try:
                    delimiter = phenotype_suggestions.detect_delimiter(self.inputs['source'])
                except ValueError as error:
                    self.input_field = 'summary_delimiter'
                    return ChatReply(str(error) + ' Enter csv/tsv for manual mapping, or auto to retry proposals.')
                return self.propose_summary(delimiter)
            if field == 'summary_proposals':
                if text.lower() == 'accept':
                    if phenotype_preparation.digest(self.inputs['source']) != self.summary_report['source_sha256']:
                        raise ValueError('Source changed after proposals. Type inputs to select it again.')
                    self.inputs.update(json.loads(json.dumps(self.summary_report['proposed'])))
                    return self.next_summary_input()
                if text.lower() not in ('manual', 'csv', 'tsv'):
                    return ChatReply('Enter accept, manual or cancel. Only the final plan can be approved with y.')
                delimiter = text.lower() if text.lower() in ('csv', 'tsv') else self.inputs['delimiter']
                self.summary_report = None
                self.columns, _ = phenotype_preparation.read_table(self.inputs['source'], delimiter)
                self.inputs = {'source': self.inputs['source'], 'delimiter': delimiter, 'columns': {}}
                return self.next_summary_input()
            if field == 'summary_delimiter':
                if text.lower() == 'auto':
                    return self.propose_summary(phenotype_suggestions.detect_delimiter(self.inputs['source']))
                self.summary_report = None
                self.columns, _ = phenotype_preparation.read_table(self.inputs['source'], text.lower())
                self.inputs['delimiter'] = text.lower()
                self.inputs['columns'] = {}
            elif field.startswith('summary_column_'):
                role = field.removeprefix('summary_column_')
                name = text if text in self.columns else self.columns[int(text)-1] if text.isdigit() and 1 <= int(text) <= len(self.columns) else None
                if role == 'ESTIMABLE' and text.lower() == 'none':
                    name = None
                elif name is None:
                    raise ValueError('Choose a listed column name or number. Only ESTIMABLE may use none.')
                if name is not None and name in self.inputs['columns'].values():
                    raise ValueError('Choose a separate source column for each mapped role.')
                self.inputs['columns'][role] = name
            elif field == 'summary_effect':
                if text.lower() not in ('or', 'log_or'):
                    raise ValueError('Enter or for odds ratio, or log_or for BETA=log(OR). Continuous-trait BETA is unsupported.')
                self.inputs['effect_scale'] = text.lower()
            elif field == 'summary_se':
                if text.lower() != 'log_or':
                    raise ValueError('SE must already be the standard error of log(OR). Enter log_or only if confirmed from the source.')
                self.inputs['se_scale'] = 'log_or'
            elif field == 'summary_trait':
                from hlagent.analysis.hatk_custom_association import check_settings
                check_settings(text, 18)
                self.inputs['phenotype_name'] = text
            elif field == 'summary_build':
                from hlagent.analysis.hatk_custom_association import check_settings
                build = int(text)
                check_settings(self.inputs['phenotype_name'], build)
                self.inputs['genome_build'] = build
            return self.next_summary_input()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.discard_plan()
            self.input_field = field
            return ChatReply(f'Input check failed: {error}\nCorrect the value, type inputs to restart, or back to return.')

    def propose_summary(self, delimiter):
        self.summary_report = summary_suggestions.suggest(self.inputs['source'], delimiter, self.run_root)
        self.columns = self.summary_report['header']
        self.inputs['delimiter'] = delimiter
        self.input_field = 'summary_proposals'
        return ChatReply(summary_suggestions.render(self.summary_report), actions=(
            'accept: use proposals | manual (or csv/tsv): map all fields',))

    def phenotype_proposal(self):
        return ChatReply(phenotype_suggestions.render(self.auto_report), actions=(
            'accept: review proposed fields | manual: specify all mappings',
            'assist: optional LLM proposal using column names/candidates and counts',))

    def next_summary_input(self):
        for role in summary_preparation.MAPPED_FIELDS + ('ESTIMABLE',):
            if role not in self.inputs['columns']:
                self.input_field = 'summary_column_' + role
                help_text = {'A1': 'effect allele', 'A2': 'other allele', 'NMISS': 'sample count used per marker',
                             'EFFECT': 'OR or log(OR) effect', 'SE': 'standard error of log(OR)',
                             'ESTIMABLE': 'optional True/False flag. Enter none if unavailable'}.get(role, role)
                return ChatReply('Columns:\n' + '\n'.join(f'{i}. {name}' for i, name in enumerate(self.columns, 1)) +
                                 f'\nChoose {role} ({help_text}). No allele is inferred.')
        for key, field, message in (
                ('effect_scale', 'summary_effect', 'Declare effect scale: or or log_or. log_or means binary-logistic BETA=log(OR), not a continuous-trait effect.'),
                ('se_scale', 'summary_se', 'Confirm from the source that SE is on the log(OR) scale by entering log_or. SE of OR is unsupported.'),
                ('phenotype_name', 'summary_trait', 'Enter the binary trait name. Confirm compatible case/control direction from the source.'),
                ('genome_build', 'summary_build', 'Declare the source genome build: 18, 19 or 38. No coordinates are converted.')):
            if key not in self.inputs:
                self.input_field = field
                return ChatReply(message)
        return self.prepare_plan()

    def collect_phenotype(self, text):
        field = self.input_field
        try:
            if field == 'pheno_source':
                if text.lower() in ('candidates', 'files'):
                    return ChatReply(self.source_listing(), actions=('candidates: refresh the clinical source list',))
                self.inputs['source'] = str(require_file(Path(selected_recorded_path(text, self.source_candidates))))
                self.input_field = 'pheno_delimiter'
                return ChatReply('Choose csv (comma), tsv (tab), or auto for delimiter detection and mapping proposals. Auto asks for the matching FAM first.')
            if field == 'pheno_delimiter':
                automatic = text.lower() == 'auto'
                delimiter = phenotype_suggestions.detect_delimiter(self.inputs['source']) if automatic else text.lower()
                self.columns, _ = phenotype_preparation.read_table(self.inputs['source'], delimiter)
                self.inputs['delimiter'] = delimiter
                self.auto_requested = automatic
                if automatic:
                    self.input_field = 'pheno_fam'
                    return ChatReply('Delimiter detected: ' + delimiter + '. Enter a matching FAM path, or run to select a completed marker panel from HLA marker generation (HATK Node1) before ID matching.')
                return self.next_phenotype_input()
            if field == 'pheno_suggestions':
                if text.lower() == 'assist':
                    self.auto_report = phenotype_suggestions.assist(self.client, self.inputs['source'], self.auto_report)
                    return self.phenotype_proposal()
                if text.lower() == 'accept':
                    self.inputs = {key: self.inputs[key] for key in ('source', 'delimiter', 'fam')}
                    self.inputs.update(self.auto_report['proposed'])
                    return self.next_phenotype_input()
                if text.lower() == 'manual':
                    self.inputs = {key: self.inputs[key] for key in ('source', 'delimiter', 'fam')}
                    self.auto_report = None
                    self.auto_requested = False
                    return self.next_phenotype_input()
                return ChatReply('Enter accept, assist, manual or cancel. This step does not approve file generation.')
            if field in ('pheno_fid', 'pheno_iid', 'pheno_trait'):
                column = text if text in self.columns else self.columns[int(text) - 1] if text.isdigit() and 1 <= int(text) <= len(self.columns) else None
                if column is None:
                    raise ValueError('Choose a listed column name or number.')
                key = {'pheno_fid': 'fid_column', 'pheno_iid': 'iid_column', 'pheno_trait': 'trait_column'}[field]
                if field == 'pheno_trait' and column in (self.inputs['fid_column'], self.inputs['iid_column']):
                    raise ValueError('Choose a trait column different from the ID columns.')
                self.inputs[key] = column
                return self.next_phenotype_input()
            if field == 'pheno_control':
                self.inputs['control_value'] = text
                return self.next_phenotype_input()
            if field == 'pheno_case':
                if text == self.inputs['control_value']:
                    raise ValueError('Control and case values must differ.')
                self.inputs['case_value'] = text
                return self.next_phenotype_input()
            if field == 'pheno_name':
                from hlagent.analysis.hatk_custom_association import check_settings
                check_settings(text, 18)
                self.inputs['phenotype_name'] = text
                return self.next_phenotype_input()
            if field == 'pheno_fam' and text.lower() == 'run':
                self.run_names = completed_runs(self.run_root, 'node1')
                self.input_field = 'pheno_node1'
                return ChatReply(run_listing(self.run_names, stage_title('hatk_node1'), self.run_root))
            if field == 'pheno_node1':
                prefix, _ = node1_panel(selected_run(text, self.run_names, self.run_root))
                self.inputs['fam'] = str(prefix) + '.fam'
            else:
                self.inputs['fam'] = str(require_file(Path(text).expanduser().resolve()))
            if self.auto_requested and self.auto_report is None:
                self.auto_report = phenotype_suggestions.suggest(self.inputs['source'], self.inputs['fam'], self.inputs['delimiter'])
                self.input_field = 'pheno_suggestions'
                return self.phenotype_proposal()
            return self.next_phenotype_input()
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            self.discard_plan()
            self.input_field = field
            return ChatReply(f'Input check failed: {error}\nCorrect the value, type inputs to restart mappings, or back to return.')

    def next_phenotype_input(self):
        fields = {
            'fid_column': ('pheno_fid', 'Choose the FID column. IDs remain text, including leading zeros.'),
            'iid_column': ('pheno_iid', 'Choose the IID column. The same column may serve both IDs only if it matches FAM.'),
            'trait_column': ('pheno_trait', 'Choose the binary trait column. It must differ from ID columns.'),
            'control_value': ('pheno_control', 'Enter the exact source control value. No coding direction is inferred.'),
            'case_value': ('pheno_case', 'Enter the exact source case value. It must differ from control.'),
            'phenotype_name': ('pheno_name', 'Enter the output trait column name. This does not establish the diagnosis.'),
            'fam': ('pheno_fam', 'Enter a matching FAM path, or run to select a completed marker panel from HLA marker generation (HATK Node1).'),
        }
        for key, (field, message) in fields.items():
            if key not in self.inputs:
                self.input_field = field
                if key in ('fid_column', 'iid_column', 'trait_column'):
                    message = 'Columns:\n' + '\n'.join(f'{i}. {name}' for i, name in enumerate(self.columns, 1)) + '\n' + message
                return ChatReply(message)
        return self.prepare_plan()

    def respond(self, text):
        raw_text = text
        text = text.strip()
        lowered = text.lower()
        if self.closed or lowered in EXIT_WORDS:
            return self.end_session()
        summary_column = (self.mode == 'summary' and (self.input_field or '').startswith('summary_column_') and text in self.columns)
        if lowered in BACK_WORDS or lowered in CANCEL_WORDS or (lowered in ('n', 'no') and self.input_field not in ('pheno_control', 'pheno_case') and not summary_column):
            return self.leave()
        if lowered in ('convert', '/convert', 'converter', 'fileconverter', 'file-converter', 'inputs'):
            return self.choose_inputs()
        if lowered in ('phenotype', 'summary') and self.input_field in (None, 'mode'):
            return self.choose_inputs(lowered)
        if lowered in ('run', 'files') and self.input_field in (None, 'mode'):
            return self.choose_inputs(lowered)
        if self.input_field == 'mode':
            if lowered in ('1', '2', '3', '4'):
                return self.choose_inputs({'1': 'run', '2': 'files', '3': 'phenotype', '4': 'summary'}[lowered])
            return ChatReply('Enter run/files/phenotype/summary or 1/2/3/4.')
        if self.input_field and self.mode == 'summary':
            return self.collect_summary(text) if text else ChatReply('Enter the requested value.')
        if self.input_field and self.mode == 'phenotype':
            value = raw_text if self.input_field in ('pheno_control', 'pheno_case') else text
            return self.collect_phenotype(value) if text else ChatReply('Enter the requested value.')
        if self.input_field is not None:
            return self.collect_input(text)
        if lowered in ('y', 'yes'):
            if self.pending_plan is None:
                return ChatReply('There is no plan to approve. Type inputs to select conversion data.')
            plan = self.pending_plan
            self.discard_plan()
            try:
                self.last_result = (summary_preparation.execute(plan) if self.mode == 'summary' else
                                    phenotype_preparation.execute(plan) if self.mode == 'phenotype' else run_conversion(plan))
            except KeyboardInterrupt:
                self.last_result = {'status': 'cancelled', 'plan': plan}
            except (OSError, ValueError, RuntimeError) as error:
                self.last_result = {'status': 'failed', 'plan': plan, 'error': str(error)}
            return ChatReply('Returned to Supervisor.', result=self.last_result, return_to_supervisor=True)
        if lowered in ('plan', 'retry'):
            return self.prepare_plan()
        if not text:
            return ChatReply('Type inputs to select conversion data.')
        self.discard_plan()
        state = {'input_mode': self.mode, 'has_selected_inputs': bool(self.inputs),
                 'last_result': result_context(self.last_result)}
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'system', 'content': 'Current state: ' + json.dumps(state)}]
        messages += self.history[-8:] + [{'role': 'user', 'content': text}]
        decision = self.client.decide(messages, DECISION_SCHEMA)
        validate_decision(decision, DECISION_SCHEMA)
        self.history = (self.history + [{'role': 'user', 'content': text},
                                       {'role': 'assistant', 'content': json.dumps(decision)}])[-8:]
        action = decision['action']
        if action in ('choose_inputs', 'run', 'files', 'phenotype', 'summary'):
            return self.choose_inputs(None if action == 'choose_inputs' else action)
        if action == 'plan':
            return self.prepare_plan()
        if action in ('back', 'cancel'):
            return self.leave()
        if action == 'exit':
            return self.end_session()
        return ChatReply(decision['message'])
