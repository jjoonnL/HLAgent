"""Preview or run HATK marker generation, association, and visualization examples."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from hlagent.runtime.presentation import stage_title
from hlagent.analysis.hatk import check_environment, prepare_example, prepare_association, prepare_conditional, prepare_omnibus, prepare_meta, run_example, validate_results
from hlagent.analysis.hatk_association import validate_association_outputs
from hlagent.analysis.hatk_conditional import validate_conditional_outputs
from hlagent.analysis.hatk_omnibus import validate_omnibus_outputs
from hlagent.analysis.hatk_meta import validate_meta_outputs
from hlagent.analysis.hatk_custom_association import validate_custom_association_outputs
from hlagent.analysis.hatk_custom_conditional import validate_custom_conditional_outputs
from hlagent.analysis.hatk_custom_meta import validate_custom_meta_outputs
from hlagent.analysis.hatk_custom_omnibus import validate_custom_omnibus_outputs
from hlagent.analysis.hatk_custom_markers import validate_custom_marker_outputs
from hlagent.analysis.hatk_phasing import validate_phasing_outputs
from hlagent.analysis.hatk_stepwise import validate_stepwise_outputs


def show_plan(plan):
    if plan.dictionary:
        print('HATK dictionary preparation')
        print('\nInputs')
        print('Source: ' + plan.data['source_dir'])
        from hlagent.analysis.imgt_dictionary import release_name
        print('IMGT release: ' + release_name(plan.data['settings']['imgt']) + ' (source headers checked)')
        print('Compatibility: generation with this release requires checking the outputs.')
        print('Genome build: hg18')
        print('\nPreparation')
        print('Method: IMGT2Seq from the original HATK modules')
        print('Allele format: two-field')
        print('Genes: A, B, C, DPA1, DPB1, DQA1, DQB1, DRB1')
        print('Outputs: allele table, AA/DNA dictionaries and heatmap tables')
        print('Source files are copied into a new run. No download, imputation or association is performed.')
        print('\nBefore approval')
        print('Image: ' + plan.to_dict()['image'])
        if plan.seccomp_unconfined:
            print('Compatibility test: Docker syscall restrictions are disabled for this run only.')
        print('Results will be saved in: ' + str(plan.run_dir))
        return
    if plan.stepwise:
        counts, settings, options = plan.data['phenotype'], plan.data['settings'], plan.data['selection']
        print('Stepwise conditional association (HATK Node3)')
        print('\nInputs')
        print(f"Panel: {plan.data['panel']['prefix']}")
        print(f"Phenotype: {settings['phenotype_name']}. {counts['cases']} cases, {counts['controls']} controls")
        print(f"Genome build: hg{settings['genome_build']} (declared)")
        print('\nAnalysis')
        version = plan.to_dict()['stepwise_analysis_version']
        print('Model: PLINK 2 always-Firth additive logistic' if version == 3 else
              'Model: PLINK 1.9 additive logistic')
        print('Covariates: ' + (', '.join(settings.get('covariates', []))
                                or 'none. Type covariates to add age, sex or supplied PCs'))
        if settings.get('covariates'):
            print('Adjustment: the same selected covariates are included in every scan')
        if version == 3:
            print(f"Candidates: {options['initial_candidate_count']} of {options['input_marker_count']} pass initial MAC >= {options['min_mac']}")
            print(f"Minimum MAC: {options['min_mac']}. Rechecked on each conditional test's samples")
            print('Threshold rule: ' + ('Bonferroni 0.05 / initial eligible candidates, fixed across scans'
                                       if options['threshold_mode'] == 'bonferroni_initial_candidates' else 'user-supplied fixed P cutoff'))
        print(f"Selection: add the lowest-P estimable marker when P < {options['threshold']}")
        print(f"Maximum scans: {options['max_iterations']}. Ties are resolved by marker ID")
        print('Each scan adjusts for all previously selected markers. Raw scans and selection history are saved.')
        if version == 3:
            print('Low-MAC and PLINK screening exclusions are recorded. Unresolved eligible tests stop selection.')
            print('Uses PLINK Firth Wald P values. Selected markers do not establish causal independence or post-selection validity.')
        else:
            print('Exact structural exclusions are recorded. Unclassified failed tests stop selection before adding a marker.')
            print('Selected markers do not establish independent causal signals. The cutoff is not a multiple-testing correction.')
        print('\nBefore approval')
        print(f"Image: {plan.to_dict()['image']}")
        if plan.seccomp_unconfined:
            print('Compatibility test: Docker syscall restrictions are disabled for this run only.')
        print(f'Results will be saved in: {plan.run_dir}')
        return
    if plan.custom_meta:
        settings, summary = plan.data["settings"], plan.data["review_summary"]
        print("Meta-analysis (HATK Node5): supplied study association results")
        print("\nInputs")
        print(f"Input tables: verified ({len(plan.data['files'])})")
        print(f"Study 1: {plan.data['study1_file']}; {summary['study_marker_counts'][0]} markers")
        print(f"Study 2: {plan.data['study2_file']}; {summary['study_marker_counts'][1]} markers")
        print(f"Shared trait: {settings['phenotype_name']}; declared hg{settings['genome_build']}; compatible binary-trait log odds assumed")
        print("\nAnalysis")
        print("Steps: match marker IDs/coordinates, align effect alleles, fixed-effect IVW, Manhattan PNG/PDF")
        print(f"Eligible markers: {summary['meta_markers']}; excluded: {summary['excluded_markers']}; study 2 allele flips: {summary['flipped_markers']}")
        print("Exclusions: " + json.dumps(summary["exclusion_counts"], sort_keys=True))
        print("No strand inference. Ambiguous A/T and C/G SNPs are excluded. Raw estimates/exclusions are retained.")
        print("Sample nonoverlap and trait/build/effect compatibility are caller declarations, not verified from result tables.")
        print("No regression reruns, covariate adjustment, random-effects model or overlap correction.")
        print("\nBefore approval")
        print(f"Image: {plan.to_dict()['image']}")
        if plan.seccomp_unconfined:
            print("Compatibility test: Docker syscall restrictions are disabled for this run only.")
        print(f"Results will be saved in: {plan.run_dir}")
        return
    if plan.custom_omnibus:
        counts, settings = plan.data["phenotype"], plan.data["settings"]
        print("Amino-acid omnibus test (HATK Node4): supplied prephased markers")
        print("\nInputs")
        print(f"Input files: verified ({len(plan.data['files'])})")
        print(f"Phased input: {plan.data['phased_file']}")
        scope = plan.data.get('test_counts')
        if scope:
            print(f"Tests: {scope['aa_positions']} AA positions + {scope['insertion_tests']} insertions = {scope['total_tests']} total")
        else:
            print(f"AA positions: {plan.data['positions']}")
        if plan.data.get('phasing_source'):
            print(f"Source Phasing run: {plan.data['phasing_source']['source_run']}. Existing results are preserved")
        print(f"FAM: {plan.data['fam_file']}; unique IIDs; phased pair order checked")
        print(f"Phenotype: {settings['phenotype_name']}; {counts['cases']} cases; {counts['controls']} controls; matched by FID/IID")
        print(f"Coordinates: declared hg{settings['genome_build']}; build and phasing provenance are not independently verified")
        print("\nAnalysis")
        print("Steps: decode residues, original HATK R omnibus tests, Manhattan PNG/PDF")
        print(f"Model: phenotype repeated on both haplotypes; {counts['samples']} people, {counts['samples'] * 2} haplotype rows")
        print("Original-model compatibility analysis. Within-person haplotype dependence is not adjusted")
        print("No covariates, conditions, new phasing, or Node1-3 reruns. Not validated individual-level inference.")
        print("\nBefore approval")
        print(f"Image: {plan.to_dict()['image']}")
        if plan.seccomp_unconfined:
            print("Compatibility test: Docker syscall restrictions are disabled for this run only.")
        print(f"Results will be saved in: {plan.run_dir}")
        return
    if plan.custom_markers:
        settings = plan.data["settings"]
        print("HLA marker generation (HATK Node1): supplied HLA genotypes and dictionaries")
        print("\nInputs")
        print(f"Input files: verified ({len(plan.data['files'])})")
        print(f"HPED: {plan.data['hped_file']}; {plan.data['input_samples']} samples; headerless 22-column format")
        print("Gene order: A, B, C, DPA1, DPB1, DQA1, DQB1, DRB1 (two alleles per gene)")
        print(f"Dictionary: {plan.data['dictionary_dir']}; declared hg{settings['genome_build']}/IMGT {settings['imgt']}; two-field output")
        print("SNP panel: " + (plan.data['snp_prefix'] or "none. Only HLA-derived markers"))
        print("\nAnalysis")
        print("Steps: NomenCleaner, dictionary coverage report, bMarkerGenerator. No IMGT2Seq or association.")
        print("QC: mind 0.3; geno 0.5; MAF 0.0001; HWE 0.000001; founders only")
        print("QC may remove samples/markers. Dictionary build/version suitability is caller supplied.")
        print("Missing dictionary sequences use the original HATK missing-genotype behavior. Coverage is recorded.")
        print("\nBefore approval")
        print(f"Image: {plan.to_dict()['image']}")
        if plan.seccomp_unconfined:
            print("Compatibility test: Docker syscall restrictions are disabled for this run only.")
        print(f"Results will be saved in: {plan.run_dir}")
        return
    if plan.custom_association or plan.custom_conditional:
        counts, settings = plan.data["phenotype"], plan.data["settings"]
        print("Conditional association (HATK Node3): supplied marker panel" if plan.custom_conditional else "Marker association (HATK Node2): supplied marker panel")
        print("\nInputs")
        print(f"Input files: verified ({len(plan.data['files'])})")
        print(f"Panel: {plan.data['panel']['prefix']} (declared hg{settings['genome_build']})")
        print(f"Phenotype: {settings['phenotype_name']}; {counts['cases']} cases; {counts['controls']} controls; matched by FID/IID")
        print("\nAnalysis")
        print("Model: additive logistic regression. 1=control, 2=case")
        print("Covariates: " + (", ".join(settings.get('covariates', []))
                                or "none. Type covariates to add age, sex or supplied PCs"))
        if settings.get('covariates'):
            print(f"Covariate table: {plan.data['covariate_file']}")
            print("Covariates matched by FID/IID. Complete numeric values required for every panel sample.")
        if plan.custom_conditional:
            if settings.get("covariates"):
                print("The same covariates are used in the baseline and conditional scans.")
            print("Condition marker(s): " + ", ".join(plan.data["conditions"]))
            print("Steps: one fresh baseline scan and one conditional scan. Before/after comparison table")
            print("No automatic marker selection or repeated conditioning. No new plots, QC, or marker generation.")
            print("Missing condition genotypes can change sample counts. Conditional association is not causal independence.")
        else:
            print("Steps: association and Manhattan PNG/PDF. No additional QC, marker generation, or heatmaps.")
        print("Genome build and panel suitability are caller supplied, not independently verified.")
        print("\nBefore approval")
        print(f"Image: {plan.to_dict()['image']}")
        if plan.seccomp_unconfined:
            print("Compatibility test: Docker syscall restrictions are disabled for this run only.")
        print(f"Results will be saved in: {plan.run_dir}")
        return
    print(stage_title(plan.to_dict()["example"]) + " example")
    print("\nInputs")
    print(f"Example files: verified ({len(plan.data['files'])})")
    if plan.meta:
        print(f"Source marker association run (HATK Node2): {plan.parent_run}")
        print("Input: same RA example, divided into two nonoverlapping phenotype-stratified subsets")
        for index, counts in enumerate(plan.data["split_counts"], 1):
            print(f"Split {index}: {counts['cases']} cases; {counts['controls']} controls")
        print("\nAnalysis")
        print("Steps: two PLINK 1.9 association scans. Fixed-effect inverse-variance meta-analysis. Manhattan PNG/PDF")
        print("Same hg18 marker panel and effect-allele coding in both splits. No strand inference")
        print("Demonstration subsets of one cohort, not independent replication cohorts. Relatedness is not assessed.")
        print("No covariates, random-effects model, or cross-disease pooling. Full-cohort Node2 estimates are not pooled.")
        print("Node1-4 are not rerun. Input files are copied into a new run directory.")
    elif plan.omnibus:
        counts = plan.data["phenotype"]
        print(f"RA phenotype: {counts['cases']} cases; {counts['controls']} controls; matched by FID/IID")
        print("Input: bundled prephased AA markers. Sample-pair order checked against FAM")
        print("\nAnalysis")
        print("Steps: decode residues, original HATK R omnibus tests, Manhattan PNG/PDF")
        print("Model: phenotype repeated on both haplotypes; 600 people, 1200 haplotype rows")
        print("Original-model compatibility example. Within-person haplotype dependence is not adjusted")
        print("No covariates or conditions. No new phasing. Node1-3 are not rerun.")
    elif plan.parent_run:
        counts = plan.data["phenotype"]
        print(f"Source {stage_title('hatk_node2' if plan.conditional else 'hatk_node1')} run: {plan.parent_run}")
        print(f"RA phenotype: {counts['cases']} cases; {counts['controls']} controls; matched by FID/IID")
        print("\nAnalysis")
        print("Model: additive logistic regression; 1=control, 2=case. No covariates")
        if plan.conditional:
            print("Steps: one PLINK 1.9 conditional regression, then a before/after comparison table")
            print("Condition marker(s): " + ", ".join(plan.data["conditions"]))
            if plan.data["condition_selection"] == "suggested_baseline_lead":
                print("Suggestion: Node2's lowest-P estimable marker. Approval accepts this condition.")
            print("No automatic marker selection or repeated fitting. No new plots will be generated.")
            print("Unestimable tests are retained. This does not establish causal independence.")
        else:
            print("Steps: association, Manhattan plot, eight allele-by-AA-position heatmaps (PNG/PDF)")
        print("Node1 and Node2 are not rerun. Input files are copied into a new run directory." if plan.conditional
              else "Node1 is not rerun. Input files are copied into a new run directory.")
    else:
        print("Input: WTCCC HATK example; 600 samples. Hg18. Eight HLA genes")
        print("Dictionary: bundled IMGT 3.32.0, two-field. IMGT2Seq will not be rerun")
        print("\nAnalysis")
        print("Steps: NomenCleaner, then bMarkerGenerator (HLA, AA, intragenic and supplied SNP markers)")
        print("QC: mind 0.3; geno 0.5; MAF 0.0001; HWE 0.000001; founders only")
        print("QC may remove samples and markers. No association analysis will be run.")
    print("\nBefore approval")
    print(f"Image: {plan.to_dict()['image']}")
    if plan.seccomp_unconfined:
        print("Compatibility test: Docker syscall restrictions are disabled for this run only.")
    print(f"Results will be saved in: {plan.run_dir}")


def show_result_guidance(result):
    stage = result['plan'].get('example')
    summary = result.get('summary') or {}
    if stage in ('hatk_node2', 'hatk_node2_custom'):
        table = 'association.results.tsv' if stage == 'hatk_node2_custom' else 'RA.results.tsv'
        print(f'Read first: {table} contains marker IDs, effect alleles, OR, SE, P and NMISS (samples used per marker).')
        print('Use the Manhattan plot to locate signals, then inspect their table rows. OR refers to the reported A1 allele. P is not an effect size.')
        if all(key in summary for key in ('tested_markers', 'estimable_markers', 'unestimable_markers')):
            print(f"Marker estimates: {summary['estimable_markers']} of {summary['tested_markers']} available; {summary['unestimable_markers']} unestimable.")
        if summary.get('unestimable_markers', 0):
            print('Unestimable tests are not negative findings. Inspect missing statistics and the recorded exclusions before interpreting them.')
        if summary.get('minimum_p') is not None:
            print(f"Smallest reported P: {summary['minimum_p']}. Review multiple testing and model limitations before drawing conclusions.")
        print('Next options at Supervisor: node3-custom for one conditional scan with explicit marker IDs, or phasing to prepare an amino-acid omnibus analysis.')
        print('Both options need the original marker panel. Conditional analysis also needs a matching phenotype. Each task requires a new plan and approval.')
    elif stage in ('hatk_node4', 'hatk_node4_custom'):
        table = 'omnibus' if stage == 'hatk_node4_custom' else 'RA.omnibus'
        print(f'Read first: {table} tests amino-acid positions and any supplied insertion states. Inspect Variant, Residues, deltaDF and log10_P, then the Manhattan plot.')
        if 'insertion_tests' in summary:
            print(f"Scope: {summary['aa_positions']} AA positions + {summary['insertion_tests']} insertions = {summary['total_tests']} tests.")
        if all(key in summary for key in ('positions', 'estimable_positions', 'unestimable_positions')):
            print(f"Omnibus tests: {summary['estimable_positions']} of {summary['positions']} estimable; {summary['unestimable_positions']} unestimable.")
        if summary.get('unestimable_positions', 0):
            print('Unestimable positions are not negative findings. Inspect their missing statistics before interpreting them.')
        print('The table reports log10(P), not -log10(P): more negative values mean smaller P. The plot uses -log10(P). N counts haplotype rows, not independent people.')
        print('A position-level association does not identify a causal residue. Multiple testing and the unadjusted within-person haplotype dependence limit interpretation.')
        print('Next: inspect the tested positions and their listed residues alongside marker-level association results, if available. Use node2-custom with the original unphased panel and matching phenotype for marker-level tests.')
        print('Conditional omnibus is not supported in this workflow. No follow-up analysis starts automatically.')


def show_result(result):
    print(f"Status: {result['status']}")
    directory = Path(result["plan"]["run_dir"])
    if result.get("summary"):
        print(json.dumps(result["summary"], indent=2))
    if result["status"] == "incomplete" and result["plan"].get("example") == "hatk_stepwise":
        print("Selection is incomplete. A complete stopping conclusion was not established.")
        print(f"Selection history: {directory / 'output/stepwise.selection.tsv'}")
        print("Stop reason: " + result["summary"]["stop_reason"])
    if result["status"] == "completed":
        print("Structure checks passed. This does not assess biological accuracy.")
        if result['plan'].get('example') == 'hatk_imgt2seq':
            print(f'Dictionary directory: {directory / "output"}')
            print('Use node1-custom and select this dictionary from the recorded candidates.')
            print('Dictionary structure was checked. This does not establish biological accuracy.')
        elif result["plan"].get("example") == "hatk_stepwise":
            print(f"Selection history: {directory / 'output/stepwise.selection.tsv'}")
            print('Stop reason: ' + result['summary']['stop_reason'])
            print('Selected markers do not establish causal independence.')
            if result['summary'].get('analysis_version', 1) == 1:
                print('Legacy selection skipped unclassified tests. A reliable threshold stop is not established.')
        elif result["plan"].get("example") == "hatk_node5_custom":
            print(f"Meta-analysis table: {directory / 'output/meta.tsv'}")
            print(f"Meta-analysis plot: {directory / 'output/meta.manhattan.png'}")
            print("Study nonoverlap and effect compatibility are caller declarations, not independently verified.")
        elif result["plan"].get("example") == "hatk_node4_custom":
            print(f"Omnibus table: {directory / 'output/omnibus'}")
            print(f"Omnibus plot: {directory / 'output/omnibus.manhattan.png'}")
            print("Original HATK haplotype-row model. Within-person dependence is not adjusted.")
        elif result["plan"].get("example") == "hatk_node3_custom":
            print("One baseline scan and one supplied-condition scan completed.")
            print(f"Comparison table: {directory / 'output/conditional.comparison.tsv'}")
            print(f"Baseline results: {directory / 'output/association.assoc.logistic'}")
            print(f"Conditional results: {directory / 'output/conditional.assoc.logistic'}")
        elif result["plan"].get("example") == "hatk_node2_custom":
            print(f"Association table: {directory / 'output/association.results.tsv'}")
            print(f"Manhattan plot: {directory / 'output/association.manhattan.png'}")
        elif result["plan"].get("example") == "hatk_node5":
            print(f"Meta-analysis table: {directory / 'output/RA.meta.tsv'}")
            print(f"Meta-analysis plot: {directory / 'output/RA.meta.manhattan.png'}")
            print("Demonstration subsets of one cohort. Not independent replication studies.")
        elif result["plan"].get("example") == "hatk_node4":
            print(f"Omnibus table: {directory / 'output/RA.omnibus'}")
            print(f"Omnibus plot: {directory / 'output/RA.omnibus.manhattan.png'}")
        elif result["plan"].get("example") == "hatk_node3":
            summary = result["summary"]
            if summary.get("mode") == "single_conditional":
                print("One conditional regression completed. Inspect the before/after association table.")
                print(f"Comparison table: {directory / 'output/RA.comparison.tsv'}")
                print(f"Conditional results: {directory / 'output/RA.conditional.assoc.logistic'}")
            elif summary.get("stop_reason") == "threshold_not_met":
                print("Selection ended: no remaining estimable marker meets the entry threshold.")
            elif summary.get("stop_reason") == "numerical_regression_failures":
                print("Selection stopped: unresolved numerical results require review.")
            if summary.get("final_unestimable_markers", 0):
                print("Unestimable tests remain. Global convergence is not claimed.")
            if summary.get("final_unresolved_numerical_tests", 0):
                print(f"Unresolved numerical marker tests excluded: {summary['final_unresolved_numerical_tests']}")
            if summary.get("mode") != "single_conditional":
                print(f"Selection history: {directory / 'output/RA.selection.tsv'}")
            print(f"Conditional summary: {directory / 'output/RA.conditional.summary.json'}")
        elif result["plan"].get("parent_run"):
            print(f"Association table: {directory / 'output/RA.results.tsv'}")
            print(f"Plots and matrices: {directory / 'output'}")
        else:
            print(f"Marker panel prefix: {directory / 'output/markers'}")
        show_result_guidance(result)
    if result.get("error"):
        print(result["error"], file=sys.stderr)
    if (directory / "run.json").is_file():
        print(f"Run record: {directory / 'run.json'}")
    else:
        print(f"Run directory (if created): {directory}")
    return 0 if result["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Check the environment and request execution approval.")
    parser.add_argument("--check-environment", action="store_true", help="Check Python packages and PLINK without analysis.")
    parser.add_argument("--check-results", metavar="RUN_DIR", help="Recheck an existing run without modifying it.")
    parser.add_argument("--seccomp-unconfined", action="store_true",
                        help="Use the container-only seccomp compatibility override.")
    parents = parser.add_mutually_exclusive_group()
    parents.add_argument("--meta-from-run", metavar="NODE2_RUN_DIR",
                         help="Split a completed Node2 RA example into disjoint subsets for fixed-effect meta-analysis.")
    parents.add_argument("--omnibus", action="store_true",
                         help="Run the original-model omnibus example with bundled prephased AA markers.")
    parents.add_argument("--conditional-from-run", metavar="NODE2_RUN_DIR",
                         help="Run one conditional regression from a completed Node2 example.")
    parser.add_argument("--condition-marker", action="append", metavar="MARKER_ID",
                        help="Marker to adjust for. Repeat for several. Default: suggest the Node2 lead marker.")
    parents.add_argument("--from-run", metavar="NODE1_RUN_DIR",
                        help="Run association and plots from a completed Node1 example run.")
    args = parser.parse_args()
    if args.condition_marker and not args.conditional_from_run:
        parser.error("--condition-marker requires --conditional-from-run")
    try:
        if args.check_results:
            directory = Path(args.check_results)
            record = json.loads((directory / "run.json").read_text())
            stage = record.get("plan", {}).get("example")
            check = validate_stepwise_outputs if stage == "hatk_stepwise" else validate_phasing_outputs if stage == "hatk_phasing" else validate_custom_meta_outputs if stage == "hatk_node5_custom" else validate_custom_omnibus_outputs if stage == "hatk_node4_custom" else validate_custom_marker_outputs if stage == "hatk_node1_custom" else validate_custom_conditional_outputs if stage == "hatk_node3_custom" else validate_custom_association_outputs if stage == "hatk_node2_custom" else validate_meta_outputs if stage == "hatk_node5" else validate_omnibus_outputs if stage == "hatk_node4" else validate_conditional_outputs if stage == "hatk_node3" else validate_association_outputs if stage == "hatk_node2" else validate_results
            summary = check(directory)
            print(json.dumps(summary, indent=2))
            print("Structure checks passed. This does not assess biological accuracy.")
            if record.get('status') == 'completed' and stage in ('hatk_node2', 'hatk_node2_custom', 'hatk_node4', 'hatk_node4_custom'):
                print(f"Results directory: {directory / 'output'}")
                show_result_guidance(dict(record, summary=summary))
            return 0
        if args.check_environment:
            environment = check_environment(args.seccomp_unconfined)
            print(json.dumps({key: value for key, value in environment.items()
                              if key != "source_sha256"}, indent=2))
            return 0
        plan = (prepare_meta(args.meta_from_run, seccomp_unconfined=args.seccomp_unconfined) if args.meta_from_run else
                prepare_omnibus(seccomp_unconfined=args.seccomp_unconfined) if args.omnibus else
                prepare_conditional(args.conditional_from_run, seccomp_unconfined=args.seccomp_unconfined,
                                    condition_markers=args.condition_marker) if args.conditional_from_run else
                prepare_association(args.from_run, seccomp_unconfined=args.seccomp_unconfined) if args.from_run
                else prepare_example(seccomp_unconfined=args.seccomp_unconfined))
        show_plan(plan)
        if not args.run:
            print("Preview only. Add --run to check the environment and request approval.")
            return 0
        check_environment(args.seccomp_unconfined)
        stage = "Node5" if args.meta_from_run else "Node4" if args.omnibus else "Node3" if args.conditional_from_run else "Node2" if args.from_run else "Node1"
        if input(f"Start {stage_title('hatk_' + stage.lower())}? [y/n]: ").strip().lower() not in ("y", "yes"):
            print("Not started.")
            return 0
        print(f"Starting {stage_title('hatk_' + stage.lower())}. Log: {plan.run_dir / 'hatk.log'}", flush=True)
        result = run_example(plan)
        return show_result(result)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"Cannot run HATK example: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nNot started.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
