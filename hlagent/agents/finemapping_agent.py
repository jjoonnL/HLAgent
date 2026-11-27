"""Plan reviewed HATK analyses through a dedicated dialogue role."""

import json
from pathlib import Path
import subprocess

from hlagent.agents.prompt_rules import COMMON_RULES
from hlagent.runtime.analysis_run_selection import completed_runs, selected_run, run_listing
from hlagent.runtime.run_visibility import is_archived
from hlagent.analysis.cookhla_to_hped import conversion_hped
from hlagent.analysis.hatk_phasing import completed_phasing_inputs
from hlagent.cli.hatk_phasing_run import node1_panel
from hlagent.analysis.cookhla_inputs import require_file
from hlagent.runtime.phenotype_inputs import PHENOTYPE_BRIEF, PHENOTYPE_HELP, phenotype_columns, review_phenotype
from hlagent.runtime.summary_preparation import prepared_summary
from hlagent.runtime.phenotype_preparation import prepared_phenotype
from hlagent.runtime.phenotype_selection import phenotype_candidates, candidate_listing, select_candidate
from hlagent.runtime.recorded_input_selection import recorded_candidates, recorded_listing, selected_recorded_path
from hlagent.analysis.hatk_custom_association import check_settings
from hlagent.analysis.hatk_custom_meta import study_table, meta_settings
from hlagent.analysis.hatk_custom_conditional import review_custom_conditions
from hlagent.analysis.hatk_custom_markers import pedigree, marker_settings, check_dictionaries
from hlagent.runtime.presentation import stage_title
from hlagent.runtime.chat_types import BACK_WORDS, CANCEL_WORDS, EXIT_WORDS, ChatReply, result_context, validate_decision
from hlagent.analysis.hatk import ROOT, prepare_association, prepare_conditional, prepare_example, prepare_omnibus, prepare_meta, prepare_custom_association, prepare_custom_conditional, prepare_stepwise, prepare_custom_markers, prepare_custom_omnibus, prepare_custom_meta, check_association_panel, run_example


SYSTEM_PROMPT = COMMON_RULES + """You are HLAgent's HLA Fine-mapping agent. A separate Supervisor routes
HATK requests to you. Five bundled examples are available: Node1 generates
markers from the included 600-person WTCCC HPED/SNP data and existing
hg18/IMGT3320 dictionaries; Node2 uses an explicitly selected completed Node1
example run for RA logistic association, Manhattan plot, and eight AA heatmaps;
Node3 uses a completed Node2 RA example for ONE conditional logistic regression,
with condition markers explicitly reviewed by the user. The Node2 lead marker is
only a suggestion; no automatic forward selection or repeated fitting is supported.
The program collects condition marker IDs locally. It does not establish causal independence.
Node4 also accepts supplied AA-only phased BGL, a six-column FAM, and a named binary phenotype,
with an explicit genome build. Completed Phasing runs may supply checked BGL/FAM/build;
the matching phenotype is still collected locally. The program collects these locally; no paths or settings are inferred.
Unique IIDs and exact phased pair order are required. No new phasing or covariates are supported.
Node4 uses bundled prephased AA markers for the original HATK R omnibus tests
and a Manhattan plot, without selecting or rerunning Node1-3 or performing new phasing.
It repeats each person's phenotype on two haplotype rows without adjusting
within-person dependence. This is a compatibility example, not validated inference.
Node5 also accepts exactly two supplied normalized association TSVs with CHR SNP BP A1 A2 NMISS OR SE ESTIMABLE.
They must concern the same binary trait, compatible log-odds effects/case coding, and declared common build.
The program collects paths, shared trait/build and a sample-nonoverlap declaration locally.
It aligns exact or reversed allele labels and excludes coordinate/allele mismatches and ambiguous DNA SNPs.
It does not infer strand, verify nonoverlap from summary tables, rerun regressions or correct overlap.
Never claim that a declaration proves independent cohorts or that estimated sample sizes can be summed.
Node5 takes a completed Node2 RA example, divides its samples into disjoint
phenotype-stratified halves, refits PLINK1.9 association separately, then performs
fixed-effect inverse-variance meta-analysis and a Manhattan plot. It does not pool
the full-cohort Node2 estimates or RA with another disease. These are demonstration
subsets of one cohort, not independent replication studies; relatedness is not assessed.
Node1 also accepts a supplied headerless HPED (22 columns, eight standard genes),
a prepared dictionary directory, explicit genome build and IMGT filename tag,
and an optional matched SNP panel. It uses two-field output and the existing QC;
no phenotype file, IMGT2Seq, or association is run. The program collects every
input locally; a completed conversion run may supply checked HPED. Never infer
gene order, dictionary build, dictionary version or SNP files.
Node2 also accepts a supplied chromosome 6 PLINK panel and a named binary
phenotype column (1 control, 2 case) with an explicit hg18/19/38 build. No Node1
run is needed. A completed Node1 run may supply its checked panel and recorded build;
the phenotype and Node3 condition IDs are still collected separately.
The program collects paths/settings locally and shows a plan.
The direct Node2 path runs association and Manhattan with optional explicitly selected numeric covariates, without new QC
or heatmaps. Direct Node3 additionally collects exact condition marker IDs,
then runs one fresh matched baseline and ONE conditional scan with a comparison
table, no plots. It needs no Node2 run or automatic lead-marker selection.
Never infer the trait, build, coding or condition IDs. Conditions are collected locally.
These examples are separate from the CookHLA example. Do not claim a CookHLA
result is automatically connected to HATK.
CookHLA is handled by HLA Imputation. CookHLA-to-HPED conversion and new HATK marker
phasing are handled by File Converter and Genotype Phasing at Supervisor. To switch,
ask the user to type back and request the relevant role there. These operations do
not start automatically in Fine-mapping.

Return JSON with action and message. Actions:
- node1: the user explicitly requests the bundled marker-generation example.
- node1_custom: the user requests marker generation from supplied HLA genotypes and dictionaries.
  The program collects HPED/dictionary/settings/optional SNP inputs locally.
- node2: the user explicitly requests association or its plots using the example.
  The program collects the Node1 run selection locally; never extract or invent paths.
- node2_custom: the user requests association on a supplied marker panel/phenotype.
  The program collects the inputs locally; never extract paths/settings yourself.
- node3_custom: the user requests conditional analysis with a supplied panel/phenotype/markers.
  The program collects all inputs locally; no automatic marker selection is available.
- node3: the user explicitly requests the bundled conditional-regression example.
  The program collects the Node2 run selection locally; never extract or invent paths.
- node4_custom: the user requests omnibus tests on supplied already-phased AA data.
  The program collects phased file, FAM, phenotype name/file and build locally.
- node4: the user explicitly requests the bundled prephased omnibus example.
- node5_custom: the user requests fixed-effect meta-analysis of two supplied normalized binary-trait result tables.
  All paths/settings/declarations are collected locally, never inferred.
- node5: the user explicitly requests the bundled split-sample meta-analysis example.
  The program collects a compatible Node2 run locally.
- conditions: the user wants to choose/change condition marker IDs for Node3.
  The program asks locally; never extract, invent, or approve marker IDs yourself.
- choose_stage: the user asks for HATK/fine-mapping without a clear example step.
- plan: prepare or retry the currently selected example with a new approval.
- answer: answer questions or ask for clarification. State limitations plainly.
- back: return to Supervisor without ending this chat.
- cancel: discard this plan and return to Supervisor.
- exit: a direct request to end this chat, not a question, quotation, or negation.
- unsupported: custom HATK data outside the Node1 marker-input, Node2/Node3 binary panel, Node4 prephased AA and Node5 two-study normalized-summary paths, continuous traits,
  tool resource/QC changes, covariates outside custom Node2/single Node3/stepwise, new phasing, conditional omnibus, random-effects, overlapping-study meta-analysis, other summary formats or arbitrary commands.

A generic request for an example means choose_stage; do not guess Node1/Node2/Node3/Node4/Node5.
Never change settings, select files, approve analysis, or invent execution results.
Only the exact local y/yes approval starts a displayed plan. Even 'run now' only
prepares it. Node2 does not rerun Node1; Node3 does not rerun Node1/Node2. A retry preserves the selected step/input
but still needs a newly reviewed plan and approval.
Use the current state and recent conversation. Only last_result establishes that
an analysis ran. A null result means no recorded result is available here. Do not
invent failure causes; raw errors and individual-level data are not supplied.
Structure checks and example success are not proof of biological accuracy.
The exact public command stepwise opens optional automatic marker selection with its own reviewed plan.
Never substitute stepwise for a single conditional request or insert it into the default workflow.
Node3 uses one PLINK 1.9 logistic fit, like the reviewed HATK conditional path.
Do not describe it as stepwise or claim that new markers were selected automatically.
Before/after associations may have different sample counts because of missing genotypes.
Do not interpret an unestimable test as no association or claim every model converged.
Historical stepwise results remain historical; they do not describe the current example.
Interpret natural cancellation/return/exit intent; distinguish it from questions,
quotations, or requests not to exit. For ambiguous 'stop', ask what to stop.
Use the latest user's language, retaining technical identifiers. Keep replies
brief and direct. Return no text outside JSON.
"""

DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["node1", "node1_custom", "node2", "node2_custom", "node3_custom", "node3", "node4", "node4_custom", "node5", "node5_custom", "conditions", "choose_stage", "plan", "answer",
                                                  "back", "cancel", "exit", "unsupported"]},
        "message": {"type": "string"},
    },
    "required": ["action", "message"],
}


class FineMappingAgent:
    def __init__(self, client, seccomp_unconfined=False, run_root=None):
        self.client = client
        self.seccomp_unconfined = seccomp_unconfined
        self.run_root = Path(run_root) if run_root is not None else ROOT / "runs"
        self.pending_plan = None
        self.last_result = None
        self.history = []
        self.closed = False
        self.step = None
        self.parent_run = None
        self.input_field = None
        self.run_names = []
        self.phenotype_files = []
        self.dictionary_candidates = []
        self.condition_markers = None
        self.association_inputs = None
        self.covariates_requested = False
        self.covariate_proposal = None
        self.marker_inputs = None
        self.omnibus_inputs = None
        self.meta_inputs = None
        self.meta_prepared_runs = {}
        self.conversion_run = None
        self.phasing_run = None
        self.association_run = None
        self.conditional_source_run = None

    @property
    def parent_label(self):
        return stage_title("hatk_node2" if self.step in ("node3", "node5") else "hatk_node1")

    @property
    def input_prompt(self):
        if self.pending_plan is not None:
            return f"Start {stage_title('hatk_' + self.step)}? [y/n]> "
        return {"imgt_source": "IMGT source directory or download> ",
                "imgt_download_version": "IMGT version [3.32.0/3320/latest]> ",
                "imgt_download_approval": "Download IMGT source? [y/n]> ",
                "stage": "HATK analysis [1-5/node command]> ",
                "conditions": "Condition marker ID(s)> ",
                "meta_study1": "Study 1 normalized TSV path> ",
                "meta_study2": "Study 2 normalized TSV path> ",
                "meta_summary_run": "Prepared summary run name, number, or directory> ",
                "meta_trait": "Shared phenotype name> ",
                "meta_build": "Common declared genome build [18/19/38]> ",
                "meta_nonoverlap": "Confirm no overlapping samples [y/n]> ",
                "omnibus_phased": "Phased AA BGL path or run> ",
                "omnibus_phasing_run": "Phasing run name, number, or directory> ",
                "omnibus_fam": "FAM file path> ",
                "omnibus_pheno": "Phenotype [number/path]> ",
                "omnibus_prepared_run": "Prepared phenotype run name, number, or directory> ",
                "omnibus_name": "Phenotype column name> ",
                "omnibus_build": "Phased AA genome build [18/19/38]> ",
                "marker_hped": "HPED path or run> ",
                "marker_edit": "Change [hped/build/imgt/dictionary/snps]> ",
                "marker_conversion_run": "Conversion run name, number, or directory> ",
                "marker_build": "HLA/dictionary genome build [18/19/38]> ",
                "marker_imgt": "IMGT filename tag> ",
                "marker_dictionary": "Dictionary directory> ",
                "marker_snps": "Optional SNP PLINK prefix [none]> ",
                "association_prefix": "Marker PLINK prefix or run> ",
                "association_node1_run": "Marker generation run [number/name/directory]> ",
                "association_pheno": "Phenotype [number/path]> ",
                "association_prepared_run": "Prepared phenotype run name, number, or directory> ",
                "association_name": "Phenotype column name> ",
                "association_build": "Panel genome build [18/19/38]> ",
                "association_threshold": "Stepwise P threshold [bonferroni/value]> ",
                "association_mac": "Minimum MAC [20]> ",
                "association_iterations": "Maximum stepwise scans [100]> ",
                "association_conditions": "Condition marker ID(s)> ",
                "association_covar_file": "Covariate table [phenotype/none] or path> ",
                "association_covar_names": "Covariate columns [numbers/names]> ",
                "parent_run": f"{self.parent_label} run name, number, or directory> "}.get(self.input_field, "HLA Fine-mapping> ")

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

    def choose_stage(self):
        self.pending_plan = None
        self.conditional_source_run = None
        self.step = None
        self.parent_run = None
        self.association_inputs = None
        self.covariates_requested = False
        self.covariate_proposal = None
        self.marker_inputs = None
        self.omnibus_inputs = None
        self.meta_inputs = None
        self.input_field = "stage"
        return ChatReply("Choose a HATK analysis:\n\n"
                         "1. HLA marker generation (HATK Node1)\n"
                         "   Example: node1 (bundled HLA genotypes)\n"
                         "   Your data: node1-custom (HPED and dictionaries)\n"
                         "   Dictionary: type prepare-dictionary (local IMGT source or download, hg18)\n\n"
                         "2. Marker association (HATK Node2)\n"
                         "   Example: node2 (select a completed marker-generation example (HATK Node1))\n"
                         "   Your data: node2-custom (marker panel and phenotype)\n"
                         "   Optional covariates: enter node2-custom, then type covariates at the review plan.\n\n"
                         "3. Conditional association (HATK Node3)\n"
                         "   Single scan: node3 or node3-custom (adjust for specified markers)\n"
                         "   Automatic selection: stepwise (repeat scans and select adjustment markers)\n"
                         "   Optional covariates: enter node3-custom or stepwise, then type covariates at the review plan.\n\n"
                         "4. Amino-acid omnibus test (HATK Node4)\n"
                         "   Example: node4 (bundled phased amino-acid genotypes)\n"
                         "   Your data: node4-custom (phased AA BGL, FAM and phenotype)\n\n"
                         "5. Meta-analysis (HATK Node5)\n"
                         "   Example: node5 (split RA cohort, select a completed marker-association example (HATK Node2))\n"
                         "   Your data: node5-custom (two independent study summaries)\n\n"
                         "Select: enter 1/2/3/4/5 for an example, or a node command for either route.\n")

    def choose_parent(self, step="node2"):
        self.pending_plan = None
        self.step = step
        self.parent_run = None
        self.condition_markers = None
        self.association_inputs = None
        self.covariates_requested = False
        self.covariate_proposal = None
        self.marker_inputs = None
        self.omnibus_inputs = None
        self.meta_inputs = None
        self.input_field = "parent_run"
        self.run_names = []
        for record_path in sorted(self.run_root.glob('*/run.json')):
            directory = record_path.parent
            if is_archived(directory):
                continue
            try:
                record = json.loads((directory / "run.json").read_text())
                plan = record.get("plan", {})
                if (record.get("status") == "completed" and plan.get("example") == ("hatk_node2" if step in ("node3", "node5") else "hatk_node1")
                        and plan.get("genome_build") == 18 and str(plan.get("imgt")) == "3320"
                        and plan.get("field_format") == 2
                        and (step not in ("node3", "node5") or (plan.get("phenotype") == "RA"
                             and plan.get("regression") == "logistic" and plan.get("covariates") == []))):
                    self.run_names.append(directory.name)
            except (OSError, ValueError, AttributeError):
                continue
        from hlagent.runtime.run_metadata import saved_run_listing
        listing = saved_run_listing([self.run_root / name / "run.json" for name in self.run_names]) if self.run_names else ""
        return ChatReply((f"Completed {self.parent_label} examples:\n" + listing if listing else f"No completed {self.parent_label} examples found here.") +
                         f"\n\nSelect: enter a listed number or a known completed {self.parent_label} run directory.\n"
                         "Files and recorded hashes will be checked.\n" +
                         ("Alternative: Type custom for two supplied study result tables.\n" if step == "node5" else
                          "Alternative: Type custom for a supplied marker panel and phenotype.\n" if step in ("node2", "node3") else "") +
                         (f"For optional age, sex or PC adjustment, type {step}-custom.\n" if step in ("node2", "node3") else "") +
                         ("For automatic selection, type stepwise.\n" if step == "node3" else "") +
                         "\ndetails: timestamps, run IDs and paths")

    def choose_custom_meta(self):
        self.pending_plan = None
        self.step = "node5"
        self.parent_run = None
        self.marker_inputs = None
        self.association_inputs = None
        self.covariates_requested = False
        self.covariate_proposal = None
        self.omnibus_inputs = None
        self.meta_inputs = {}
        self.meta_prepared_runs = {}
        self.input_field = "meta_study1"
        return ChatReply("Input: enter the first normalized association TSV path.\n"
                         "Reuse: enter run to select a completed prepared summary.\n"
                         "Prepare CSV/TSV: use prepare-summary at Supervisor.\n\n"
                         "Columns: CHR SNP BP A1 A2 NMISS OR SE ESTIMABLE\n"
                         "Required: same binary trait, compatible case coding/log-odds effects and genome build.\n"
                         "The samples must not overlap. Inputs are collected locally.\n\n")

    def collect_custom_meta(self, text):
        try:
            if self.input_field == 'meta_summary_run':
                if text.lower() == 'files':
                    self.input_field = self.meta_run_field
                    return ChatReply('Enter a normalized study TSV path, or run to select a prepared summary.')
                run = selected_run(text, self.run_names, self.run_root)
                path, _ = prepared_summary(run)
                self.input_field = self.meta_run_field
                self.meta_prepared_runs[self.input_field] = run
                text = str(path)
            elif self.input_field in ('meta_study1', 'meta_study2') and text.lower() == 'run':
                self.meta_run_field = self.input_field
                self.run_names = completed_runs(self.run_root, 'summary')
                self.input_field = 'meta_summary_run'
                return ChatReply(run_listing(self.run_names, 'Prepared summary', self.run_root),
                                 actions=('files: enter a normalized TSV path instead',))
            if self.input_field in ("meta_study1", "meta_study2"):
                path = require_file(Path(text).expanduser().resolve())
                study_table(path)
                first = self.input_field == "meta_study1"
                self.meta_inputs["study1_file" if first else "study2_file"] = str(path)
                self.input_field = "meta_study2" if first else "meta_trait"
                return ChatReply("Enter the second normalized study TSV, or run for a prepared summary. Two independent studies are required." if first else
                                 "Enter the shared trait name. Both studies must use compatible case coding, effects and analysis models.")
            if self.input_field == "meta_trait":
                check_settings(text, 18)
                for run in self.meta_prepared_runs.values():
                    if prepared_summary(run)[1]['phenotype_name'] != text:
                        raise ValueError('Trait name differs from a selected prepared summary. Review source metadata. No trait is renamed automatically.')
                self.meta_inputs["phenotype_name"] = text
                self.input_field = "meta_build"
                return ChatReply("Enter the common genome build: 18, 19 or 38. It must already match in both tables.")
            if self.input_field == "meta_build":
                build = int(text)
                check_settings(self.meta_inputs["phenotype_name"], build)
                for run in self.meta_prepared_runs.values():
                    if prepared_summary(run)[1]['genome_build'] != build:
                        raise ValueError('Genome build differs from a selected prepared summary. No coordinates are converted.')
                self.meta_inputs["genome_build"] = build
                self.input_field = "meta_nonoverlap"
                return ChatReply("Confirm that the two study samples do not overlap. This cannot be verified from summary tables. "
                                 "Enter y to record your declaration and review a plan. It does not start analysis. Enter n to leave.")
            if text.lower() not in ("y", "yes"):
                return ChatReply("Enter y to declare no sample overlap, or n/back to leave. No plan has been approved.")
            meta_settings(self.meta_inputs["phenotype_name"], self.meta_inputs["genome_build"], True)
            self.meta_inputs["nonoverlap_confirmed"] = True
            for field, run in self.meta_prepared_runs.items():
                path, settings = prepared_summary(run)
                key = 'study1_file' if field == 'meta_study1' else 'study2_file'
                if (str(path) != self.meta_inputs[key] or settings['phenotype_name'] != self.meta_inputs['phenotype_name']
                        or settings['genome_build'] != self.meta_inputs['genome_build']):
                    raise ValueError('Prepared summary no longer matches the declared study inputs.')
            plan = prepare_custom_meta(**self.meta_inputs, run_root=self.run_root,
                                       seccomp_unconfined=self.seccomp_unconfined)
        except (OSError, ValueError, RuntimeError) as error:
            return ChatReply(f"Input check failed: {error}\nType back to re-enter the study inputs.")
        self.input_field = None
        self.pending_plan = plan
        return self.review(plan)

    def choose_custom_omnibus(self):
        self.pending_plan = None
        self.phasing_run = None
        self.step = "node4"
        self.parent_run = None
        self.marker_inputs = None
        self.association_inputs = None
        self.covariates_requested = False
        self.covariate_proposal = None
        self.omnibus_inputs = {}
        self.meta_inputs = None
        self.input_field = "omnibus_phased"
        return ChatReply("Input: enter a phased AA BGL file path, optionally including INS_AA insertion rows.\n"
                         "Reuse: enter run to select a completed Phasing run with its BGL/FAM.\n\n"
                         "Format: an I id paired-IID header, or five FAM-matching pedigree headers.\n"
                         "Marker IDs must encode AA gene, residue position, chromosome 6 coordinate and exon.\n"
                         "Insertion IDs use INS_AA_gene_leftxright_coordinate with case-sensitive z/Z states.\n"
                         "No new phasing is performed. Inputs are collected locally.\n\n")

    def collect_custom_omnibus(self, text):
        try:
            field = self.input_field
            if field in ('omnibus_pheno', 'omnibus_prepared_run'):
                selection = self.select_prepared_phenotype(text, 'omnibus')
                if isinstance(selection, ChatReply):
                    return selection
                field = self.input_field
                text = selection
            if field == "omnibus_phased" and text.lower() == "run":
                self.run_names = completed_runs(self.run_root, "phasing")
                self.input_field = "omnibus_phasing_run"
                return ChatReply(run_listing(self.run_names, "Phasing", self.run_root),
                                 actions=('files: enter phased BGL/FAM paths instead',))
            if field == "omnibus_phasing_run":
                if text.lower() == "files":
                    return self.choose_custom_omnibus()
                run = selected_run(text, self.run_names, self.run_root)
                phased, fam, build = completed_phasing_inputs(run)
                self.phasing_run = run
                self.omnibus_inputs = {"phased_file": str(phased), "fam_file": str(fam), "genome_build": build}
                self.input_field = "omnibus_pheno"
                return self.phenotype_reply(f"Selected Phasing run: {run.name}; recorded hg{build}.\n\n")
            if field in ("omnibus_phased", "omnibus_fam", "omnibus_pheno"):
                key, next_field, message = {
                    "omnibus_phased": ("phased_file", "omnibus_fam", "Enter the six-column FAM in exactly the phased sample-pair order. IIDs must be unique."),
                    "omnibus_fam": ("fam_file", "omnibus_pheno", PHENOTYPE_HELP),
                    "omnibus_pheno": ("phenotype_file", "omnibus_name", "Enter the phenotype column name; 1=control and 2=case are required.")}[field]
                path = require_file(Path(text).expanduser().resolve())
                if field == "omnibus_pheno":
                    columns = phenotype_columns(path)
                self.omnibus_inputs[key] = str(path)
                self.input_field = next_field
                if next_field == "omnibus_pheno":
                    return self.phenotype_reply()
                if field == 'omnibus_pheno':
                    return self.phenotype_column_reply(columns)
                return ChatReply(message)
            if field == "omnibus_name":
                check_settings(text, 18)
                review_phenotype(self.omnibus_inputs["fam_file"], self.omnibus_inputs["phenotype_file"], text)
                from hlagent.analysis.hatk_omnibus import read_phased_files
                read_phased_files(self.omnibus_inputs["phased_file"], self.omnibus_inputs["fam_file"],
                                  self.omnibus_inputs["phenotype_file"], text)
                self.omnibus_inputs["phenotype_name"] = text
                if self.phasing_run is not None:
                    return self.prepare_plan()
                self.input_field = "omnibus_build"
                return ChatReply("Enter the phased marker genome build: 18, 19, or 38. Coordinates are not converted or inferred.")
            build = int(text)
            check_settings(self.omnibus_inputs["phenotype_name"], build)
            self.omnibus_inputs["genome_build"] = build
            return self.prepare_plan()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            return self.phenotype_error(error)

    def choose_custom_markers(self):
        self.pending_plan = None
        self.conversion_run = None
        self.step = "node1"
        self.parent_run = None
        self.association_inputs = None
        self.covariates_requested = False
        self.covariate_proposal = None
        self.marker_inputs = {}
        self.omnibus_inputs = None
        self.meta_inputs = None
        self.input_field = "marker_hped"
        return ChatReply("Input: enter a headerless 22-column HPED path.\n"
                         "Reuse: enter run to select a completed CookHLA-to-HPED conversion.\n\n"
                         "Format: six pedigree fields, then two alleles per gene.\n"
                         "Gene order: A, B, C, DPA1, DPB1, DQA1, DQB1, DRB1.\n"
                         "Required: prepared two-field dictionaries. SNP data is optional.\n"
                         "Inputs stay in local prompts.\n\n")

    def collect_custom_markers(self, text):
        try:
            if self.input_field == "marker_hped" and text.lower() == "run":
                self.run_names = completed_runs(self.run_root, "conversion")
                self.input_field = "marker_conversion_run"
                return ChatReply(run_listing(self.run_names, "Conversion", self.run_root),
                                 actions=('files: enter an HPED path instead',))
            if self.input_field == "marker_conversion_run":
                if text.lower() == "files":
                    self.input_field = "marker_hped"
                    return ChatReply("Enter an HPED path, or run to select a completed conversion. Other selected inputs are retained.")
                run = selected_run(text, self.run_names, self.run_root)
                path = conversion_hped(run)
                pedigree(path)
                self.conversion_run = run
                self.marker_inputs["hped_file"] = str(path)
                reply = self.next_marker_input()
                reply.message = f"Selected conversion run: {run.name}. " + reply.message
                return reply
            elif self.input_field == "marker_hped":
                path = require_file(Path(text).expanduser().resolve())
                pedigree(path)
                self.marker_inputs["hped_file"] = str(path)
                self.conversion_run = None
            elif self.input_field == "marker_build":
                build = int(text)
                marker_settings(build, "3320")
                self.marker_inputs["genome_build"] = build
            elif self.input_field == "marker_imgt":
                marker_settings(self.marker_inputs["genome_build"], text)
                self.marker_inputs["imgt"] = text
            elif self.input_field == "marker_dictionary":
                if text.lower() in ('candidates', 'files'):
                    return ChatReply(self.dictionary_listing())
                directory = Path(selected_recorded_path(text, self.dictionary_candidates))
                check_dictionaries(directory, marker_settings(self.marker_inputs["genome_build"], self.marker_inputs["imgt"]))
                self.marker_inputs["dictionary_dir"] = str(directory)
            else:
                self.marker_inputs["snp_prefix"] = None if not text or text.lower() == "none" else text
            return self.next_marker_input()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            return ChatReply(f"Input check failed: {error}\nEnter a corrected value, use edit to change another selected input, or type back.")

    def dictionary_listing(self):
        self.dictionary_candidates = recorded_candidates(
            self.run_root, 'dictionary', self.marker_inputs['genome_build'], self.marker_inputs['imgt'])
        return recorded_listing(self.dictionary_candidates, 'dictionary')

    def next_marker_input(self):
        fields = {
            "hped_file": ("marker_hped", "Enter an HPED path, or run to select a completed conversion."),
            "genome_build": ("marker_build", "Enter the matching dictionary genome build: 18, 19, or 38. HPED does not determine the dictionary build."),
            "imgt": ("marker_imgt", "Enter the numeric IMGT dictionary filename tag, for example 3320."),
            "dictionary_dir": ("marker_dictionary", "Enter the prepared directory containing the matching HLA allele table and AA/SNP sequence/MAP dictionaries."),
            "snp_prefix": ("marker_snps", "Enter an optional chromosome 6 SNP PLINK prefix, or none/Enter to skip. Its sample metadata must match HPED."),
        }
        for key, (field, message) in fields.items():
            if key not in self.marker_inputs:
                self.input_field = field
                if field == "marker_dictionary":
                    message += "\n" + self.dictionary_listing()
                return ChatReply(message, actions=('candidates: refresh the dictionary list',)
                                 if field == 'marker_dictionary' else ())
        reply = self.prepare_plan()
        if self.marker_inputs["snp_prefix"] is not None:
            self.marker_inputs["snp_prefix"] = reply.plan.data["snp_prefix"]
        return reply

    def edit_markers(self, field=""):
        self.discard_plan()
        if self.step != "node1" or self.marker_inputs is None or len(self.marker_inputs) != 5:
            return ChatReply("Complete the supplied HLA marker inputs before editing a reviewed plan.")
        fields = {"hped": "hped_file", "build": "genome_build", "imgt": "imgt",
                  "dictionary": "dictionary_dir", "snps": "snp_prefix"}
        if field not in fields:
            self.input_field = "marker_edit"
            values = '\n'.join(f"- {name}: {self.marker_inputs[key]}" for name, key in fields.items())
            return ChatReply("Choose one input to change. Other inputs are retained:\n" + values +
                             "\nEnter hped/build/imgt/dictionary/snps, or cancel to return to Supervisor.")
        self.input_field = "marker_" + field
        value = self.marker_inputs[fields[field]]
        listing = "\n" + self.dictionary_listing() if field == "dictionary" else ""
        return ChatReply(f"Current {field}: {value}. Enter a replacement; other inputs are retained. " +
                         ("Enter run to select a completed conversion. " if field == "hped" else
                          "Enter none to remove the optional SNP panel. " if field == "snps" else "") +
                         "All inputs will be rechecked and a new plan will require approval." + listing,
                         actions=('candidates: refresh the dictionary list',) if field == 'dictionary' else ())

    def choose_custom_association(self, step="node2"):
        self.pending_plan = None
        self.association_run = None
        self.conditional_source_run = None
        self.step = step
        self.stepwise_selection = {}
        self.parent_run = None
        self.marker_inputs = None
        self.omnibus_inputs = None
        self.meta_inputs = None
        self.condition_markers = None
        self.association_inputs = {}
        self.covariates_requested = False
        self.covariate_proposal = None
        self.input_field = "association_prefix"
        return ChatReply(("Task: Stepwise conditional association (HATK Node3).\n\n" if step == "stepwise" else
                          "Task: Conditional association (HATK Node3).\n\n" if step == 'node3' else
                          "Task: Marker association (HATK Node2).\n\n") +
                         "Input: enter a chromosome 6 PLINK marker panel prefix.\n"
                         "Reuse: enter run to select a completed HLA marker generation run (HATK Node1).\n"
                         "Format: prefix without .bed/.bim/.fam.\n\n"
                         "Reusing a run keeps its checked panel and recorded build.\n"
                         "No parent run is required for direct input.\n"
                         "Model: binary phenotype 1=control/2=case. No heatmaps.\n"
                         "Optional covariates: type covariates at the review plan for Node2, single Node3 or stepwise.\n"
                         "Inputs are collected locally.\n\n")

    def collect_custom_association(self, text):
        try:
            if self.input_field in ('association_pheno', 'association_prepared_run'):
                selection = self.select_prepared_phenotype(text, 'association')
                if isinstance(selection, ChatReply):
                    return selection
                text = selection
            if self.input_field == "association_prefix" and text.lower() == "run":
                self.run_names = completed_runs(self.run_root, "node1")
                self.input_field = "association_node1_run"
                return ChatReply(run_listing(self.run_names, stage_title("hatk_node1"), self.run_root),
                                 actions=('files: enter a marker panel prefix instead',))
            if self.input_field == "association_node1_run":
                if text.lower() == "files":
                    return self.choose_custom_association(self.step)
                run = selected_run(text, self.run_names, self.run_root)
                prefix, build = node1_panel(run)
                panel = check_association_panel(prefix)
                self.association_run = run
                self.association_inputs = {"input_prefix": panel["prefix"], "genome_build": build}
                self.input_field = "association_pheno"
                return self.phenotype_reply('Selected marker panel\n'
                                            f'  Run: {run.name}\n'
                                            f"  Samples: {panel['samples']} | Genome build: hg{build} (recorded)\n\n")
            if self.input_field == "association_prefix":
                self.association_inputs["input_prefix"] = check_association_panel(text)["prefix"]
                self.input_field = "association_pheno"
                return self.phenotype_reply()
            if self.input_field == "association_pheno":
                path = require_file(Path(text).expanduser().resolve())
                columns = phenotype_columns(path)
                self.association_inputs["phenotype_file"] = str(path)
                self.input_field = "association_name"
                return self.phenotype_column_reply(columns)
            if self.input_field == "association_name":
                check_settings(text, 18)
                review_phenotype(self.association_inputs["input_prefix"] + ".fam",
                               self.association_inputs["phenotype_file"], text)
                self.association_inputs["phenotype_name"] = text
                if self.association_run is None:
                    self.input_field = "association_build"
                    return ChatReply("Enter the panel genome build: 18, 19, or 38. It is not inferred from file names.")
                if self.step == "stepwise":
                    self.input_field = "association_threshold"
                    return self.stepwise_threshold_reply()
                if self.step == "node3":
                    self.input_field = "association_conditions"
                    return ChatReply("Enter exact condition marker IDs separated by spaces. No marker is selected automatically.")
            if self.input_field == "association_build":
                build = int(text)
                check_settings(self.association_inputs["phenotype_name"], build)
                self.association_inputs["genome_build"] = build
                if self.step == "stepwise":
                    self.input_field = "association_threshold"
                    return self.stepwise_threshold_reply()
                if self.step == "node3":
                    self.input_field = "association_conditions"
                    return ChatReply("Enter exact condition marker IDs separated by spaces. No marker is selected automatically.")
            elif self.input_field == "association_threshold":
                from hlagent.analysis.hatk_stepwise import selection_settings
                value = None if text.lower() in ('', 'bonferroni', 'auto') else float(text)
                if value is not None:
                    selection_settings(value)
                self.stepwise_selection['threshold'] = value
                self.input_field = 'association_mac'
                return ChatReply('Enter minimum minor allele count (MAC), or press Enter for 20.\n'
                                 'MAC is rechecked on the samples used in each conditional test. Enter 0 to disable this filter.')
            elif self.input_field == "association_mac":
                value = int(text) if text else 20
                if value < 0:
                    raise ValueError('Minimum MAC must be a nonnegative integer.')
                self.stepwise_selection['min_mac'] = value
                self.input_field = 'association_iterations'
                return ChatReply('Enter maximum scans from 1 to 100, or press Enter for 100.')
            elif self.input_field == "association_iterations":
                from hlagent.analysis.hatk_stepwise import selection_settings
                value = int(text) if text else 100
                selection_settings(0.05, value)
                self.stepwise_selection['max_iterations'] = value
            elif self.input_field == "association_conditions":
                self.condition_markers = review_custom_conditions(self.association_inputs["input_prefix"] + ".bim", text.split())
            if self.covariates_requested and not self.association_inputs.get('covariates'):
                self.input_field = 'association_covar_file'
                return self.covariate_file_reply()
            plan = self.prepare_supplied_plan()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            return self.phenotype_error(error)
        self.input_field = None
        self.pending_plan = plan
        return self.review(plan)

    def covariate_file_reply(self):
        return ChatReply('Choose a numeric covariate table starting with FID IID and named columns.\n'
                         'Enter phenotype to use extra columns from the selected phenotype table, or enter a file path.\n'
                         'Every panel sample must have complete values. No automatic recoding, PCA or sample removal.\n'
                         'Categorical variables must already be explicitly encoded. Enter none to use no covariates.')

    def collect_covariates(self, text):
        try:
            if self.input_field == 'association_covar_file':
                if text.lower() == 'none':
                    self.association_inputs.pop('covariate_file', None)
                    self.association_inputs.pop('covariates', None)
                    self.covariates_requested = False
                    self.covariate_proposal = None
                else:
                    path = (self.association_inputs['phenotype_file'] if text.lower() == 'phenotype' else text)
                    path = require_file(Path(path).expanduser().resolve())
                    from hlagent.runtime.covariate_selection import proposals, listing
                    proposal = proposals(path, self.association_inputs['phenotype_name'])
                    self.association_inputs['covariate_file'] = str(path)
                    self.covariate_proposal = proposal
                    self.input_field = 'association_covar_names'
                    return ChatReply(listing(proposal), actions=(
                        'files: choose another covariate table',
                        'none: remove all covariates',))
            else:
                from hlagent.analysis.covariates import aligned_covariates
                from hlagent.runtime.covariate_selection import proposals, selected_columns
                if text.lower() == 'files':
                    self.input_field = 'association_covar_file'
                    return self.covariate_file_reply()
                if text.lower() == 'none':
                    self.input_field = 'association_covar_file'
                    return self.collect_covariates('none')
                proposal = proposals(self.association_inputs['covariate_file'], self.association_inputs['phenotype_name'])
                if self.covariate_proposal is not None and proposal['source_sha256'] != self.covariate_proposal['source_sha256']:
                    raise ValueError('The covariate table changed after the columns were displayed. Type files to review it again.')
                names = selected_columns(text, proposal)
                aligned_covariates(self.association_inputs['input_prefix'] + '.fam',
                                   self.association_inputs['covariate_file'], names,
                                   self.association_inputs['phenotype_name'])
                self.association_inputs['covariates'] = names
            plan = self.prepare_supplied_plan()
        except (OSError, ValueError, IndexError) as error:
            return ChatReply(f'Covariate check failed: {error}\nCorrect the value or type back.')
        self.input_field = None
        self.pending_plan = plan
        return self.review(plan)

    def stepwise_threshold_reply(self):
        return ChatReply('Enter a selection P cutoff, or press Enter for Bonferroni (0.05 / initial MAC-eligible markers).\n'
                         'The resulting cutoff is fixed across scans. It does not adjust for the full stepwise selection process.')

    def phenotype_help(self):
        self.phenotype_files = phenotype_candidates(self.run_root)
        return PHENOTYPE_BRIEF + '\n\n' + candidate_listing(self.phenotype_files)

    def phenotype_reply(self, context=''):
        return ChatReply(context + self.phenotype_help(), actions=(
            'help: format and commands | candidates: refresh | run: prepared runs',))

    def phenotype_column_reply(self, columns):
        return ChatReply('Available phenotype columns: ' + ', '.join(columns) +
                         '\n\nEnter the column name. No column is selected automatically.',
                         actions=('phenotype: choose another phenotype file',))

    def phenotype_error(self, error):
        ready = ((self.association_inputs is not None and 'input_prefix' in self.association_inputs)
                 or (self.omnibus_inputs is not None and 'fam_file' in self.omnibus_inputs))
        return ChatReply(f'Input check failed: {error}\n\nCorrect the value or file, then retry.',
                         actions=('phenotype: choose another phenotype file',) if ready else ())

    def select_prepared_phenotype(self, text, role):
        if self.input_field == role + '_pheno':
            if text.lower() == 'help':
                return ChatReply(PHENOTYPE_HELP, actions=(
                    'candidates: return to the file list | run: prepared phenotypes',))
            if text.lower() in ('candidates', 'files'):
                return self.phenotype_reply()
            if text.isdigit():
                return select_candidate(text, self.phenotype_files)
        if self.input_field == role + '_pheno' and text.lower() == 'run':
            self.run_names = completed_runs(self.run_root, 'phenotype')
            self.input_field = role + '_prepared_run'
            return ChatReply(run_listing(self.run_names, 'Prepared phenotype', self.run_root))
        if self.input_field == role + '_prepared_run':
            if text.lower() == 'files':
                self.input_field = role + '_pheno'
                return self.phenotype_reply()
            path = prepared_phenotype(selected_run(text, self.run_names, self.run_root))
            self.input_field = role + '_pheno'
            return str(path)
        return text

    def prepare_supplied_plan(self):
        if self.conditional_source_run is not None:
            from hlagent.runtime.workflow_runs import association_inputs
            checked = association_inputs(self.conditional_source_run)
            if any(checked[key] != self.association_inputs[key] for key in ('input_prefix', 'genome_build')):
                raise ValueError('Selected Node2 run no longer matches the conditional inputs.')
        if self.association_run is not None:
            prefix, build = node1_panel(self.association_run)
            if (str(prefix), build) != (self.association_inputs["input_prefix"], self.association_inputs["genome_build"]):
                raise ValueError("Selected Node1 run no longer matches the association inputs.")
        prepare = prepare_stepwise if self.step == "stepwise" else prepare_custom_conditional if self.step == "node3" else prepare_custom_association
        from hlagent.analysis.stepwise_firth import selection_arguments
        return prepare(**self.association_inputs, run_root=self.run_root,
                       seccomp_unconfined=self.seccomp_unconfined,
                       **(selection_arguments(self.stepwise_selection) if self.step == "stepwise" else {"condition_markers": self.condition_markers} if self.step == "node3" else {}))

    def collect_parent(self, text):
        name = text
        if text.isdigit() and 1 <= int(text) <= len(self.run_names):
            name = self.run_names[int(text) - 1]
        directory = self.run_root / name if name in self.run_names else Path(text).expanduser()
        try:
            prepare = prepare_meta if self.step == "node5" else prepare_conditional if self.step == "node3" else prepare_association
            plan = prepare(directory, run_root=self.run_root,
                           seccomp_unconfined=self.seccomp_unconfined)
        except (OSError, ValueError, RuntimeError) as error:
            return ChatReply(f"{self.parent_label} run check failed: {error} Enter a corrected name/directory, or type back.")
        self.parent_run = str(directory.resolve())
        self.input_field = None
        self.pending_plan = plan
        return self.review(plan)

    def prepare_plan(self):
        self.pending_plan = None
        if self.step is None:
            return self.choose_stage()
        if self.step == "node1" and self.marker_inputs is not None:
            if len(self.marker_inputs) != 5:
                return self.choose_custom_markers()
            if self.conversion_run is not None:
                if str(conversion_hped(self.conversion_run)) != self.marker_inputs["hped_file"]:
                    raise ValueError("Selected conversion no longer matches the HPED input.")
            plan = prepare_custom_markers(**self.marker_inputs, run_root=self.run_root,
                                          seccomp_unconfined=self.seccomp_unconfined)
        elif self.step in ("node2", "node3", "stepwise") and self.association_inputs is not None:
            if not all(key in self.association_inputs for key in ("input_prefix", "phenotype_file", "phenotype_name", "genome_build")):
                return self.choose_custom_association(self.step)
            if self.step == "node3" and self.condition_markers is None:
                self.input_field = "association_conditions"
                return ChatReply("Enter exact condition marker IDs separated by spaces.")
            plan = self.prepare_supplied_plan()
        elif self.step == "node5" and self.meta_inputs is not None:
            if len(self.meta_inputs) != 5:
                return self.choose_custom_meta()
            plan = prepare_custom_meta(**self.meta_inputs, run_root=self.run_root,
                                       seccomp_unconfined=self.seccomp_unconfined)
        elif self.step in ("node2", "node3", "node5"):
            if self.parent_run is None:
                return self.choose_parent(self.step)
            prepare = prepare_meta if self.step == "node5" else prepare_conditional if self.step == "node3" else prepare_association
            plan = prepare(self.parent_run, run_root=self.run_root,
                           seccomp_unconfined=self.seccomp_unconfined,
                           **({"condition_markers": self.condition_markers} if self.step == "node3" else {}))
        elif self.step == "node4" and self.omnibus_inputs is not None:
            if len(self.omnibus_inputs) != 5:
                return self.choose_custom_omnibus()
            if self.phasing_run is not None:
                phased, fam, build = completed_phasing_inputs(self.phasing_run)
                if (str(phased), str(fam), build) != (self.omnibus_inputs["phased_file"],
                        self.omnibus_inputs["fam_file"], self.omnibus_inputs["genome_build"]):
                    raise ValueError("Selected phasing run no longer matches the omnibus inputs.")
            plan = prepare_custom_omnibus(**self.omnibus_inputs, run_root=self.run_root,
                                          seccomp_unconfined=self.seccomp_unconfined)
        elif self.step == "node4":
            plan = prepare_omnibus(run_root=self.run_root, seccomp_unconfined=self.seccomp_unconfined)
        else:
            plan = prepare_example(run_root=self.run_root, seccomp_unconfined=self.seccomp_unconfined)
        self.input_field = None
        self.pending_plan = plan
        return self.review(plan)

    def review(self, plan):
        actions = []
        if plan.stepwise:
            actions.append('settings: change threshold, minimum MAC or scan limit')
        if plan.custom_association or plan.custom_conditional or plan.stepwise:
            actions.append('covariates: select or change adjustment columns')
        if plan.custom_markers:
            actions.append('edit: change one input while retaining the others')
        if self.step == 'node3':
            actions.append('conditions: choose other adjustment markers')
            actions.append('stepwise: switch to automatic selection and review a new plan')
        if self.association_inputs is not None or self.omnibus_inputs is not None:
            actions.append('phenotype: choose another phenotype file')
        return ChatReply("Status: waiting for approval. Analysis has not started.\n\n"
                         "Review the inputs and settings below. Enter y to start or n to discard.",
                         plan=plan, actions=tuple(actions))

    def choose_conditions(self):
        if self.step == "node3" and self.association_inputs is not None and all(
                key in self.association_inputs for key in ("input_prefix", "phenotype_file", "phenotype_name", "genome_build")):
            self.pending_plan = None
            self.input_field = "association_conditions"
            return ChatReply("Current condition marker(s): " + ", ".join(self.condition_markers or []) +
                             "\nEnter exact replacement marker IDs separated by spaces, or type back.")
        if self.step != "node3" or self.parent_run is None:
            return ChatReply("Select a Node2 run for Node3 first.")
        self.pending_plan = None
        self.input_field = "conditions"
        try:
            plan = prepare_conditional(self.parent_run, run_root=self.run_root,
                                       seccomp_unconfined=self.seccomp_unconfined,
                                       condition_markers=self.condition_markers)
        except (OSError, ValueError, RuntimeError) as error:
            return ChatReply(f"Condition marker check failed: {error}\nType back to select a valid parent.")
        return ChatReply("Current condition marker(s): " + ", ".join(plan.data["conditions"]) +
                         "\nEnter exact marker IDs separated by spaces. Enter keeps the current choice. "
                         "Type back or cancel to leave.")

    def collect_conditions(self, text):
        requested = text.split() if text else self.condition_markers
        try:
            plan = prepare_conditional(self.parent_run, run_root=self.run_root,
                                       seccomp_unconfined=self.seccomp_unconfined, condition_markers=requested)
        except (OSError, ValueError, RuntimeError) as error:
            return ChatReply(f"Condition marker check failed: {error}\nEnter corrected IDs, or type back.")
        self.condition_markers = requested
        self.input_field = None
        self.pending_plan = plan
        return self.review(plan)

    def respond(self, text):
        text = text.strip()
        lowered = text.lower()
        if self.closed or lowered in EXIT_WORDS:
            return self.end_session()
        if lowered in CANCEL_WORDS or lowered in BACK_WORDS or lowered in ("n", "no"):
            return self.leave()
        if lowered == 'prepare-dictionary':
            self.choose_stage()
            self.step, self.input_field = 'imgt2seq', 'imgt_source'
            return ChatReply('Prepare HATK dictionaries from a local IMGT/HLA source snapshot.\n'
                             'Input: local IMGT directory, or type download to obtain a release.\n'
                             'Output: hg18, two-field dictionaries for eight HLA genes.\n'
                             'The source version is checked from headers. Original files are preserved.')
        if self.input_field == 'imgt_download_version':
            from hlagent.analysis.imgt_source import download_request
            try:
                self.imgt_download = download_request(text)
            except ValueError as error:
                return ChatReply(str(error))
            self.input_field = 'imgt_download_approval'
            return ChatReply('Download IMGT/HLA source from ANHIG/IMGTHLA on GitHub.\n'
                             'Version: ' + self.imgt_download + '\n'
                             'A new local source directory will be created. Existing files are preserved.\n'
                             'Dictionary generation requires a separate reviewed plan and approval.\n'
                             'Enter y to download, n or back to return.')
        if self.input_field == 'imgt_download_approval':
            if lowered not in ('y', 'yes'):
                return ChatReply('Enter y to download, or back to return. No download was started.')
            from hlagent.analysis.imgt_source import download_source
            try:
                source = download_source(self.imgt_download, self.run_root / 'imgt-sources')
            except (OSError, ValueError, RuntimeError) as error:
                self.input_field = 'imgt_download_version'
                return ChatReply(f'Download failed: {error}\nChoose a version again or type back.')
            self.input_field = 'imgt_source'
            return self.respond(str(source))
        if self.input_field == 'imgt_source':
            if lowered == 'download':
                self.input_field = 'imgt_download_version'
                return ChatReply('Enter an IMGT release such as 3.32.0 or 3320, or latest.\n'
                                 'No network request is made until you approve the download.')
            if not text:
                return ChatReply('Enter the local IMGT source directory, or type back.')
            from hlagent.analysis.hatk import prepare_dictionary
            try:
                plan = prepare_dictionary(text, self.run_root, self.seccomp_unconfined)
            except (OSError, ValueError) as error:
                return ChatReply(f'Input check failed: {error}\nCorrect the source directory or type back.')
            self.input_field = None
            self.pending_plan = plan
            return self.review(plan)
        if (lowered == 'settings' and self.step == 'stepwise' and self.association_inputs
                and all(k in self.association_inputs for k in ('input_prefix', 'phenotype_file', 'phenotype_name', 'genome_build'))):
            self.pending_plan = None
            self.input_field = 'association_threshold'
            return self.stepwise_threshold_reply()
        if self.input_field in ('association_covar_file', 'association_covar_names'):
            return self.collect_covariates(text)
        if lowered == 'covariates':
            if self.step not in ('node2', 'node3', 'stepwise') or not self.association_inputs or not all(
                    key in self.association_inputs for key in ('phenotype_file', 'phenotype_name', 'genome_build')):
                return ChatReply('Select a custom Node2, single Node3 or stepwise panel and phenotype before adding covariates.\n'
                                 'Covariates are not supported in Node4.')
            self.pending_plan = None
            self.input_field = 'association_covar_file'
            return self.covariate_file_reply()
        if lowered == "phenotype":
            self.discard_plan()
            if self.step in ('node2', 'node3', 'stepwise') and self.association_inputs is not None and 'input_prefix' in self.association_inputs:
                inputs, field = self.association_inputs, 'association_pheno'
            elif self.step == 'node4' and self.omnibus_inputs is not None and 'fam_file' in self.omnibus_inputs:
                inputs, field = self.omnibus_inputs, 'omnibus_pheno'
            else:
                return ChatReply('Select the marker panel or phased AA/FAM inputs before choosing a phenotype file.')
            inputs.pop('phenotype_file', None)
            inputs.pop('phenotype_name', None)
            self.input_field = field
            return self.phenotype_reply()
        if lowered == "node5-custom" or (lowered == "custom" and self.step == "node5" and self.input_field == "parent_run"):
            return self.choose_custom_meta()
        if self.input_field and self.input_field.startswith("meta_"):
            if not text:
                return ChatReply('Enter the requested value.')
            return self.collect_custom_meta(text)
        if lowered == "node4-custom":
            return self.choose_custom_omnibus()
        if self.input_field and self.input_field.startswith("omnibus_"):
            if not text:
                return ChatReply('Enter the requested value.')
            return self.collect_custom_omnibus(text)
        if lowered == "node1-custom":
            return self.choose_custom_markers()
        if lowered == "edit" or lowered.startswith("edit "):
            return self.edit_markers(lowered[4:].strip())
        if self.input_field == "marker_edit":
            return self.edit_markers(lowered)
        if self.input_field and self.input_field.startswith("marker_"):
            if not text and self.input_field != "marker_snps":
                return ChatReply('Enter the requested value.')
            return self.collect_custom_markers(text)
        if lowered == "stepwise":
            if self.association_inputs and self.association_inputs.get('covariates'):
                inputs = dict(self.association_inputs)
                self.choose_custom_association('stepwise')
                self.association_inputs = inputs
                self.input_field = 'association_threshold'
                return self.stepwise_threshold_reply()
            return self.choose_custom_association("stepwise")
        if (lowered in ("node2-custom", "node3-custom") or
                (lowered == "custom" and self.step in ("node2", "node3") and self.input_field == "parent_run")):
            return self.choose_custom_association("node3" if lowered == "node3-custom" or lowered == "custom" and self.step == "node3" else "node2")
        if self.input_field and self.input_field.startswith("association_"):
            if not text and self.input_field not in ("association_threshold", "association_mac", "association_iterations"):
                return ChatReply('Enter the requested value.')
            return self.collect_custom_association(text)
        if self.input_field == "parent_run":
            if not text:
                return ChatReply(f"Enter a completed {self.parent_label} run, or type back.")
            return self.collect_parent(text)
        if self.input_field == "conditions":
            return self.collect_conditions(text)
        if lowered == "conditions":
            return self.choose_conditions()
        if lowered in ("y", "yes"):
            if self.pending_plan is None:
                return ChatReply("There is no plan to approve. Choose a HATK example first.")
            plan = self.pending_plan
            self.pending_plan = None
            try:
                progress = getattr(self, "execution_progress", None)
                self.last_result = run_example(plan, progress=progress) if progress else run_example(plan)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                self.last_result = {"status": "failed", "plan": plan.to_dict(), "error": str(error)}
            return ChatReply("Returned to Supervisor.", result=self.last_result, return_to_supervisor=True)
        if lowered in ("node1", "node2", "node3", "node4", "omnibus", "node5", "meta") or (self.input_field == "stage" and lowered in ("1", "2", "3", "4", "5")):
            if lowered in ("node2", "2", "node3", "3", "node5", "meta", "5"):
                return self.choose_parent("node5" if lowered in ("node5", "meta", "5") else "node3" if lowered in ("node3", "3") else "node2")
            self.marker_inputs = None
            self.omnibus_inputs = None
            self.meta_inputs = None
            self.step = "node4" if lowered in ("node4", "omnibus", "4") else "node1"
            self.parent_run = None
            return self.prepare_plan()
        if lowered in ("hatk", "finemapping", "fine-mapping", "example"):
            return self.choose_stage()
        if self.pending_plan is not None:
            review = self.review(self.pending_plan)
            if lowered in ('details', 'help'):
                return review
            return ChatReply('Unknown input. Enter y to start or n to discard. '
                             'Use details to inspect the plan or the commands below to change it.',
                             plan=self.pending_plan, actions=review.actions)
        if not text:
            return ChatReply('Choose node1/node2/node3/node4/node5, describe a HATK example request.')
        state = {"selected_step": self.step, "has_selected_parent": self.parent_run is not None,
                 "has_supplied_panel": self.association_inputs is not None,
                 "has_supplied_hped": self.marker_inputs is not None,
                 "has_supplied_phased_aa": self.omnibus_inputs is not None,
                 "has_supplied_studies": self.meta_inputs is not None,
                 "has_pending_plan": self.pending_plan is not None, "last_result": result_context(self.last_result)}
        self.pending_plan = None
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": "Current state: " + json.dumps(state)}]
        messages += self.history[-12:] + [{"role": "user", "content": text}]
        decision = self.client.decide(messages, DECISION_SCHEMA)
        validate_decision(decision, DECISION_SCHEMA)
        self.history.extend([{"role": "user", "content": text},
                             {"role": "assistant", "content": json.dumps(decision)}])
        self.history = self.history[-12:]
        action = decision["action"]
        if action == "node5_custom":
            return self.choose_custom_meta()
        if action == "node4_custom":
            return self.choose_custom_omnibus()
        if action == "node1_custom":
            return self.choose_custom_markers()
        if action in ("node1", "node4"):
            self.marker_inputs = None
            self.omnibus_inputs = None
            self.meta_inputs = None
            self.step = action
            self.parent_run = None
            return self.prepare_plan()
        if action in ("node2_custom", "node3_custom"):
            return self.choose_custom_association("node3" if action == "node3_custom" else "node2")
        if action in ("node2", "node3", "node5"):
            return self.choose_parent(action)
        if action == "conditions":
            return self.choose_conditions()
        if action == "plan":
            return self.prepare_plan()
        if action == "choose_stage":
            return self.choose_stage()
        if action in ("back", "cancel"):
            return self.leave()
        if action == "exit":
            return self.end_session()
        return ChatReply(decision["message"])
