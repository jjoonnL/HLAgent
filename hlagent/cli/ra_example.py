"""Local review and approval for the fixed RA example workflow."""

from hlagent.analysis import hatk
from hlagent.runtime import ra_example
from hlagent.runtime.execution_feedback import ExecutionFeedback


def show_plan(plan, detailed=False):
    print('RA example: HLA genotypes to association results and an HTML report')
    print('\nInputs')
    print('Bundled WTCCC/HATK data: 600 people, 300 cases and 300 controls.')
    print('Genome build: hg18. Dictionary: IMGT 3320, two-field.')
    print('Phenotype: RA, matched by FID/IID. 1=control, 2=case.')
    print('\nApproved sequence')
    for index, step in enumerate(ra_example.STEPS, 1):
        print(f'{index}. {step}')
    print('5. Offline HTML report with tables and figures.')
    print('\nDefaults')
    qc = hatk.QC
    print(f"Marker generation QC: mind {qc['mind']}, geno {qc['geno']}, MAF {qc['maf_threshold']}, "
          f"HWE {qc['hwe_threshold']}. Founders only.")
    print('Marker association: additive logistic regression without additional covariates.')
    print('Phasing: Java memory 4g, seed 1, single thread. Missing genotypes are inferred.')
    print('Amino-acid omnibus test: original HATK statistical model.')
    print('This example starts from HLA genotypes. It does not run CookHLA,')
    print('conditional association (HATK Node3) or meta-analysis (HATK Node5).')
    if detailed:
        print('\nInterpretation limits')
        print('The omnibus model uses two haplotype rows per person with the same case/control label.')
        print('It does not account for the two rows coming from one person.')
        print('N counts haplotype rows, not independent people. The table reports log10(P).')
        print('File checks do not test whether HLA calls or haplotype assignments match a known truth.')
        print('They also do not test whether P values control false positives under a null model.')
        print('These limits are also described alongside the results in the HTML report.')
    print('\nOne approval starts all listed steps. A failed or cancelled step stops the workflow.')
    print('Completed runs and logs are preserved. Restarting requires a new plan and approval.')
    if plan.node1.seccomp_unconfined:
        print('Compatibility test: Docker syscall restrictions are disabled for these runs.')
    print(f'Analysis runs: {plan.node1.run_dir.parent}')
    print(f'Workflow record and report: {plan.directory}')


def start(ui, seccomp_unconfined=False):
    plan = ra_example.prepare_example(seccomp_unconfined=seccomp_unconfined)
    ui.render(show_plan, plan, title='Review example workflow',
              actions=('details: model and validation limits',), navigation='back / cancel: discard and return to Supervisor')
    while True:
        try:
            answer = ui.ask('Start the full RA example? [y/n]> ').strip().lower()
        except (KeyboardInterrupt, EOFError):
            ui.message('Example discarded. No analysis was started.', title='Supervisor')
            return
        if answer == 'details':
            ui.render(show_plan, plan, detailed=True, title='Example details')
            continue
        if answer in ('n', 'no', 'back', 'cancel', 'exit'):
            ui.message('Example discarded. No analysis was started.', title='Supervisor',
                       actions=('help: show commands',))
            return
        if answer in ('y', 'yes'):
            break
        ui.message('Enter y to approve every listed step, details for explanations, or n to discard.', title='Review example workflow')

    def execute(label, stage):
        ui.line('Starting ' + label + '. Log: ' + str(stage.run_dir / 'hatk.log'))
        with ExecutionFeedback(stage, ui=ui) as feedback:
            result = hatk.run_example(stage, progress=feedback.update)
        if result['status'] != 'completed':
            ui.render(feedback.show_recovery, result, title='Recovery')
        return result

    result = ra_example.run_example(plan, execute_stage=execute)
    lines = ['Status: ' + result['status']]
    if result['status'] == 'completed':
        lines += ['All four analysis stages and saved-result checks completed.',
                  'HTML report: ' + result['report']]
    else:
        lines += ['Stopped at: ' + result['current_stage'], result['error'],
                  'Completed stages are preserved. No later stage starts automatically.']
    lines += ['Workflow record: ' + str(plan.directory / 'workflow.json')]
    ui.result_details = '\n'.join(lines + [step['stage'] + ': ' + step['run_dir']
                                          for step in result.get('steps', [])])
    ui.plan_details = ui.input_details = None
    ui.message('\n'.join(lines), title='Example result', error=result['status'] != 'completed')
    ui.message('Returned to Supervisor.', title='Supervisor', actions=('help: show commands',))
    return result
