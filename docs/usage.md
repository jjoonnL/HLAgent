# Analysis guide

Use this guide when preparing your own data or reviewing analysis settings.
For installation and first launch, see [Getting Started](../README.md#getting-started).
For screen controls, see the [Web guide](web-ui.md).

## Run an analysis

1. At Supervisor, describe your goal or enter `hatk` to choose an HATK task.
2. Choose an example or supply your own inputs when prompted.
3. Review the inputs, settings and output directory in the plan.
4. Approve the plan to start, then follow the execution status and saved result path.

| Action | CLI | Web |
| --- | --- | --- |
| Show the full plan | Enter `details`. | Expand `Full plan information`. |
| Start the analysis | Enter `y` at the approval prompt. | Use the plan's approval button. |
| Edit inputs or settings | Use the options shown at the current prompt. | Use `Change inputs / settings` where available. |
| Discard a plan | Enter `n`, `back` or `cancel` at the plan prompt. | Use `Cancel plan`. |

Changing inputs or settings requires reviewing and approving the updated plan.
Typing `y` in Web chat does not approve an analysis.

## Choose a task

The commands below identify supplied-input routes. Enter `hatk` to choose an
example or a supplied-input route interactively. Node numbers refer to HATK
analysis tasks; you do not need to run all five in numeric order.

| Task | Command | Required inputs |
| --- | --- | --- |
| HLA imputation | `imputation` | SNP PLINK BED/BIM/FAM and compatible reference files |
| Marker generation | `node1-custom` | HPED and reference dictionaries; optional SNP panel |
| Marker association | `node2-custom` | Marker panel and binary phenotype |
| Conditional association | `node3-custom` | Marker panel, phenotype and conditioning marker IDs |
| Forward selection | `stepwise` | Marker panel and binary phenotype |
| Genotype phasing | `phasing` | Completed marker panel or supplied PLINK prefix |
| Amino-acid omnibus | `node4-custom` | Phased AA BGL, matching FAM and phenotype |
| Meta-analysis | `node5-custom` | Two compatible independent study summaries |
| Dictionary preparation | `prepare-dictionary` | Compatible IMGT snapshot or an approved download |

Use `convert` for supported HPED, phenotype and study-summary preparation.
It previews the proposed conversion and asks for approval before creating files.
Direct analysis scripts are also available under `scripts/analysis/`, each with `--help`.

## Prepare your inputs

Input paths refer to the computer running HLAgent. Web does not provide browser
file uploads. Where recorded inputs are offered, you can select an existing run;
HLAgent checks its files and recorded hashes before reuse.

### Marker panels

For PLINK inputs, supply the common prefix of the `.bed`, `.bim` and `.fam`
files when prompted. For example, use `/data/cohort/markers` for
`markers.bed`, `markers.bim` and `markers.fam` in that directory.

### HLA genotypes and dictionaries

Marker generation accepts a **headerless, 22-column HPED** file:

- Six PLINK pedigree fields: `FID IID father mother sex phenotype`.
- Two alleles per gene, in this order: `A B C DPA1 DPB1 DQA1 DQB1 DRB1`.

Choose a genome build and dictionary version compatible with your inputs.
The supported dictionary generator produces hg18, two-field dictionaries for
these eight genes. Structure checks do not establish biological correctness.

### Phenotype

Provide a table starting with `FID IID` and a named trait column.
Binary traits use **1 for controls and 2 for cases**. IDs must match the panel.

```text
FID IID RA
F1 S1 1
F2 S2 2
```

### Covariates

Node2, single-scan Node3 and stepwise accept optional numeric covariates.
Use extra columns in the phenotype table or a separate table starting with
`FID IID`, then select the columns to include.

```text
FID IID age sex_encoded PC1
F1 S1 45 0 -0.012
F2 S2 52 1 0.008
```

- Every panel sample must have complete values for the selected covariates.
- Categorical variables must already be explicitly encoded.
- Age, sex and PC column suggestions are hints; they are not selected automatically.
- HLAgent does not perform automatic PCA, recoding or sample removal.

## Review stepwise settings

Before approval, use the displayed settings option to review or change:

| Setting | Default |
| --- | --- |
| Minimum MAC | 20 |
| Selection threshold | Bonferroni cutoff based on the initial eligible candidates, fixed across scans |
| Maximum scans | 100 |
| Model | PLINK2 always-Firth additive logistic regression |

Each scan includes previously selected markers and any specified covariates.
Unresolved eligible tests stop selection with an incomplete result.

## Try the bundled RA workflow

The example cohort and dictionaries are stored in the HATK image and copied to
the ignored `datasets/examples/hatk/` cache after hash checks on first use.
No separate data preparation or dictionary generation is needed for this example.

Enter `example` at Supervisor or choose `Review the full RA example` in Web.
The example uses 600 people (300 cases and 300 controls), hg18 inputs and
IMGT 3.32.0 dictionaries. One reviewed approval covers:

Marker generation → marker association → genotype phasing → AA omnibus → HTML report

CookHLA, conditional association and meta-analysis are separate tasks.
A failed or cancelled stage stops the sequence and preserves saved stages;
the workflow does not automatically resume its remaining stages.

## Monitor or stop execution

Follow the reported stages and tool-log updates. For supported Docker analyses:

- **CLI:** press `Ctrl+C` to request cancellation.
- **Web:** select `Stop analysis` and confirm.

Check the recorded cancellation outcome before starting another execution.
Closing the browser does not request cancellation.

## Find and reuse saved results

Outputs are saved under `runs/`. New directory names include a UTC timestamp,
for example `association-20261118-093000Z`; `Z` means UTC.

| Command | What it does |
| --- | --- |
| `next` | Reuses a supported completed result as input to a newly reviewed task. |
| `resume` | Rechecks a saved individual task plan and prepares a new execution with fresh approval. |
| `report` | Collects saved analyses into an HTML report. |

Reports preview figures and provide PDFs and result tables. They use saved files
without rerunning analyses or calling an LLM. In Web, open `Results & reports`;
see the [Web guide](web-ui.md#results-and-reports) for its controls.

For a report from an explicitly selected run, you can also use:

```bash
python report.py runs/RUN_NAME --output runs/reports/REPORT_NAME
```

Deleting an analysis is permanent and may break downstream references.
Existing standalone reports can retain copied artifacts. Deleting a Web session
removes its conversation, not its saved analysis files.

## Analysis models

| Analysis | Model |
| --- | --- |
| Node2 and single-scan Node3 | Binary additive logistic regression with optional encoded numeric covariates |
| Stepwise | PLINK2 always-Firth selection with optional encoded numeric covariates |
| Node4 | Original HATK haplotype-row model, without adjustment for within-person dependence |
| Node5 | Inverse-variance-weighted fixed-effect meta-analysis of two compatible study summaries |

Selected stepwise markers do not establish causal independence
or post-selection validity. Interpret results in the context of the study design.
