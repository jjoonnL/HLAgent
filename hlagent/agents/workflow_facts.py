"""Input contracts checked by the current HATK preparation and validation code."""


WORKFLOW_FACTS = {
    'genotype_phasing': {
        'operation': 'Infer haplotype phase and missing genotypes with BEAGLE 3.3.2.',
        'input': 'A compatible chromosome 6 HATK marker PLINK panel, including AA markers.',
        'output': 'Full phased BGL, AA-only phased BGL and matching FAM.',
        'not_required_for': ['Node2 additive association', 'Node3 single conditional regression'],
    },
    'node4_omnibus': {
        'required_input_state': 'AA markers must already be phased when Node4 starts.',
        'required_files': ['Compatible AA-only phased BGL', 'Matching FAM', 'Named binary phenotype table'],
        'new_phasing': 'Needed to prepare missing phased input; unnecessary if compatible phased input already exists.',
        'validation': 'Phased sample-pair order, marker format and phenotype matching are checked locally.',
        'execution': 'Node4 itself does not run phasing; a new phasing job needs a separate reviewed approval.',
        'model_limit': 'Original HATK haplotype-row model; within-person dependence is not adjusted.',
    },
    'evidence_boundary': 'These are tool input contracts, not evidence that any user file has been validated.',
}
