"""Streamlit presentation only. All task decisions and fits use the existing backend."""

import argparse
from html import escape
from pathlib import Path
import re
from time import monotonic

import streamlit as st
import streamlit.components.v1 as components

from hlagent.runtime.execution_feedback import STAGES
from hlagent.runtime.html_report import inspect_run
from hlagent.cli.terminal import LOGO
from hlagent.web import artifacts
from hlagent.web.session import WebSession
from hlagent.web.settings import MODEL_SUGGESTIONS
from hlagent.web.presentation import conversation_expanded, instructions, message
from hlagent.web.registry import SessionRegistry


ROLE_NAMES = {'supervisor': 'Supervisor', 'finemapping': 'HLA Fine-mapping', 'imputation': 'HLA Imputation',
              'conversion': 'File Converter', 'phasing': 'Genotype Phasing', 'tutor': 'HLA Tutor'}


@st.cache_resource(show_spinner=False)
def session_registry():
    return SessionRegistry()


def submit(ui, operation='message', **values):
    ui.submit({'operation': operation, **values})
    st.rerun()


def task_menu(ui):
    with st.expander('Choose a task', expanded=len(ui.history) < 3):
        columns = st.columns(3)
        for i, (label, command) in enumerate((('HLA imputation', 'imputation'), ('HLA fine-mapping', 'hatk'),
                                             ('Genotype phasing', 'phasing'), ('Prepare inputs', 'convert'),
                                             ('HLA Tutor', 'tutor'), ('Guided workflow', 'workflow'))):
            if columns[i % 3].button(label, use_container_width=True, disabled=ui.busy):
                submit(ui, text=command)
        if columns[0].button('Review the full RA example', use_container_width=True, disabled=ui.busy):
            submit(ui, text='example')


def hatk_menu(ui):
    st.subheader('Choose a HATK analysis')
    analyses = (
        ('HLA marker generation', 'node1', 'Create HLA, amino-acid and DNA markers from HLA genotypes and dictionaries.'),
        ('Marker association', 'node2', 'Test marker associations with a phenotype. Covariates can be added at plan review.'),
        ('Conditional association', 'node3', 'Adjust for specified markers in one scan, or use stepwise for automatic marker selection.'),
        ('Amino-acid omnibus test', 'node4', 'Test amino-acid residues jointly at each position using phased data.'),
        ('Meta-analysis', 'node5', 'Combine association summaries from two studies.'),
    )
    key = 'hatk_analysis_' + ui.root.name
    selected = st.session_state.get(key)
    columns = st.columns(2)
    for index, (label, command, _) in enumerate(analyses):
        if columns[index % 2].button(label, use_container_width=True, disabled=ui.busy,
                                     type='primary' if selected == command else 'secondary'):
            st.session_state[key] = command
            st.rerun()
    if selected is None:
        st.write('Choose an analysis to see its inputs and available routes.')
        return
    label, command, description = next(item for item in analyses if item[1] == selected)
    with st.container(border=True):
        st.markdown('**' + label + '**')
        st.write(description)
        columns = st.columns(2)
        if columns[0].button('Use example data', use_container_width=True, disabled=ui.busy):
            submit(ui, text=command)
        if columns[1].button('Use your data', use_container_width=True, disabled=ui.busy):
            submit(ui, text=command + '-custom')
        if command == 'node3':
            st.write('Stepwise repeats conditional scans and selects adjustment markers. Optional covariates stay fixed across scans.')
            if st.button('Set up stepwise analysis', disabled=ui.busy):
                submit(ui, text='stepwise')
        if command == 'node1' and st.button('Prepare a dictionary', disabled=ui.busy):
            submit(ui, text='prepare-dictionary')


def tutor_modes(ui, state):
    st.subheader('Answer sources')
    st.write('**Current mode:** ' + state['tutor_mode'])
    st.write('local and rag need no Tavily key. web and hybrid require a separate Tavily API key.')
    ready = state['settings_available']['tavily']
    st.write('**Tavily:** ' + ('configured' if ready else 'not configured. Add a key below.'))
    columns = st.columns(4)
    sources = ('HLAgent usage and setup', 'Your indexed papers and documents',
               'Web search with Tavily', 'Indexed documents + Web search')
    for column, mode, description in zip(columns, ('local', 'rag', 'web', 'hybrid'), sources):
        if column.button(mode, type='primary' if state['tutor_mode'] == mode else 'secondary', disabled=ui.busy or mode in ('web', 'hybrid') and not ready, use_container_width=True):
            submit(ui, text=mode)
        column.write(description)
    search_settings(ui, state)


def plan_card(ui, state):
    st.subheader('Review plan')
    st.markdown(instructions(state['plan_text']))
    labels = {'example': 'Approve all listed steps', 'analysis': 'Approve and run',
              'download': 'Approve source download', 'report': 'Create HTML report'}
    cols = st.columns(3)
    if cols[0].button(labels[state['approval_kind']], type='primary', use_container_width=True, disabled=ui.busy):
        submit(ui, 'approve', token=state['approval_token'])
    if cols[1].button('Cancel plan', use_container_width=True, disabled=ui.busy):
        submit(ui, text='cancel')
    if state['approval_kind'] == 'analysis':
        with cols[2].popover('Change inputs / settings', use_container_width=True, disabled=ui.busy):
            st.caption('Changes require a new plan and approval. Use the current task\'s supported commands.')
            if state.get('resources'):
                with st.form('cookhla_settings'):
                    memory = st.text_input('Java memory', value=state['resources']['memory'])
                    processes = st.number_input('Processes', min_value=1, value=state['resources']['processes'], step=1)
                    if st.form_submit_button('Review updated settings'):
                        submit(ui, 'resources', memory=memory, processes=int(processes))
            with st.form('edit_plan'):
                change = st.text_input('Setting change or input command', placeholder='settings, inputs, phenotype or covariates')
                if st.form_submit_button('Apply change'):
                    submit(ui, text=change)

    with st.expander('Full plan information'):
        st.text(state['plan_details'])


def chat(ui, state):
    st.html('<pre role="heading" aria-level="1" aria-label="HLAgent" '
            'style="font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
            'font-size:clamp(7px,1.4vw,16px);line-height:1.1;white-space:pre;'
            'color:#29b8db;margin:0 0 1.25rem;overflow-x:auto">' + escape(LOGO) + '</pre>')
    st.write('Current role: **' + ROLE_NAMES[state['role']] + '**')
    if state['closed']:
        st.info('Session ended. Start a new session from the sidebar.')
        return
    if state['role'] == 'supervisor' and not state['approval_kind']:
        task_menu(ui)
    if state['role'] == 'tutor':
        tutor_modes(ui, state)
    with st.expander('Conversation', expanded=conversation_expanded(state)):
        lines = sum(max(1, len(row['content']) // 90 + row['content'].count('\n') + 1) + 3 for row in ui.history)
        height = min(420, max(140, 32 + lines * 24))
        with st.container(height=height, key='conversation_' + ui.root.name):
            for row in ui.history:
                with st.chat_message(row['role']):
                    if row['role'] == 'assistant':
                        st.caption(ROLE_NAMES.get(row.get('agent'), 'Supervisor'))
                    st.markdown(message(row['content'], row.get('agent')) if row['role'] == 'assistant' else row['content'])
    if state['approval_kind']:
        plan_card(ui, state)
    else:
        if state['role'] == 'finemapping' and state['field'] == 'stage':
            hatk_menu(ui)
        else:
            if state['field'] is not None:
                st.markdown(message(state['message'], state['role']))
            choices = state.get('choices', [])
            if choices:
                selected = st.selectbox('Recorded choices', range(len(choices)), index=None,
                                        format_func=lambda i: choices[i]['label'], placeholder='Choose an entry', disabled=ui.busy)
                if st.button('Use selected entry', disabled=ui.busy or selected is None):
                    submit(ui, text=choices[selected]['value'])
            label = state['prompt'].removesuffix('> ').strip()
            if state['field'] is not None:
                st.caption('Paths refer to files on the computer running HLAgent. Submit each field for backend validation.')
            with st.form('current_input_' + str(state['revision']), clear_on_submit=True):
                value = st.text_input(label or 'Message', placeholder='Enter the requested value' if state['field'] else 'Describe a task or ask a question', disabled=ui.busy)
                if st.form_submit_button('Submit', disabled=ui.busy):
                    submit(ui, text=value)
        can_back = state['role'] != 'supervisor' or state['prompt'] != 'Supervisor> '
        if can_back or state.get('selection_details'):
            col1, col2, _ = st.columns([2, 2, 4])
            if can_back:
                col1.button('Back to Supervisor', use_container_width=True, disabled=ui.busy,
                            on_click=ui.submit, args=({'operation': 'message', 'text': 'back'},))
            if state.get('selection_details'):
                with col2.popover('Selection details', use_container_width=True):
                    st.text(state['selection_details'])
    running_status(ui)
    if state.get('result'):
        result = state['result']
        st.divider()
        st.subheader('Latest result')
        st.text(result['text'])
        if result['metrics']:
            st.table([{'Metric': key.replace('_', ' '), 'Value': str(value)} for key, value in result['metrics'].items()])
        report = result.get('report')
        has_report = bool(report and Path(report).parent in artifacts.reports(ui.root))
        full_example = result.get('kind') == 'example' and result['status'] == 'completed' and has_report
        columns = st.columns(2)
        if not full_example:
            columns[0].button('View results', use_container_width=True, disabled=ui.busy,
                              on_click=open_result_page, args=(ui, 'Analysis files'))
        if has_report:
            columns[0 if full_example else 1].button('View report', use_container_width=True, disabled=ui.busy,
                                                    type='primary' if full_example else 'secondary',
                                                    on_click=open_result_page, args=(ui, 'View reports'))
        if result.get('details'):
            with st.expander('Result details'):
                st.code(result['details'], language='json', height=320)


def open_result_page(ui, mode):
    st.session_state['page'] = 'Results & reports'
    st.session_state['workspace'].page = 'Results & reports'
    st.session_state['results_view_' + ui.root.name] = mode
    result = ui.state.get('result') or {}
    if mode == 'View reports' and result.get('report'):
        target = Path(result['report']).parent
        if target in artifacts.reports(ui.root):
            st.session_state['report_selection_' + ui.root.name] = target
    elif mode == 'Analysis files' and result.get('run_dir'):
        target = Path(result['run_dir'])
        if target in [path for path, _ in artifacts.saved_runs(ui.root)]:
            st.session_state['analysis_selection_' + ui.root.name] = target


def open_html_view(key):
    st.session_state[key] = 'View reports'


def result_browser(ui):
    st.title('Results & reports')
    key = 'results_view_' + ui.root.name
    if key not in st.session_state:
        st.session_state[key] = 'Create report' if ui.state and ui.state['approval_kind'] == 'report' else 'View reports'
    mode = st.radio('Report actions', ('View reports', 'Create report', 'Analysis files'), horizontal=True, key=key)
    pending_report = bool(ui.state and ui.state['approval_kind'] == 'report')
    shared = st.checkbox('Include CLI results and reports', disabled=pending_report,
                         help='Also list results and reports saved by the CLI on this computer. Cancel a pending report plan to change this filter.')
    previous = st.checkbox('Include previous Web sessions', value=True, key='report_history_' + ui.root.name,
                           disabled=pending_report, help='Include saved results from earlier Web sessions. Cancel a pending report plan to change this filter.')
    if mode == 'View reports':
        st.subheader('Saved HTML reports')
        st.write('Open or download a report that has already been created.')
        view_html_reports(ui, shared, previous)
    elif mode == 'Create report':
        st.subheader('Create an HTML report')
        st.write('Select analyses, review the plan, then confirm report creation.')
        if ui.state and ui.state['approval_kind'] == 'report':
            plan_card(ui, ui.state)
            return
        if ui.state and ui.state['message'].startswith('HTML report: '):
            st.success('HTML report created.')
            st.button('View created report', on_click=open_html_view, args=(key,))
        runs = artifacts.saved_runs(ui.root, shared, include_web=previous)
        if not runs:
            st.info('No saved analyses in this selection.')
            return
        indices = st.multiselect('Analyses to include', range(len(runs)), format_func=lambda i: runs[i][1])
        if st.button('Review selection', disabled=not indices or ui.busy):
            submit(ui, 'prepare_report', runs=[str(runs[i][0]) for i in indices])
    else:
        st.subheader('Analysis files')
        analysis_files(ui, shared, previous)


def confirm_report_deletion(ui, report, fingerprint, shared, previous):
    try:
        ui.poll()
        if ui.busy:
            raise ValueError('Wait for the current task to finish before deleting a report.')
        artifacts.delete_report(ui.root, report, fingerprint, shared, previous)
        st.session_state['report_delete_notice'] = 'Report deleted. Original analysis results and logs were kept.'
    except (OSError, ValueError) as error:
        st.session_state['report_delete_notice'] = str(error)
    st.session_state['report_delete_done'] = True


@st.dialog('Delete report?')
def delete_report_dialog(ui, report, label, fingerprint, shared, previous):
    if st.session_state.pop('report_delete_done', False):
        st.rerun()
    st.write(label)
    st.write('Delete this report and its attached files? Original analysis results and logs will be kept.')
    columns = st.columns(2)
    columns[0].button('Delete this report', type='primary', use_container_width=True,
                      on_click=confirm_report_deletion, args=(ui, report, fingerprint, shared, previous))
    if columns[1].button('Keep report', use_container_width=True):
        st.rerun()


def view_html_reports(ui, shared, previous=False):
    if st.session_state.get('report_delete_notice'):
        st.info(st.session_state.pop('report_delete_notice'))
    directories = artifacts.reports(ui.root, shared, include_web=previous)
    if directories:
        key = 'report_selection_' + ui.root.name
        if st.session_state.get(key) not in directories:
            st.session_state.pop(key, None)
        labels = artifacts.report_labels(directories, ui.root)
        columns = st.columns([4, 1], vertical_alignment='bottom')
        report = columns[0].selectbox('HTML report', directories, key=key, format_func=labels.get)
        if columns[1].button('Delete report', use_container_width=True, disabled=ui.busy):
            st.session_state.pop('report_delete_done', None)
            delete_report_dialog(ui, report, labels[report], artifacts.report_fingerprint(report), shared, previous)
        preview = artifacts.preview_report(report)
        preview += '<style>@media(min-width:601px){main{max-width:1200px;padding-left:24px;padding-right:24px}}</style>'
        components.html(preview, height=1000, scrolling=True)
        st.caption('Local source links are disabled in this preview. The ZIP includes the original report and its assets.')
        st.download_button('Download complete report ZIP', artifacts.report_zip(report), report.parent.name + '-report.zip')

    else:
        st.info('No saved HTML reports in this selection. Choose Create report to make one.')


def confirm_analysis_deletion(ui, run, fingerprint, shared, previous):
    try:
        ui.poll()
        if ui.busy:
            raise ValueError('Wait for the current task to finish before deleting an analysis.')
        artifacts.delete_analysis(ui.root, run, fingerprint, shared, previous)
        st.session_state['analysis_delete_notice'] = 'Analysis deleted. Other analyses and saved reports were kept.'
    except (OSError, ValueError) as error:
        st.session_state['analysis_delete_notice'] = str(error)
    st.session_state['analysis_delete_done'] = True


@st.dialog('Delete analysis?')
def delete_analysis_dialog(ui, run, label, fingerprint, shared, previous):
    if st.session_state.pop('analysis_delete_done', False):
        st.rerun()
    st.write(label)
    st.code(str(run), language=None)
    st.warning('Permanently delete this analysis folder, including inputs, results and logs? '
               'Deleted results cannot be reused or included in new reports. '
               'Tasks that reference these files may need new inputs. '
               'Existing reports and other analysis folders will be kept. This cannot be undone.')
    columns = st.columns(2)
    columns[0].button('Delete this analysis', type='primary', use_container_width=True,
                      on_click=confirm_analysis_deletion, args=(ui, run, fingerprint, shared, previous))
    if columns[1].button('Keep analysis', use_container_width=True):
        st.rerun()


def analysis_files(ui, shared, previous=False):
    if st.session_state.get('analysis_delete_notice'):
        st.info(st.session_state.pop('analysis_delete_notice'))
    runs = artifacts.saved_runs(ui.root, shared, include_web=previous)
    if not runs:
        st.info('No saved analyses in this selection.')
        return
    labels = dict(runs)
    key = 'analysis_selection_' + ui.root.name
    if st.session_state.get(key) not in labels:
        st.session_state.pop(key, None)
    columns = st.columns([4, 1], vertical_alignment='bottom')
    run = columns[0].selectbox('Analysis run', list(labels), key=key, format_func=labels.get)
    if columns[1].button('Delete analysis', use_container_width=True, disabled=ui.busy):
        st.session_state.pop('analysis_delete_done', None)
        delete_analysis_dialog(ui, run, labels[run], artifacts.analysis_fingerprint(run), shared, previous)
    if st.button('Use this run at the current task input', disabled=ui.busy):
        st.session_state['open_chat'] = True
        submit(ui, text=str(run))
    st.caption(str(run))
    with st.expander('Check saved results'):
        if st.button('Recheck selected run'):
            checked = inspect_run(run)
            st.write('Saved-result checks: ' + ('passed' if checked['checked'] else 'not passed'))
            if checked['issues']:
                st.text('\n'.join(checked['issues']))
    with st.expander('Tool logs'):
        st.code(artifacts.log_tail(run), language=None)
    files = artifacts.result_files(run)
    if files:
        path = st.selectbox('Result file', files, format_func=lambda p: p.name)
        raw = artifacts.read_file(run, path)
        if path.suffix == '.png':
            st.image(raw)
        elif path.suffix == '.pdf':
            png = path.with_suffix('.png')
            if png in files:
                st.image(artifacts.read_file(run, png))
                st.caption('PNG preview of this figure. Download the original PDF below.')
            else:
                st.info('Download this PDF to view it in your PDF reader.')
        else:
            st.code(raw[:65536].decode('utf-8', errors='replace'), language=None)
            st.caption('Preview shows at most 64 KB. Download the file for all rows.')
        st.download_button('Download result file', raw, path.name)


@st.fragment(run_every=1)
def session_status(ui):
    with st.container(key='current_step', gap='small'):
        state = ui.state
        st.markdown('**Current step**')
        if state is None:
            st.info('Starting session')
            return
        st.write(ROLE_NAMES[state['role']])
        if ui.busy:
            st.info('Running task' if ui.progress else 'Processing request')
            if ui.progress:
                stage = ui.progress[-1]['stage']
                st.caption(STAGES.get(stage, stage))
        elif ui.error:
            st.warning('Needs attention')
        elif state['closed']:
            st.info('Session ended')
        elif state['approval_kind']:
            st.info('Waiting for approval')
            st.caption('Review the plan in Chat & analysis.')
        elif state['role'] == 'tutor' and state['field'] is None:
            st.info('Questions and answers')
        elif state['field'] is not None or state['prompt'] != 'Supervisor> ':
            st.info('Providing inputs and settings')
            st.caption('Choose an analysis and data source.'
                       if state['role'] == 'finemapping' and state['field'] == 'stage'
                       else state['prompt'].removesuffix('> ').strip())
        elif state.get('result'):
            st.info('Result available')
            st.caption('Latest result: ' + state['result']['status'])
        else:
            st.info('Choosing a task')
    with st.container(key='current_model'):
        st.markdown('**Current model**  \n' + state['provider'] + '  \n' + state['model'])


def apply_web_settings(ui, operation, secret_field, save, provider=None, model_field=None):
    key = st.session_state.get(secret_field, '') if secret_field else ''
    if secret_field:
        st.session_state[secret_field] = ''
    request = {'operation': operation, 'api_key': key, 'save': save}
    if provider is not None:
        request.update(provider=provider, model=st.session_state[model_field])
    ui.submit(request)


def model_settings(ui, state):
    with st.expander('Model settings', expanded=isinstance(state, dict) and not state['settings_available'].get(state['provider'], True)):
        if not state:
            return
        identity = f"{ui.root}_{state['provider']}_{state['model']}"
        providers = tuple(MODEL_SUGGESTIONS)
        provider = st.selectbox('Provider', providers, index=providers.index(state['provider']),
                                key='provider_' + identity, disabled=ui.busy)
        default = state['model'] if provider == state['provider'] else state.get('models', {}).get(provider, MODEL_SUGGESTIONS[provider])
        model_field = 'model_' + identity + '_' + provider
        secret_field = 'llm_key_' + identity + '_' + provider if provider != 'ollama' else None
        available = state['settings_available'].get(provider, False)
        replace_key = not available
        if provider != 'ollama' and available:
            st.caption('API key configured')
            replace_key = st.checkbox('Change API key', key='replace_' + identity + '_' + provider, disabled=ui.busy)
        if not replace_key:
            secret_field = None
        with st.form('model_settings_' + identity + '_' + provider, clear_on_submit=True, border=False):
            st.text_input('Model', value=default, key=model_field, disabled=ui.busy,
                          help='Uses your configured model or an editable suggestion.')
            if secret_field:
                st.text_input('LLM API key', type='password', key=secret_field, disabled=ui.busy,
                              help='Leave blank to reuse an available key.')
            st.form_submit_button('Save settings', disabled=ui.busy, use_container_width=True, on_click=apply_web_settings,
                                  args=(ui, 'configure', secret_field, True, provider, model_field))
        st.caption('Saved settings are reused in new sessions.')


def search_settings(ui, state):
    with st.expander('Search settings'):
        st.caption('Tutor web and hybrid require a separate Tavily key. Local and rag do not.')
        if state:
            st.caption('Tavily key: ' + ('configured' if state['settings_available']['tavily'] else 'not configured'))
        available = bool(state and state['settings_available']['tavily'])
        replace_key = not available or st.checkbox('Change Tavily key', key='replace_search_' + str(ui.root), disabled=ui.busy)
        secret_field = 'search_key_' + str(ui.root) if replace_key else None
        with st.form('search_settings_' + str(ui.root), clear_on_submit=True):
            if secret_field:
                st.text_input('Tavily API key', type='password', key=secret_field, disabled=ui.busy)
            st.form_submit_button('Save search key', disabled=ui.busy, use_container_width=True, on_click=apply_web_settings,
                                  args=(ui, 'configure_search', secret_field, True))


def start_session():
    workspace = st.session_state['workspace']
    workspace.add(lambda: WebSession(seccomp=st.session_state.get('seccomp', False)))
    st.session_state['session_index'] = workspace.index
    st.session_state['web_session'] = workspace.current
    st.session_state['page'] = 'Chat & analysis'


def select_session():
    workspace = st.session_state['workspace']
    workspace.select(st.session_state['session_index'])
    st.session_state['web_session'] = workspace.current


def select_page():
    st.session_state['workspace'].page = st.session_state['page']


def confirm_session_deletion(workspace, target):
    try:
        workspace.remove(target, lambda: WebSession(seccomp=st.session_state.get('seccomp', False)))
        st.session_state['sync_workspace'] = True
    except ValueError as error:
        st.session_state['delete_error'] = str(error)


@st.dialog('Delete session?')
def delete_session(workspace, target):
    if st.session_state.get('sync_workspace'):
        st.rerun()
    st.write('Delete this conversation? Saved analysis files will be kept.')
    if st.session_state.get('delete_error'):
        st.error(st.session_state['delete_error'])
    columns = st.columns(2)
    columns[0].button('Confirm deletion', type='primary', use_container_width=True,
                      on_click=confirm_session_deletion, args=(workspace, target))
    if columns[1].button('Keep session', use_container_width=True):
        st.rerun()


def request_stop(ui, request_id):
    try:
        ui.cancel(request_id)
    except ValueError as error:
        st.session_state['stop_notice'] = str(error)
    st.session_state['stop_dialog_done'] = True


@st.dialog('Stop analysis?')
def stop_analysis(ui, request_id):
    if st.session_state.pop('stop_dialog_done', False):
        st.rerun()
    st.write('Stop this analysis? Completed stages and existing files will be kept. Later stages will not start.')
    columns = st.columns(2)
    columns[0].button('Confirm stop', type='primary', use_container_width=True,
                      on_click=request_stop, args=(ui, request_id))
    if columns[1].button('Keep running', use_container_width=True):
        st.rerun()


def sidebar(ui, state):
    with st.sidebar:
        st.html('<h2 style="font-size:30px">HLAgent</h2>')
        sessions = st.session_state['web_sessions']
        labels = tuple(st.session_state['workspace'].labels)
        st.selectbox('Sessions', range(len(sessions)), key='session_index', on_change=select_session,
                     format_func=lambda i: labels[i], disabled=ui.busy)
        st.html('<style>.st-key-session_actions button{min-height:32px;padding:4px 6px}'
                '.st-key-session_actions button p{font-size:14px;white-space:nowrap}</style>')
        actions = st.container(key='session_actions').columns(2)
        actions[0].button('New session', disabled=ui.busy, use_container_width=True, on_click=start_session)
        if actions[1].button('Delete session', disabled=ui.busy, use_container_width=True):
            st.session_state.pop('delete_error', None)
            delete_session(st.session_state['workspace'], ui)
        if st.session_state.pop('open_chat', False):
            st.session_state['page'] = 'Chat & analysis'
            st.session_state['workspace'].page = 'Chat & analysis'
        page = st.radio('Workspace', ('Chat & analysis', 'Results & reports'), key='page', on_change=select_page)
        st.html('<style>'
                '.st-key-current_step{padding-top:8px;gap:8px}'
                '.st-key-current_step > [data-testid="stElementContainer"]:nth-child(2){margin-bottom:2px}'
                '.st-key-current_step [data-testid="stAlert"]{padding:0;margin:0;min-height:40px}'
                '.st-key-current_step [data-testid="stAlertContainer"]'
                '{padding:8px 12px;margin:0;width:100%;min-height:40px;box-sizing:border-box}'
                '</style>')
        with st.container(key='live_sidebar_status'):
            session_status(ui)
        model_settings(ui, state)
    return page


@st.fragment(run_every=1)
def running_status(ui):
    was_busy = ui.busy
    ui.poll()
    if was_busy and not ui.busy:
        st.rerun()
    if ui.busy:
        status = st.status('Running analysis' if ui.progress else 'Processing request', expanded=True)
        with status.container():
            if getattr(ui, 'cancellable', False):
                if getattr(ui, 'cancel_requested', False):
                    st.warning('Stop requested. Waiting for the tool to stop and save its status.')
                elif st.button('Stop analysis', type='secondary'):
                    st.session_state.pop('stop_dialog_done', None)
                    stop_analysis(ui, ui.request_id)
            st.caption('Working…' if not ui.progress else f'Running · {int(monotonic() - ui.started)}s')
            if ui.progress:
                step = next((event['stage'] for event in reversed(ui.progress)
                             if re.match(r'^\d+/\d+ ', event['stage'])), None)
                if step:
                    st.write('**' + step + '**')
                event = ui.progress[-1]
                st.write(STAGES.get(event['stage'], event['stage']))
                run = next((item.get('run_dir') for item in reversed(ui.progress) if item.get('run_dir')), None)
                if run:
                    with st.expander('Current tool log'):
                        artifacts.contained(ui.root, run)
                        st.code(artifacts.log_tail(run), language=None)


def main():
    st.set_page_config(page_title='HLAgent', layout='wide')
    st.html('<style>'
            '[data-testid="stMainBlockContainer"]{padding-top:3rem;padding-left:2rem;padding-right:2rem}'
            '[data-testid="stSidebarHeader"]{height:1.875rem;margin-bottom:0.5rem}'
            '</style>')
    components.html(Path(__file__).with_name('sidebar_scroll.html').read_text(), height=0)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--seccomp-unconfined', action='store_true')
    args, _ = parser.parse_known_args()
    st.session_state['seccomp'] = args.seccomp_unconfined
    token, workspace = session_registry().connect(st.query_params.get('workspace'),
                                                lambda: WebSession(seccomp=args.seccomp_unconfined))
    if st.query_params.get('workspace') != token:
        st.query_params['workspace'] = token
    if st.session_state.get('workspace_token') != token or st.session_state.pop('sync_workspace', False):
        st.session_state['workspace_token'] = token
        st.session_state['workspace'] = workspace
        st.session_state['web_sessions'] = workspace.sessions
        st.session_state['session_index'] = workspace.index
        st.session_state['page'] = workspace.page
    ui = st.session_state['web_session'] = workspace.current
    ui.poll()
    page = sidebar(ui, ui.state)
    if st.session_state.get('stop_notice'):
        st.info(st.session_state.pop('stop_notice'))
    if ui.error:
        if 'Cannot reach Ollama at ' in ui.error:
            st.error('Ollama is not reachable. Start Ollama on the computer running HLAgent, then submit your request again. You can also choose a cloud provider in Model settings.')
            st.code('ollama serve', language='bash')
            with st.expander('Connection details'):
                st.text(ui.error)
        else:
            st.error(ui.error)
    if ui.state is None:
        running_status(ui)
        if not ui.busy:
            st.info('Check server settings, then start a new session.')
        return
    try:
        if page == 'Results & reports':
            running_status(ui)
            result_browser(ui)
        else:
            chat(ui, ui.state)
    except (OSError, ValueError, KeyError):
        st.error('Could not display this saved item. Check that its recorded files are present and unchanged.')
