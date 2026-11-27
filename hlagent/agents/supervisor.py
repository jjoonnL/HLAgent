"""Route requests and manage handoff to local analysis and teaching roles."""

import json
import re
from pathlib import Path

from hlagent.runtime.run_metadata import saved_run_listing

from hlagent.agents.prompt_rules import COMMON_RULES
from hlagent.agents.task_intent import TASK_ROUTES, explicit_task, question_destination, command_guidance, current_model_question, model_recommendation_question
from hlagent.runtime.chat_types import BACK_WORDS, CANCEL_WORDS, EXIT_WORDS, ChatReply, result_context, validate_decision
from hlagent.agents.imputation_agent import ImputationAgent
from hlagent.agents.finemapping_agent import FineMappingAgent
from hlagent.agents.fileconverter_agent import FileConverterAgent
from hlagent.agents.phasing_agent import PhasingAgent
from hlagent.agents.tutor_agent import TutorAgent
from hlagent.runtime.presentation import stage_title
from hlagent.runtime.workflow_runs import association_inputs, continuation
from hlagent.runtime.checkpoints import (save_checkpoint, recovery_candidates, recovery_record,
                                         rebuild_plan, install_plan)


SYSTEM_PROMPT = COMMON_RULES + """You are HLAgent's Supervisor, a router, not an analysis worker.
Return one JSON object with action, request_kind, clarification_required and message.

Classification:
- single_task: one operation requested now. Missing input files/trait/build/condition
  IDs do not make it ambiguous: the selected local form asks for them.
- ordered_tasks: explicit first/next operation. Open only that first operation.
- unordered_tasks: several requested operations with no selected next step.
  Use answer and clarification_required true; ask the user to choose.
- question: explanations, requirements, comparisons or interpretation, NOT execution.
  Keep interface navigation, available features, command names, goal selection and
  configuration instructions at Supervisor with action answer. Use tutor for HLA
  concepts, method comparisons, scientific input requirements and statistical
  interpretation. Do not give a scientific explanation inside a Supervisor answer.
  Questions about actual saved results may go to their owning role. Greetings use answer.
  Examples: "Which command opens phasing?" -> answer. "How does phasing work?" -> tutor.
  Public CLI commands are phasing, imputation, hatk, convert, prepare-phenotype,
  prepare-summary, tutor, config, workflow, next, resume, report, help and exit.
  JSON actions such as phase_inputs, prepare_summary and node3_inputs are INTERNAL,
  not commands to type. For a command question, give the actual CLI spelling.
  "What can HLAgent do?" -> answer. "Difference between imputation and phasing?" -> tutor.
  "Do not run conditional association. Explain its meaning." -> tutor, never execution.
  All these questions use clarification_required false. Unknown reference/intent can
  ask for clarification without inventing a task or forcing a Tutor handoff.
- Session identity questions such as "What is the LLM model now?" and
  "지금 모델 뭐야?" are question/answer, not configuration. Answer using the
  current_model and current_provider in Current state. Never guess session settings.
  "How do I change the model?" is question/answer with config command guidance.
  "Which model should I use?" is question/answer. Briefly list local versus cloud
  selection criteria and the config command. Do not ask a preference questionnaire
  or open settings for advice.
  "Change the model" is configuration. A model keyword alone is not a change request.
- configuration: an explicit request to change LLM/provider/model/API/search settings; action answer,
  clarification_required false. The application opens settings locally. Never collect keys.
- control: a direct request to end this chat; action exit. Never exit for quotations,
  negation or questions. Unsupported capabilities use unsupported/unsupported and false.
For single_task or ordered_tasks, clarification_required is false when the next
operation is identifiable. An unclear goal uses answer and clarification_required true.

Prefer the specific action below for task requests. These actions only open local
input selection; they cannot choose files, set clinical labels or approve execution:
imputation_inputs: CookHLA HLA imputation input selection.
hped_conversion: existing CookHLA hard calls to HPED.
prepare_phenotype: clinical CSV/TSV to a binary phenotype file.
prepare_summary: association CSV/TSV column/effect-scale normalization for Node5.
phase_inputs: phase an existing HATK marker panel using BEAGLE.
node1_inputs: create markers from HLA genotypes (HPED and dictionaries).
node2_inputs: marker association with a named binary phenotype.
node3_inputs: adjust for explicit marker IDs and test remaining associations.
node4_inputs: position-level amino-acid omnibus on already-phased AA BGL/FAM.
node5_inputs: fixed-effect meta-analysis of two independent compatible study summaries.
Use broad roles imputation/conversion/phasing/finemapping only when the operation
within the role is unspecified, for an explicitly bundled example, or to edit/retry
an existing role's task/settings. Tutor handles workflow/HLA explanations and
literature. Do not invent prerequisite jobs, results or already retrieved evidence.

Common distinctions (also apply in Korean):
- "adjust for a marker and test remaining associations" -> node3_inputs, single_task.
  Adjustment plus testing is ONE conditional scan, not two tasks needing an order.
- "combine association results from two independent studies" -> node5_inputs,
  single_task. Two input studies are NOT two requested operations.
- "prepare summary CSV for meta-analysis" -> prepare_summary, NOT node5_inputs.
- "clinical CSV to phenotype" -> prepare_phenotype, NOT the general conversion menu.
- "already have phased amino-acid genotypes; test each position" -> node4_inputs.
  Existing phased input is NOT a request to rerun phasing.
- "phase my panel for omnibus analysis" -> phase_inputs. Downstream purpose is not
  another requested task. Preparing FOR analysis differs from running WITH prepared data.
- "What does conditional association mean?" -> tutor/question, false.
- "marker 보정 후 연관분석 하고 싶어" -> node3_inputs/single_task, false.
- "임상 CSV를 phenotype으로 준비" -> prepare_phenotype/single_task, false.
- "imputation and phasing, no selected order" -> answer/unordered_tasks, true.
- "analyze my HLA data" -> answer/single_task, true; offer concrete goal choices.

Optional automatic forward selection is available through the exact public command stepwise.
Do not substitute it for single conditional scans or insert it into a workflow.
Custom Node2, single Node3 and explicit stepwise support selected numeric covariates from a FID/IID table.
Age, sex and ancestry PCs are covariates, not condition marker IDs.
An association request adjusted only for these covariates uses node2_inputs.
No automatic PCA, categorical encoding or missing-covariate sample removal.
Covariates are unsupported in Node4 and bundled examples. Stepwise must be explicitly requested.
Unsupported: continuous traits,
conditional omnibus, random-effects/overlapping-study meta-analysis, more than two
studies, other typing/conversion engines. Node4 keeps the original haplotype-row
model without within-person dependence adjustment. Split RA subsets are not
independent replication. Do not silently substitute a supported analysis.
Current state is authoritative. Completion never prohibits a fresh supported task.
For a retry or contextual reference, use history/last_agent to identify the task;
otherwise judge the current request without treating old clarification as a rule.
Never extract or choose paths/settings, execute commands or approve a plan. Only
local y/yes approves a reviewed plan. Keep message brief in the user's language.
"""


DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "request_kind": {"type": "string", "enum": ["single_task", "ordered_tasks", "unordered_tasks", "question", "configuration", "control", "unsupported"]},
        "clarification_required": {"type": "boolean"},
        "action": {"type": "string", "enum": [*TASK_ROUTES, "imputation", "finemapping", "conversion", "phasing", "tutor", "answer", "exit", "unsupported"]},
        "message": {"type": "string"},
    },
    "required": ["request_kind", "clarification_required", "action", "message"],
}


CONFIGURATION_HELP = (
    "Opening local settings.\n"
    "Select: choose LLM or search settings.\n"
    "API keys use hidden input outside the model conversation.\n"
    "Cancel to keep the current session."
)


def validate_routing_decision(decision):
    validate_decision(decision, DECISION_SCHEMA)
    if (decision['request_kind'] not in DECISION_SCHEMA['properties']['request_kind']['enum']
            or type(decision['clarification_required']) is not bool):
        raise ValueError('Invalid request classification. No handoff or analysis was started.')
    if decision['action'] == 'exit' and decision['request_kind'] != 'control':
        raise ValueError('Exit requires a direct control request. No analysis was started.')
    if decision['action'] in TASK_ROUTES and decision['request_kind'] not in ('single_task', 'ordered_tasks'):
        raise ValueError('A task input action requires a task request. No analysis was started.')


class SupervisorSession:
    def __init__(self, client, seccomp_unconfined=False, checkpoint_root=None):
        self.client = client
        self.imputation = ImputationAgent(client, seccomp_unconfined)
        self.finemapping = FineMappingAgent(client, seccomp_unconfined)
        self.conversion = FileConverterAgent(client)
        self.phasing = PhasingAgent(client, seccomp_unconfined)
        self.tutor = TutorAgent(client)
        self.agents = {"imputation": self.imputation, "finemapping": self.finemapping,
                       "conversion": self.conversion, "phasing": self.phasing, "tutor": self.tutor}
        self.active_agent = None
        self.last_agent = None
        self.history = []
        self.closed = False
        self.clarification_pending = False
        self.last_routing_decision = None
        self.last_routing_source = None
        self.next_run = None
        self.choosing_next = False
        self.prepared_phenotype_run = None
        self.checkpoint_root = checkpoint_root
        self.checkpoint_path = None
        self.checkpoint_plan = None
        self.recovery_paths = None
        self.report_paths = None
        self.report_proposed = False
        self.report_hashes = {}
        self.report_anchor = None
        self.workflow_goal = None
        self.workflow_field = None

    @property
    def pending_plan(self):
        return self.agents[self.active_agent].pending_plan if self.active_agent else None

    @property
    def input_prompt(self):
        if self.workflow_field is not None:
            return ('Workflow goal [node2/node3/node4/node5]> ' if self.workflow_field == 'goal'
                    else 'Starting input [cookhla/hped/panel/phased]> ')
        if self.report_paths is not None:
            return 'Report [y/n/numbers/files]> ' if self.report_proposed else 'Report runs [numbers/path]> '
        if self.recovery_paths is not None:
            return 'Resume [number/path]> '
        if self.choosing_next:
            return ("Next task [node2/node3/node4]> " if self.next_run[0] == 'phenotype'
                    else "Next task [node2/node3/phasing]> ")
        return self.agents[self.active_agent].input_prompt if self.active_agent else "Supervisor> "

    def discard_plan(self):
        for agent in self.agents.values():
            agent.discard_plan()

    def end_session(self):
        self.closed = True
        self.next_run = None
        self.choosing_next = False
        self.prepared_phenotype_run = None
        self.recovery_paths = None
        self.report_paths = None
        self.report_proposed = False
        self.report_hashes = {}
        self.workflow_goal = self.workflow_field = None
        self.clarification_pending = False
        self.active_agent = None
        for agent in self.agents.values():
            agent.end_session()
        return ChatReply("Session ended.", end_session=True)

    def forward(self, text):
        if text.strip().lower() in ('y', 'yes') and self.pending_plan is not None:
            self.record_checkpoint(status='execution_requested')
        reply = self.agents[self.active_agent].respond(text)
        if (self.prepared_phenotype_run is not None and self.active_agent == 'finemapping'
                and self.finemapping.input_field in ('association_pheno', 'omnibus_pheno')):
            run = self.prepared_phenotype_run
            self.prepared_phenotype_run = None
            self.finemapping.respond('run')
            reply = self.finemapping.respond(str(run))
            reply.message = 'Rechecked the prepared phenotype. Select its trait to check matching against the current samples.\n\n' + reply.message
        if reply.result is not None:
            result = reply.result
            self.report_anchor = (result.get('plan', {}).get('run_dir') if isinstance(result, dict) else str(result.run_dir))
            self.next_run = None
            if self.active_agent == "imputation" and reply.result.status == "completed":
                self.next_run = ("conversion", str(reply.result.run_dir))
                reply.message += "\nNext: type next to review HPED conversion using this CookHLA result."
            elif (self.active_agent == "conversion" and reply.result.get("status") == "completed"
                  and reply.result.get('plan', {}).get('stage') == 'cookhla_to_hped'):
                self.next_run = ("finemapping", reply.result["plan"]["run_dir"])
                reply.message += "\nNext: type next to prepare HLA markers using this HPED. You will need compatible dictionaries."
            elif self.active_agent == 'conversion' and reply.result.get('status') == 'completed':
                target = continuation(reply.result)
                if target in ('phenotype', 'summary'):
                    self.next_run = (target, reply.result['plan']['run_dir'])
                    reply.message += ('\nNext: type next to choose an analysis using this phenotype.' if target == 'phenotype'
                                      else '\nNext: type next to use this summary as Study 1 in meta-analysis (HATK Node5). A second independent study is required.')
            elif self.active_agent in ("finemapping", "phasing") and reply.result.get("status") == "completed":
                plan = reply.result.get("plan", {})
                example = plan.get("example")
                if example in ("hatk_node1", "hatk_node1_custom"):
                    self.next_run = ("node1", plan["run_dir"])
                    reply.message += "\nNext: type next to choose marker association or phasing using this panel."
                elif example == "hatk_phasing":
                    self.next_run = ("node4", plan["run_dir"])
                    reply.message += "\nNext: type next to prepare amino-acid omnibus testing (HATK Node4) using this phased AA result. A matching phenotype is required."
                elif example in ('hatk_node2', 'hatk_node2_custom'):
                    self.next_run = ('node3', plan['run_dir'])
                    reply.message += '\nNext: type next to reuse the checked panel and phenotype for one conditional scan. You must specify condition marker IDs.'
            if self.workflow_goal is not None and isinstance(reply.result, dict):
                example = reply.result.get('plan', {}).get('example', '')
                if example in ('hatk_' + self.workflow_goal, 'hatk_' + self.workflow_goal + '_custom'):
                    reply.message += '\nSelected workflow goal reached. Use report to collect the saved results.'
                    self.next_run = None
        if reply.return_to_supervisor:
            self.active_agent = None
            self.prepared_phenotype_run = None
        if reply.end_session:
            self.end_session()
        if reply.plan is not None:
            self.record_checkpoint(reply=reply)
        elif reply.result is not None:
            result = reply.result
            status = result.get('status') if isinstance(result, dict) else result.status
            run = result.get('plan', {}).get('run_dir') if isinstance(result, dict) else result.run_dir
            self.record_checkpoint(reply=reply, status=status, result_run=run)
            self.checkpoint_path = self.checkpoint_plan = None
        elif self.checkpoint_path is not None and self.pending_plan is None and not reply.end_session:
            self.record_checkpoint(reply=reply, status='discarded')
            self.checkpoint_path = self.checkpoint_plan = None
        return reply

    def record_checkpoint(self, reply=None, status='waiting_for_approval', result_run=None):
        if self.checkpoint_root is None:
            return
        try:
            if reply is not None and reply.plan is not None:
                if self.checkpoint_path is not None:
                    save_checkpoint(self.checkpoint_root, self.checkpoint_plan, self.checkpoint_path, 'superseded',
                                    workflow_goal=self.workflow_goal)
                self.checkpoint_path = None
                self.checkpoint_plan = reply.plan
            if self.checkpoint_plan is not None:
                self.checkpoint_path = save_checkpoint(self.checkpoint_root, self.checkpoint_plan,
                                                       self.checkpoint_path, status, result_run, self.workflow_goal)
        except (OSError, ValueError) as error:
            if reply is None:
                raise OSError('Could not checkpoint the plan before execution. No new execution was started.') from error
            reply.message += '\nCheckpoint save failed. Keep the run record path. Session recovery may be unavailable.'

    def choose_recovery(self):
        self.discard_plan()
        self.next_run = None
        self.choosing_next = False
        candidates = recovery_candidates(self.finemapping.run_root, self.checkpoint_root)
        self.recovery_paths = [path for path, _ in candidates]
        lines = ['Select a saved run or waiting plan. Files and settings will be rechecked.',
                 'Completed inputs can continue. Failed/cancelled or waiting plans require a new plan and approval.', '']
        lines.append(saved_run_listing(self.recovery_paths, show_status=True))
        if not candidates:
            lines.append('No saved runs found. You may enter a run directory or checkpoint JSON path.')
        return ChatReply('\n'.join(lines), actions=('details: timestamps, run IDs and paths', 'back / cancel: return to Supervisor'))

    def choose_workflow(self):
        self.discard_plan()
        self.next_run = None
        self.choosing_next = False
        self.workflow_goal = None
        self.workflow_field = 'goal'
        return ChatReply('Choose an analysis goal:\n\n'
                         '1. Marker association (HATK Node2)\n'
                         '2. Conditional association (HATK Node3), one scan with explicit markers\n'
                         '3. Amino-acid omnibus test (HATK Node4)\n'
                         '4. Meta-analysis (HATK Node5), two-study fixed-effect model\n\n'
                         'Next choose the inputs you already have. Only missing preparation steps will be offered.\n'
                         'Each step requires its own checked plan and approval. Type next after completion.',
                         actions=('back / cancel: return to Supervisor',))

    def collect_workflow(self, text):
        if self.workflow_field == 'goal':
            goal = {'1': 'node2', '2': 'node3', '3': 'node4', '4': 'node5'}.get(text, text)
            if goal not in ('node2', 'node3', 'node4', 'node5'):
                return ChatReply('Choose node2/node3/node4/node5 or 1/2/3/4.')
            self.workflow_goal = goal
            if goal == 'node5':
                self.workflow_field = None
                return self.handoff('node5-custom', 'finemapping')
            self.workflow_field = 'start'
            return ChatReply('Choose the input stage you already have:\n\n'
                             'cookhla: start with genotype imputation\n'
                             'hped: start with HLA calls and prepared dictionaries\n'
                             'panel: start with an unphased HATK marker panel\n' +
                             ('phased: start with phased AA BGL/FAM. Skip phasing\n' if goal == 'node4' else '') +
                             '\nThe phenotype and any condition markers will be requested when needed.\n'
                             'Declared builds and reference/dictionary suitability must be checked locally.')
        goal = self.workflow_goal
        if text not in ('cookhla', 'hped', 'panel') and not (text == 'phased' and goal == 'node4'):
            return ChatReply('Choose a starting input stage from the list, or type back to return.')
        self.workflow_field = None
        if text == 'cookhla':
            reply = self.respond('imputation')
            self.workflow_goal = goal
            route = 'CookHLA → HPED → ' + stage_title('hatk_node1') + ' → ' + ('Phasing → ' if goal == 'node4' else '') + stage_title('hatk_' + goal)
        elif text == 'hped':
            reply = self.handoff('node1-custom', 'finemapping')
            route = stage_title('hatk_node1') + ' → ' + ('Phasing → ' if goal == 'node4' else '') + stage_title('hatk_' + goal)
        elif text == 'panel' and goal == 'node4':
            reply = self.handoff('panel', 'phasing')
            route = 'Phasing → ' + stage_title('hatk_node4')
        else:
            reply = self.handoff(goal + '-custom', 'finemapping')
            route = stage_title('hatk_' + goal)
        reply.message = 'Selected route: ' + route + '\nUse next after each completed step. Each step requires new approval.\n\n' + reply.message
        return reply

    def choose_report(self, manual=False):
        from hlagent.runtime.report_workflow import suggest_report_runs, report_record_hashes
        self.report_proposed = False
        self.report_hashes = {}
        warning = ''
        if not manual:
            try:
                proposed = suggest_report_runs(self.finemapping.run_root, self.report_anchor)
            except (OSError, ValueError, KeyError, TypeError) as error:
                proposed = []
                warning = 'Could not recheck the suggested runs: ' + str(error) + '\n\n'
            if proposed:
                self.report_paths = proposed
                self.report_hashes = report_record_hashes(proposed)
                self.report_proposed = True
                lines = ['Create a report from these analysis steps?', '']
                lines += [saved_run_listing([run / 'run.json' for run in proposed]), '']
                lines.append('Enter y to include all, or numbers to choose specific steps.')
                return ChatReply('\n'.join(lines), actions=('details: timestamps, run IDs and paths', 'files: choose other saved runs',
                                                           'back / cancel: return to Supervisor'))
        candidates = [(path, record) for path, record in recovery_candidates(self.finemapping.run_root)
                      if path.name == 'run.json' and record.get('status') != 'native_validation']
        self.report_paths = [path.parent for path, _ in candidates]
        lines = [warning + 'Select saved runs for an offline HTML report.',
                 'Enter numbers separated by spaces, or one run directory. No analysis or LLM call is made.', '']
        lines.append(saved_run_listing([run / 'run.json' for run in self.report_paths], show_status=True))
        if not candidates:
            lines.append('No saved runs found. You may enter a run directory.')
        return ChatReply('\n'.join(lines), actions=('details: timestamps, run IDs and paths', 'back / cancel: return to Supervisor'))

    def selection_details(self):
        if self.pending_plan is not None:
            return None
        if self.report_paths is not None:
            return saved_run_listing([run / 'run.json' for run in self.report_paths], details=True, show_status=True)
        if self.recovery_paths is not None:
            return saved_run_listing(self.recovery_paths, details=True, show_status=True)
        agent = self.agents.get(self.active_agent)
        if agent is None:
            return None
        field = getattr(agent, 'input_field', None)
        if field in ('run', 'pheno_node1', 'parent_run', 'marker_conversion_run', 'association_node1_run',
                     'omnibus_phasing_run', 'meta_summary_run', 'association_prepared_run', 'omnibus_prepared_run'):
            return saved_run_listing([agent.run_root / name / 'run.json' for name in agent.run_names], details=True)
        if field in ('association_pheno', 'omnibus_pheno'):
            from hlagent.runtime.phenotype_selection import candidate_listing
            return candidate_listing(agent.phenotype_files, details=True)
        if field == 'marker_dictionary':
            from hlagent.runtime.recorded_input_selection import recorded_listing
            return recorded_listing(agent.dictionary_candidates, 'dictionary', details=True)
        if field == 'pheno_source':
            from hlagent.runtime.recorded_input_selection import recorded_listing
            return recorded_listing(agent.source_candidates, 'clinical source', details=True)
        return None

    def report_selection(self, text):
        from hlagent.runtime.html_report import generate_report, report_directory
        from hlagent.runtime.report_workflow import report_record_hashes
        lowered = text.strip().lower()
        if lowered in ('files', 'manual'):
            return self.choose_report(manual=True)
        if lowered in ('n', 'no'):
            self.report_paths = None
            self.report_proposed = False
            self.report_hashes = {}
            return ChatReply('Report cancelled. Returned to Supervisor.')
        values = text.replace(',', ' ').split()
        if lowered in ('y', 'yes') and self.report_proposed:
            runs = self.report_paths
        elif values and all(value.isdigit() for value in values):
            indices = [int(value) - 1 for value in values]
            if any(not 0 <= i < len(self.report_paths) for i in indices):
                return ChatReply('Choose listed numbers or enter one run directory.')
            runs = [self.report_paths[i] for i in indices]
        elif text and Path(text).expanduser().is_dir():
            runs = [Path(text).expanduser().resolve()]
            if str(runs[0]) not in self.report_hashes:
                self.report_proposed = False
                self.report_hashes = {}
        else:
            return ChatReply('Enter y to use the suggested steps, numbers to select, or files for other runs.'
                             if self.report_proposed else 'Choose listed numbers or enter one run directory.')
        if self.report_proposed:
            current = report_record_hashes(runs)
            if any(self.report_hashes.get(path) != value for path, value in current.items()):
                self.report_paths = None
                self.report_proposed = False
                self.report_hashes = {}
                return ChatReply('A selected run record changed. Type report to review the selection again.')
        report = generate_report(runs, report_directory(self.finemapping.run_root))
        self.report_paths = None
        self.report_proposed = False
        self.report_hashes = {}
        return ChatReply('HTML report: ' + str(report) + '\n'
                         'Review each task status and saved-result checks.')

    def resume_selection(self, text):
        if text.isdigit():
            index = int(text) - 1
            if not 0 <= index < len(self.recovery_paths):
                return ChatReply('Choose a listed number or a run/checkpoint path.')
            path = self.recovery_paths[index]
        else:
            path = text
        run, record = recovery_record(path)
        self.workflow_goal = record.get('workflow_goal') if record.get('workflow_goal') in ('node2', 'node3', 'node4', 'node5') else None
        status = record.get('status')
        if status in ('running', 'cancellation_unconfirmed'):
            return ChatReply('The saved run may still be active. Check its log/container before retrying. '
                             'No container was stopped and no replacement run was started.')
        if status == 'completed':
            target = continuation(record)
            if (self.workflow_goal is not None and record['plan'].get('example') in
                    ('hatk_' + self.workflow_goal, 'hatk_' + self.workflow_goal + '_custom')):
                target = None
            self.recovery_paths = None
            if target is None:
                return ChatReply(f'Recorded task is completed: {run.name}. No automatic follow-up is selected.\n'
                                 'Use report to inspect saved results, or choose another task.')
            self.next_run = (target, str(run))
            return self.continue_analysis()
        if status not in ('failed', 'cancelled', 'waiting_for_approval', 'execution_requested'):
            return ChatReply('This record cannot be resumed automatically. Select inputs in the normal task menu.')
        try:
            plan = rebuild_plan(record['plan'], self.finemapping.run_root, self.finemapping.seccomp_unconfined)
        except (KeyError, TypeError) as error:
            raise ValueError('The saved plan is incomplete or incompatible. Select inputs in the normal task menu.') from error
        self.recovery_paths = None
        self.report_paths = None
        install_plan(self, plan)
        reply = ChatReply('Recovered explicit inputs/settings using current code. The original run is preserved.\n\n'
                          'Review this new plan. No saved approval is restored. Enter y to start or cancel to discard.', plan=plan)
        self.record_checkpoint(reply=reply)
        return reply

    def handoff(self, text, target="imputation", task_request=False):
        self.discard_plan()
        self.next_run = None
        self.choosing_next = False
        self.prepared_phenotype_run = None
        self.recovery_paths = None
        self.clarification_pending = False
        self.active_agent = target
        self.last_agent = target
        if task_request and target in ("conversion", "phasing"):
            reply = self.agents[target].start_task(text)
        else:
            reply = self.forward(text)
        label = {"imputation": "HLA Imputation", "finemapping": "HLA Fine-mapping",
                 "conversion": "File Converter", "phasing": "Genotype Phasing",
                 "tutor": "HLA Tutor"}[target]
        reply.message = f"Handoff to {label}.\n" + reply.message
        reply.handoff = label
        return reply

    def continue_analysis(self, choice=None):
        if self.next_run is None:
            return ChatReply("No next input is selected. Choose an analysis or select a recorded run in its input menu.")
        target, run = self.next_run
        if target == 'phenotype':
            if choice is None:
                self.choosing_next = True
                return ChatReply('Choose an analysis using the prepared phenotype:\n\n'
                                 '1. Marker association (HATK Node2)\n'
                                 '2. Conditional association (HATK Node3), one scan\n'
                                 '3. Amino-acid omnibus test (HATK Node4)\n\n'
                                 'Supply the matching marker panel or phased AA inputs next.\n'
                                 'Select: enter node2/node3/node4 or 1/2/3.',
                                 actions=('back / cancel: discard this continuation',))
            choice = {'1': 'node2', '2': 'node3', '3': 'node4'}.get(choice, choice)
            if choice not in ('node2', 'node3', 'node4'):
                return ChatReply('Choose node2/node3/node4 or 1/2/3, or type back to return.')
            from hlagent.runtime.phenotype_preparation import prepared_phenotype
            prepared_phenotype(run)
            reply = self.handoff(choice + '-custom', 'finemapping')
            self.prepared_phenotype_run = run
            reply.message = 'The prepared phenotype will be rechecked after you select matching analysis inputs.\n\n' + reply.message
            return reply
        if target == 'summary':
            self.handoff('node5-custom', 'finemapping')
            self.forward('run')
            return self.forward(run)
        if target == 'node3':
            inputs = association_inputs(run)
            self.handoff('node3-custom', 'finemapping')
            self.finemapping.association_inputs = inputs
            self.finemapping.conditional_source_run = run
            self.finemapping.input_field = 'association_conditions'
            return ChatReply('Rechecked the completed Node2 panel and phenotype.\n'
                             f"Trait: {inputs['phenotype_name']} | Genome build: hg{inputs['genome_build']} (recorded)\n\n"
                             'Enter exact condition marker IDs separated by spaces. No marker is selected automatically.\n'
                             'A fresh matched baseline and one conditional scan require a new plan and approval.')
        if target == "node1":
            if choice is None and self.workflow_goal in ('node2', 'node3', 'node4'):
                choice = 'phasing' if self.workflow_goal == 'node4' else self.workflow_goal
            if choice is None:
                self.choosing_next = True
                return ChatReply("Choose the next task using the HLA marker panel (HATK Node1):\n\n"
                                 "1. Marker association (HATK Node2)\n"
                                 "   Supply a matching named binary phenotype. Phasing is not required.\n\n"
                                 "2. Genotype phasing: prepare markers for amino-acid omnibus testing (HATK Node4)\n"
                                 "   Omnibus testing remains a separate analysis with a matching phenotype.\n\n"
                                 "3. Conditional association (HATK Node3), one scan with a fresh baseline\n"
                                 "   Supply a phenotype and explicit condition marker IDs.\n\n"
                                 "Select: enter node2/node3/phasing or 1/2/3. No analysis starts automatically.",
                                 actions=("back / cancel: discard this continuation",))
            choice = {"1": "node2", "2": "phasing", "3": "node3"}.get(choice, choice)
            if choice not in ("node2", "node3", "phasing"):
                return ChatReply("Choose node2/node3/phasing or 1/2/3, or type back to return. No analysis was started.")
            if choice == "phasing":
                self.handoff("run", "phasing")
            else:
                self.handoff(choice + '-custom', "finemapping")
                self.forward("run")
        elif target == "node4":
            self.handoff("node4-custom", "finemapping")
            self.forward("run")
        elif target == "conversion":
            self.handoff("run", target)
        else:
            self.handoff("node1-custom", target)
            self.forward("run")
        reply = self.forward(run)
        reply.message = "Checking the previous result for reuse.\n" + reply.message
        return reply

    def handoff_task(self, action, request):
        """Pass a classified operation unchanged into its existing local form."""
        role, command = TASK_ROUTES[action]
        reply = self.respond('imputation') if role == 'imputation' else self.handoff(command, role)
        agent = self.agents[role]
        if action in ('node2_inputs', 'node3_inputs') and re.search(
                r'\bcovariates?\b|\bage\b|\bsex\b|\bancestry\b|principal components|\bPCs?\b|공변량|나이|성별|주성분', request, re.I):
            agent.covariates_requested = True
        agent.history = (agent.history + [{'role': 'user', 'content': request},
                                         {'role': 'assistant', 'content': reply.message}])[-12:]
        return reply

    def respond(self, text):
        text = text.strip()
        lowered = text.lower()
        self.last_routing_source = None
        if self.closed or lowered in EXIT_WORDS:
            return self.end_session()
        if lowered == 'details':
            details = self.selection_details()
            if details is not None:
                return ChatReply(details, actions=('Continue at the current selection prompt.',))
        if self.active_agent is not None:
            return self.forward(text)
        if lowered in CANCEL_WORDS or lowered in BACK_WORDS:
            self.discard_plan()
            self.next_run = None
            self.choosing_next = False
            self.prepared_phenotype_run = None
            self.recovery_paths = None
            self.report_paths = None
            self.report_proposed = False
            self.report_hashes = {}
            self.workflow_goal = self.workflow_field = None
            self.clarification_pending = False
            return ChatReply("You are with Supervisor. There is no pending analysis plan.")
        if lowered.split(' ', 1)[0] in ('workflow-order', 'execute-order'):
            return ChatReply('Choose workflow to select an analysis goal and the inputs you already have.')
        if self.workflow_field is not None:
            return self.collect_workflow(lowered)
        if self.recovery_paths is not None:
            return self.resume_selection(text)
        if self.report_paths is not None:
            return self.report_selection(text)
        if self.choosing_next:
            return self.continue_analysis(lowered)
        if lowered == "next":
            return self.continue_analysis()
        if lowered == 'resume':
            return self.choose_recovery()
        if lowered in ('report', 'report files'):
            return self.choose_report(manual=lowered == 'report files')
        if lowered == 'workflow':
            return self.choose_workflow()
        self.workflow_goal = None
        if lowered == "prepare-summary":
            return self.handoff('summary', 'conversion')
        if lowered == "prepare-phenotype":
            return self.handoff('phenotype', 'conversion')
        if lowered in ("y", "yes", "n", "no"):
            return ChatReply("There is no plan to approve. Describe an analysis task first.")
        if not text:
            return ChatReply("Describe a task, type imputation, hatk, convert, phasing, or tutor, or exit to leave.")
        if model_recommendation_question(text):
            self.last_routing_source = 'model_advice'
            return ChatReply('HLAgent supports Ollama and OpenAI, Anthropic or Gemini APIs.\n\n'
                             '- Ollama: local LLM inference. Speed and answer quality depend on your hardware and model.\n'
                             '- Cloud APIs: consider a capable model when natural-language understanding and answer quality are priorities. '
                             'Requests go to the provider and may incur charges.\n\n'
                             'Type config to change your model.')
        if current_model_question(text):
            provider = getattr(self.client, 'provider', 'ollama')
            self.last_routing_source = 'session_model'
            return ChatReply(f'Current provider: {provider}\nCurrent model: {self.client.model}')
        if lowered in ("config", "/config", "configure", "configuration", "settings"):
            self.discard_plan()
            self.clarification_pending = False
            return ChatReply(CONFIGURATION_HELP, configure=True)
        if lowered in ("tutor", "/tutor", "hlatutor", "hla-tutor",
                       "teacher", "/teacher", "hlateacher", "hla-teacher"):
            return self.handoff("tutor", "tutor")
        if lowered in ("example", "custom", "saved"):
            return self.handoff(lowered)
        if lowered in ("hatk", "/hatk", "finemapping", "fine-mapping", "node1", "node2", "node3", "node4", "omnibus", "node5", "meta", "stepwise", "prepare-dictionary", "node2-custom", "node3-custom", "node1-custom", "node4-custom", "node5-custom"):
            return self.handoff(lowered.lstrip("/"), "finemapping")
        if lowered in ("convert", "/convert", "converter", "fileconverter", "file-converter"):
            return self.handoff("convert", "conversion")
        if lowered in ("phase", "phasing", "/phasing", "genotypephasing", "genotype-phasing"):
            return self.handoff("phasing", "phasing")
        if lowered in ("imputation", "/imputation"):
            self.discard_plan()
            self.next_run = None
            self.clarification_pending = False
            self.active_agent = "imputation"
            self.last_agent = "imputation"
            self.imputation.input_field = "dataset"
            return ChatReply("Handoff to HLA Imputation.\n\n"
                             "Choose input data:\n"
                             "  example: bundled demonstration\n"
                             "  custom: supplied PLINK files\n"
                             "  saved: registered input combinations\n\n"
                             "Select: enter example, custom, or saved.\n", handoff="HLA Imputation")
        local_task = explicit_task(text)
        if local_task is not None:
            reply = self.handoff_task(local_task, text)
            self.last_routing_source = 'local_explicit_task'
            self.last_routing_decision = {'action': local_task, 'request_kind': 'single_task',
                                          'clarification_required': False, 'message': ''}
            self.history = (self.history + [{'role': 'user', 'content': text},
                            {'role': 'assistant', 'content': json.dumps(self.last_routing_decision)}])[-12:]
            return reply
        state = {"current_provider": getattr(self.client, "provider", "ollama") if isinstance(getattr(self.client, "provider", "ollama"), str) else None,
                 "current_model": self.client.model if isinstance(self.client.model, str) else None,
                 "available_agents": list(self.agents), "last_agent": self.last_agent,
                 "clarification_pending": self.clarification_pending,
                 "last_result": result_context(self.imputation.last_result),
                 "last_finemapping_result": result_context(self.finemapping.last_result),
                 "last_conversion_result": result_context(self.conversion.last_result),
                 "last_phasing_result": result_context(self.phasing.last_result),
                 "last_data_source": self.imputation.mode}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": "Current state: " + json.dumps(state)}]
        messages += self.history[-12:] + [{"role": "user", "content": text}]
        decision = self.client.decide(messages, DECISION_SCHEMA)
        validate_routing_decision(decision)
        if (decision['request_kind'] == 'configuration'
                and command_guidance(text) is not None):
            decision = dict(decision, request_kind='question', action='answer',
                            clarification_required=False, message=command_guidance(text))
        question_redirected = False
        if decision['request_kind'] == 'question' and decision['action'] in ('answer', 'tutor'):
            destination = question_destination(text)
            if destination is not None and destination != decision['action']:
                question_redirected = True
                decision = dict(decision, action=destination, clarification_required=False,
                                message='' if destination == 'tutor' else
                                'Type help for commands, workflow to choose an analysis goal, or config for settings.\n'
                                'Use back to return to Supervisor from a role.')
        command_corrected = False
        if decision['request_kind'] == 'question' and decision['action'] == 'answer':
            guidance = command_guidance(text)
            if guidance is not None:
                decision = dict(decision, message=guidance)
                command_corrected = True
        if decision['request_kind'] == 'configuration':
            self.discard_plan()
            self.clarification_pending = False
            decision = dict(decision, action='answer', clarification_required=False, message=CONFIGURATION_HELP)
        elif decision['clarification_required'] or decision['request_kind'] == 'unordered_tasks':
            self.discard_plan()
            self.clarification_pending = True
            decision = dict(decision, action='answer', clarification_required=True,
                            message='Which operation would you like to start with?\n\n'
                                    'Choose HLA imputation, marker generation, association, conditional association, '
                                    'phasing, amino-acid omnibus, meta-analysis, or input preparation.\n'
                                    'You can describe the goal, or type workflow for a guided choice.')
        self.last_routing_decision = decision
        self.history.extend([{"role": "user", "content": text},
                             {"role": "assistant", "content": json.dumps(decision)}])
        self.history = self.history[-12:]
        if decision['action'] in TASK_ROUTES:
            reply = self.handoff_task(decision['action'], text)
            self.last_routing_source = 'model_task'
            return reply
        self.last_routing_source = ('command_guidance' if command_corrected else
                                    'question_policy' if question_redirected else 'model_role')
        if decision["action"] in self.agents:
            return self.handoff(text, decision["action"],
                                task_request=decision["request_kind"] in ("single_task", "ordered_tasks"))
        if decision["action"] == "exit":
            return self.end_session()
        return ChatReply(decision["message"], configure=decision["request_kind"] == "configuration")
