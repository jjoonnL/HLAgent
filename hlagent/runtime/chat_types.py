"""Messages shared by the supervisor and analysis agent."""

from dataclasses import dataclass
from typing import Optional, Union

from hlagent.analysis.cookhla import RunPlan, RunResult
from hlagent.analysis.hatk import Node1Plan


EXIT_WORDS = {"exit", "quit", "q", "/exit", "종료"}
CANCEL_WORDS = {"cancel", "/cancel", "취소"}
BACK_WORDS = {"back", "/back", "뒤로"}


@dataclass
class ChatReply:
    message: str
    plan: Optional[Union[RunPlan, Node1Plan, dict]] = None
    result: Optional[Union[RunResult, dict]] = None
    end_session: bool = False
    return_to_supervisor: bool = False
    configure: bool = False
    actions: tuple[str, ...] = ()
    navigation: str = ""
    handoff: str = ""


def validate_decision(decision, schema):
    if not isinstance(decision, dict) or set(decision) != set(schema["required"]):
        raise ValueError("The model returned unsupported fields. No analysis was started.")
    if (not isinstance(decision["action"], str)
            or decision["action"] not in schema["properties"]["action"]["enum"]
            or not isinstance(decision["message"], str)):
        raise ValueError("The model returned an invalid action. No analysis was started.")


def result_context(result):
    """Share aggregate results with the model, excluding sample IDs and raw errors."""
    if result is None:
        return None
    summary = (result.get("summary") if isinstance(result, dict) else result.summary) or {}
    keys = ("valid", "input_samples", "output_samples", "result_rows", "expected_genes", "gene_counts",
            "samples", "predicted_genes", "absent_genes_filled_with_zero", "missing_allele_calls_by_gene",
            "phased", "confidence_filter_applied", "aa_markers", "imputed_missing_genotypes",
            "adjusted_phasing_coordinates", "engine", "observed_genotypes_preserved",
            "original_coordinates_retained_in_marker_ids", "controls", "cases", "tested_markers", "estimable_markers", "unestimable_markers",
            "zero_pvalues", "minimum_p", "maximum_nmiss", "removed_samples", "markers", "marker_classes",
            "iterations", "threshold", "max_iterations", "stop_reason", "converged",
            "selection_status", "unresolved_marker_count", "assessment",
            "min_mac", "threshold_mode", "initial_candidate_count", "initial_excluded_by_mac", "final_below_min_mac", "final_screening_exclusions",
            "regression_engine", "final_unestimable_markers", "convergence_scope",
            "final_unresolved_numerical_tests", "selection_scope", "mode", "condition_count", "conditional_passes", "baseline_passes",
            "baseline_counts", "haplotype_rows", "positions", "aa_positions", "insertion_tests", "total_tests", "estimable_positions",
            "unestimable_positions", "flipped_markers", "study_marker_counts", "effect_scale", "sample_overlap_assessment", "trait_build_effect_compatibility", "regression_rerun", "minimum_log10_p", "within_person_dependence_adjusted", "new_phasing", "model", "study_count", "sample_overlap", "split_method",
            "split_counts", "split_association_counts", "meta_markers", "excluded_markers", "exclusion_counts", "phenotype_name", "genome_build", "covariates", "heatmaps", "imgt", "field_format", "merged_supplied_snps", "qc", "dictionary_coverage")
    aggregate = {key: summary[key] for key in keys if key in summary}
    if isinstance(summary.get("selected_markers"), list):
        aggregate["selected_marker_count"] = len(summary["selected_markers"])
    return {"status": result.get("status") if isinstance(result, dict) else result.status,
            "returncode": result.get("returncode") if isinstance(result, dict) else result.returncode,
            "summary": aggregate}
