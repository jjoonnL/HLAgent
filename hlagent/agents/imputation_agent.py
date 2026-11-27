"""HLA imputation dialogue and input collection, independent of terminal I/O."""

import json
from pathlib import Path

from hlagent.agents.prompt_rules import COMMON_RULES
from hlagent.runtime.chat_types import BACK_WORDS, CANCEL_WORDS, EXIT_WORDS, ChatReply, result_context, validate_decision
from hlagent.analysis.cookhla import check_resources, prepare_custom, prepare_example, run_analysis, run_example
from hlagent.analysis.cookhla_inputs import REFERENCE_SUFFIXES, check_plink, require_file
from hlagent.runtime.datasets import REGISTRY, list_datasets, prepare_registered


SYSTEM_PROMPT = COMMON_RULES + """You are HLAgent's HLA Imputation agent. You handle CookHLA input collection,
planning, setting changes, and result questions. A separate Supervisor routes
requests to you. CookHLA supports the bundled example or user-supplied PLINK
input, an hg18 SNP2HLA-style reference, and supplied or newly calculated AGM.
The program collects and checks custom file paths; never extract or invent paths.
Registered input combinations can be selected locally with the exact command saved.
For a request to select registered inputs, use choose_data; never invent a dataset name.
HATK is handled by a separate Fine-mapping agent: ask the user to type back and
request HATK at Supervisor. Other typing tools, web search, and arbitrary commands
are unavailable here.

Return one JSON object with action, memory, processes, and message.
Actions:
- example: explicitly switch to the bundled example and prepare its plan.
- custom: explicitly request user-supplied data; begin the file input questions.
- choose_data: request CookHLA analysis without specifying example or custom data.
- plan: prepare or retry a plan for the current selected data source.
- back: return to Supervisor without ending this chat.
- update: the user wants to change memory or process count for the selected data source.
- answer: respond to greetings, explain the example or settings, or discuss an
  actual recorded result. Ask for clarification when the intended task is unclear.
- cancel: discard the pending plan.
- exit: the user directly wants to end this chat or tells the assistant to leave.
- unsupported: the request requires a capability not available here.

Control intent rules:
- Distinguish ending the chat (exit) from discarding an analysis plan (cancel).
- Interpret the user's intent rather than requiring exact command words.
  Classify a direct request to end the conversation as exit. Distinguish this
  from cancelling an analysis, asking how to exit, quoting an exit request,
  or asking not to exit. Questions, quotations, and negated requests are answer,
  not exit. The mere mention of quitting does not request it.
- A request to cancel this analysis but continue chatting means cancel.
- A request to return to the Supervisor means back.
- If 'stop' is ambiguous, ask what to stop instead of assuming exit.

Extract only explicitly requested setting changes. Use null for unchanged values.
For example, 'use 4 GB and 2 processes' gives memory '4g', processes 2. Never
guess settings, paths, commands, or security options. Never approve or execute
an analysis. Even 'run now' only prepares a plan; the program requests approval.
Use the supplied state and recent conversation for follow-up requests. Do not
claim a run happened, completed, or failed without a recorded result.
Raw error details are not supplied to the model; do not invent a failure cause.
The user can inspect the terminal error and log for details. Completion
and sample/gene coverage checks are not proof of prediction accuracy.
Conversation style:
- Use the language of the latest user message, even though these instructions
  and the JSON keys are in English. For Korean messages, write Korean prose;
  retain tool names and technical identifiers in their original form.
- A greeting or attention-getting message is not an analysis request. Reply with
  a short greeting or offer of help. Do not launch into a CookHLA explanation.
- Start directly with the reply. Do not add unrelated words, slogans, or invented
  variants of tool names. Keep replies to one or two short sentences unless the
  user asks for more detail.
- If last_result is null, no result is available in this session. Do not mention
  a previous analysis as if it happened or offer to explain a recorded result.

For answer/unsupported/cancel/exit/back/choose_data, memory and processes must be null.
Return no text outside the JSON object.
"""

DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["plan", "example", "custom", "choose_data", "update", "answer", "cancel", "back", "exit", "unsupported"]},
        "memory": {"type": ["string", "null"]},
        "processes": {"type": ["integer", "null"]},
        "message": {"type": "string"},
    },
    "required": ["action", "memory", "processes", "message"],
}


class ImputationAgent:
    def __init__(self, client, seccomp_unconfined=False, registry=REGISTRY):
        self.client = client
        self.seccomp_unconfined = seccomp_unconfined
        self.memory = "16g"
        self.processes = 6
        self.pending_plan = None
        self.last_result = None
        self.history = []
        self.closed = False
        self.mode = None
        self.input_field = None
        self.custom_inputs = {}
        self.registry = registry
        self.dataset_name = None
        self.saved_names = []

    @property
    def input_prompt(self):
        return {
            "dataset": "Data source [example/custom/saved]> ",
            "saved_dataset": "Registered dataset name or number> ",
            "input_prefix": "Target PLINK prefix> ",
            "reference_prefix": "Reference panel prefix> ",
            "genome_build": "Target genome build [18/19/38]> ",
            "agm_choice": "Use existing AGM files? [y/n]> ",
            "genetic_map": "Genetic map path> ",
            "error_rate": "Error-rate path> ",
        }.get(self.input_field, "HLA Imputation> ")

    def discard_plan(self):
        self.pending_plan = None

    def leave(self):
        self.pending_plan = None
        self.input_field = None
        return ChatReply("Returned to Supervisor. No analysis was started.", return_to_supervisor=True)

    def end_session(self):
        self.pending_plan = None
        self.input_field = None
        self.closed = True
        return ChatReply("Session ended.", end_session=True)

    def start_custom(self):
        self.pending_plan = None
        self.mode = "custom"
        self.custom_inputs = {}
        self.dataset_name = None
        self.input_field = "input_prefix"
        return ChatReply("Input: enter the target PLINK prefix without .bed/.bim/.fam.\n"
                         "Paths must exist on this machine. These input fields are checked locally.\n\n")

    def choose_saved(self):
        self.pending_plan = None
        self.saved_names = list_datasets(self.registry)
        if not self.saved_names:
            self.input_field = "dataset"
            return ChatReply("No datasets registered. Register inputs with datasets.py first, or enter example/custom.")
        self.input_field = "saved_dataset"
        listing = "\n".join(f"{number}. {name}" for number, name in enumerate(self.saved_names, 1))
        return ChatReply("Registered input combinations:\n" + listing +
                         "\nEnter a name or number. Files will be rechecked before a plan is shown.")

    def collect_input(self, text):
        field = self.input_field
        if field == "saved_dataset":
            try:
                name = text
                if text not in self.saved_names and text.isdigit() and 1 <= int(text) <= len(self.saved_names):
                    name = self.saved_names[int(text) - 1]
                if name not in self.saved_names:
                    raise ValueError("Choose a listed name or number.")
                plan = prepare_registered(name, self.memory, self.processes,
                                          self.seccomp_unconfined, self.registry)
                self.mode = "saved"
                self.dataset_name = name
                self.input_field = None
                self.pending_plan = plan
                return self.review_plan(plan)
            except (OSError, ValueError, RuntimeError) as error:
                return ChatReply(f"Dataset check failed: {error}\nChoose another entry or type back.")
        if field == "dataset":
            if text.lower() in ("example", "예시"):
                self.mode = "example"
                self.input_field = None
                return self.prepare_plan()
            if text.lower() in ("custom", "사용자 데이터"):
                return self.start_custom()
            if text.lower() == "saved":
                return self.choose_saved()
            return ChatReply("Enter example, custom, or saved, or type back to return to Supervisor.")
        if not text:
            return ChatReply("A value is required. Type cancel, back, or exit to leave.")
        try:
            if field in ("input_prefix", "reference_prefix"):
                checked = check_plink(text)
                if field == "reference_prefix":
                    for suffix in REFERENCE_SUFFIXES[3:]:
                        require_file(checked["prefix"] + suffix)
                    if not checked["hla_marker_genes"]:
                        raise ValueError("The reference BIM contains no HLA allele markers.")
                self.custom_inputs[field] = checked["prefix"]
                self.input_field = "reference_prefix" if field == "input_prefix" else "genome_build"
                return ChatReply(f"Input checked: {checked['samples']} samples.\n\n" +
                                 ("Enter the reference panel prefix." if field == "input_prefix" else
                                  "Enter the target genome build: 18, 19, or 38. The reference is assumed hg18."))
            if field == "genome_build":
                if text not in ("18", "19", "38"):
                    raise ValueError("Enter 18, 19, or 38.")
                self.custom_inputs[field] = int(text)
                self.input_field = "agm_choice"
                return ChatReply("Use an existing AGM map/error-rate pair generated for this target/reference?\n\n"
                                 "y: supply existing map and error-rate files\n"
                                 "n: calculate a new AGM during analysis")
            if field == "agm_choice":
                if text.lower() in ("y", "yes"):
                    self.input_field = "genetic_map"
                    return ChatReply("Enter the existing genetic map file path.")
                if text.lower() not in ("n", "no"):
                    raise ValueError("Enter y or n for the AGM choice.")
                self.custom_inputs.update(genetic_map=None, error_rate=None)
            elif field == "genetic_map":
                self.custom_inputs[field] = str(require_file(Path(text).expanduser().resolve()))
                self.input_field = "error_rate"
                return ChatReply("Enter the matching error-rate file path.")
            elif field == "error_rate":
                self.custom_inputs[field] = str(require_file(Path(text).expanduser().resolve()))
            # y/n during collection never approves an analysis.
            reply = self.prepare_plan()
            self.input_field = None
            return reply
        except (OSError, ValueError, RuntimeError) as error:
            return ChatReply(f"Input check failed: {error}\nEnter a corrected value, or type cancel/back.")

    def prepare_plan(self):
        if self.mode is None:
            self.input_field = "dataset"
            return ChatReply("Use the bundled example, custom paths, or registered inputs? Enter example, custom, or saved.")
        if self.mode == "saved":
            plan = prepare_registered(self.dataset_name, self.memory, self.processes,
                                      self.seccomp_unconfined, self.registry)
        elif self.mode == "custom":
            required = {"input_prefix", "reference_prefix", "genome_build", "genetic_map", "error_rate"}
            if not required.issubset(self.custom_inputs):
                return self.start_custom()
            plan = prepare_custom(**self.custom_inputs, memory=self.memory, processes=self.processes,
                                  seccomp_unconfined=self.seccomp_unconfined)
        else:
            plan = prepare_example(self.memory, self.processes, seccomp_unconfined=self.seccomp_unconfined)
        self.pending_plan = plan
        return self.review_plan(plan)

    def review_plan(self, plan):
        return ChatReply("Status: waiting for approval. Analysis has not started.\n\n"
                         "Review the inputs and settings below. Enter y to start or n to discard.",
                         plan=plan, actions=('To change settings, describe the change before approving.',))

    def respond(self, text):
        text = text.strip()
        lowered = text.lower()
        if self.closed or lowered in EXIT_WORDS:
            return self.end_session()
        if lowered in CANCEL_WORDS or lowered in BACK_WORDS:
            return self.leave()
        if self.input_field is not None:
            return self.collect_input(text)
        if lowered in ("n", "no"):
            return self.leave()
        if lowered in ("y", "yes"):
            if self.pending_plan is None:
                return ChatReply("There is no plan to approve. Ask for a plan first.")
            plan = self.pending_plan
            self.pending_plan = None
            runner = run_example if plan.inputs is None else run_analysis
            progress = getattr(self, "execution_progress", None)
            self.last_result = runner(plan, progress=progress) if progress else runner(plan)
            return ChatReply("Returned to Supervisor.", result=self.last_result, return_to_supervisor=True)
        if not text:
            return ChatReply("Describe your CookHLA request, or type back to return to Supervisor.")
        if lowered == "custom":
            return self.start_custom()
        if lowered == "saved":
            return self.choose_saved()
        if lowered == "example":
            self.mode = "example"
            self.pending_plan = None
            return self.prepare_plan()
        state = {"memory": self.memory, "processes": self.processes, "data_source": self.mode,
                 "has_pending_plan": self.pending_plan is not None,
                 "custom_inputs_ready": self.mode == "custom" and self.input_field is None and bool(self.custom_inputs),
                 "last_result": result_context(self.last_result)}
        self.pending_plan = None
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": "Current state: " + json.dumps(state)}]
        messages += self.history[-12:] + [{"role": "user", "content": text}]
        decision = self.client.decide(messages, DECISION_SCHEMA)
        validate_decision(decision, DECISION_SCHEMA)
        action = decision["action"]
        if action not in ("plan", "example", "custom", "update"):
            if decision["memory"] is not None or decision["processes"] is not None:
                raise ValueError("The model proposed settings outside a planning request.")
        self.history.extend([{"role": "user", "content": text},
                             {"role": "assistant", "content": json.dumps(decision)}])
        self.history = self.history[-12:]
        if action in ("plan", "example", "custom", "update"):
            memory = self.memory if decision["memory"] is None else decision["memory"]
            processes = self.processes if decision["processes"] is None else decision["processes"]
            check_resources(memory, processes)
            if action == "example":
                self.mode = "example"
            self.memory, self.processes = memory, processes
            if action == "custom":
                return self.start_custom()
            return self.prepare_plan()
        if action == "choose_data":
            self.input_field = "dataset"
            return ChatReply("Enter example, custom, or saved to select your input data.")
        if action in ("cancel", "back"):
            return self.leave()
        if action == "exit":
            return self.end_session()
        return ChatReply(decision["message"])
