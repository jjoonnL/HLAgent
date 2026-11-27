"""Start HLAgent with Supervisor and analysis and teaching roles."""

import argparse
from contextlib import nullcontext
import sys

from hlagent.cli import configure as configuration
from hlagent.cli.terminal import TerminalUI
from hlagent.cli.ra_example import start as start_ra_example
from hlagent.cli.cookhla_example import show_plan, show_result
from hlagent.analysis.hatk import Node1Plan
from hlagent.cli.hatk_example import show_plan as show_hatk_plan, show_result as show_hatk_result
from hlagent.analysis.cookhla_to_hped import show_plan as show_conversion_plan, show_result as show_conversion_result
from hlagent.runtime.summary_preparation import show_plan as show_summary_plan, show_result as show_summary_result
from hlagent.runtime.phenotype_preparation import show_plan as show_phenotype_plan, show_result as show_phenotype_result
from hlagent.cli.hatk_phasing_run import show_plan as show_phasing_plan, show_result as show_phasing_result
from hlagent.runtime.llm import CloudClient, OllamaClient
from hlagent.runtime.ollama_runtime import OllamaRuntime
from hlagent.runtime.settings import read_settings
from hlagent.runtime.execution_feedback import ExecutionFeedback
from hlagent.agents.supervisor import SupervisorSession
from hlagent.paths import ROOT
from hlagent.runtime.presentation import stage_title


def main():
    try:
        config = read_settings()
    except (RuntimeError, ValueError, OSError) as error:
        print(f"Could not read settings: {error}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "openai", "anthropic", "gemini"),
                        default=config.get("DEFAULT_LLM_PROVIDER", "ollama").lower())
    parser.add_argument("--model", help="Model name. Cloud providers require an explicit model or provider model environment variable.")
    parser.add_argument("--ollama-url", help="Ollama server URL (default: http://localhost:11434).")
    parser.add_argument("--seccomp-unconfined", action="store_true",
                        help="Use the container-only seccomp compatibility override.")
    parser.add_argument("--plain", action="store_true", help="Use plain text without panels, color or animation.")
    args = parser.parse_args()
    try:
        if args.provider not in ("ollama", "openai", "anthropic", "gemini"):
            raise ValueError("Choose ollama, openai, anthropic, or gemini.")
        if args.provider != "ollama":
            if args.ollama_url:
                raise ValueError("--ollama-url applies only to --provider ollama.")
            client = CloudClient(args.provider, args.model, config=config)
            print(f"Using {args.provider}. Chat, retrieved document/search excerpts and analysis settings/results summaries are sent to this API; calls may incur charges.")
            return chat(client, args.seccomp_unconfined, ui=TerminalUI(plain=args.plain),
                        checkpoint_root=ROOT / 'runs/checkpoints')
        client = OllamaClient(args.model, args.ollama_url, config=config)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    runtime = OllamaRuntime(client)
    ready = False
    try:
        runtime.prepare()
        ready = True
        return chat(client, args.seccomp_unconfined, runtime=runtime, ui=TerminalUI(plain=args.plain),
                    checkpoint_root=ROOT / 'runs/checkpoints')
    except (RuntimeError, ValueError, OSError) as error:
        print(f"Could not prepare Ollama: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStartup cancelled.")
        return 0
    finally:
        if not ready:
            runtime.close()


def configure_session(client, runtime, ui=None):
    """Collect settings locally and apply them only after preparation and atomic save."""
    candidate_runtime = None
    ask = ui.ask if ui is not None else input
    try:
        choice = ask('Settings [llm/search/back]> ').strip().lower()
        if choice in ('back', 'cancel', ''):
            return client, runtime
        if choice == 'search':
            updates = configuration.configure_search(save=False, ask=ask)
            if updates:
                configuration.save_settings(configuration.ENV_PATH, updates)
                print('Search settings saved. The next Tutor search uses the current effective key.')
            return client, runtime
        if choice != 'llm':
            print('Choose llm or search next time, or back to keep current settings.')
            return client, runtime
        current = read_settings()
        provider = getattr(client, 'provider', 'ollama')
        current = dict(current, DEFAULT_LLM_PROVIDER=provider)
        current[provider.upper() + '_MODEL'] = client.model
        if provider == 'ollama':
            current['OLLAMA_BASE_URL'] = client.base_url
        updates = configuration.configure(save=False, current=current, ask=ask)
        if updates is None:
            return client, runtime
        effective = {**current, **updates}
        provider = updates['DEFAULT_LLM_PROVIDER']
        if provider == 'ollama':
            replacement = OllamaClient(config=effective)
            candidate_runtime = OllamaRuntime(replacement)
            candidate_runtime.prepare()
        else:
            replacement = CloudClient(provider, config=effective)
        configuration.save_settings(configuration.ENV_PATH, updates)
        # A new model on the same service must retain its existing owned process.
        if (candidate_runtime is not None and runtime is not None
                and replacement.base_url == client.base_url and candidate_runtime.process is None):
            candidate_runtime.close()
            candidate_runtime = runtime
            runtime.client = replacement
        if runtime is not None and runtime is not candidate_runtime:
            runtime.close()
        print(f'Settings applied. Model: {replacement.model}; API: {replacement.base_url}')
        if provider != 'ollama':
            print('Chat, retrieved excerpts and allowed analysis summaries use this API. Calls may incur charges.')
        return replacement, candidate_runtime
    except (KeyboardInterrupt, EOFError):
        print('\nSettings cancelled. Keeping the current session.')
    except (RuntimeError, ValueError, OSError) as error:
        print(f'Could not apply settings: {error}. Keeping the current session.', file=sys.stderr)
    if candidate_runtime is not None:
        candidate_runtime.close()
    return client, runtime


def chat(client, seccomp_unconfined=False, runtime=None, ui=None, checkpoint_root=None):
    try:
        session = SupervisorSession(client, seccomp_unconfined, checkpoint_root=checkpoint_root)
        ui = ui or TerminalUI()
        ui.welcome(client)
        while True:
            feedback = None
            try:
                text = ui.ask(session.input_prompt)
                if text.strip().lower() == 'example' and session.input_prompt == 'Supervisor> ':
                    if start_ra_example(ui, seccomp_unconfined) is not None:
                        session.next_run = None
                    continue
                if text.strip().lower() == "details" and session.active_agent != 'tutor':
                    selection = session.selection_details()
                    if selection is not None:
                        ui.render(lambda: print(selection), title="Selection details",
                                  actions=("Continue at the current selection prompt.",))
                        continue
                    ui.details('plan' if session.pending_plan is not None else
                               'input' if session.active_agent is not None else 'result')
                    continue
                if text.strip().lower() == "help" and session.active_agent is None:
                    ui.help()
                    continue
                if text.strip().lower() in ("y", "yes") and session.pending_plan is not None:
                    plan = session.pending_plan
                    if isinstance(plan, dict):
                        ui.line("Starting summary preparation." if plan.get('stage') == 'summary_preparation' else "Starting phenotype preparation." if plan.get('stage') == 'phenotype_preparation' else "Starting CookHLA-to-HPED conversion.")
                    elif isinstance(plan, Node1Plan):
                        label = stage_title(plan.to_dict()["example"])
                        if plan.phasing:
                            label = "genotype phasing"
                        ui.line(f"Starting {label}. Log: {plan.run_dir / 'hatk.log'}", style="bold cyan")
                        if plan.phasing:
                            ui.line(f"BEAGLE progress log: {plan.run_dir / 'output/beagle.log'}")
                    else:
                        ui.line(f"Starting CookHLA. Log: {plan.run_dir / 'cookhla.log'}", style="bold cyan")
                    if not isinstance(plan, dict):
                        feedback = ExecutionFeedback(plan, ui=ui)
                role = session.agents.get(session.active_agent)
                if feedback is not None:
                    role.execution_progress = feedback.update
                try:
                    with feedback if feedback is not None else nullcontext():
                        reply = session.respond(text)
                finally:
                    if feedback is not None:
                        role.execution_progress = None
                navigation = reply.navigation
                if not navigation and session.active_agent and not reply.end_session:
                    navigation = ('back / cancel: discard plan and return to Supervisor'
                                  if reply.plan is not None else 'back: return to Supervisor')
                if reply.configure:
                    ui.message(reply.message, title="Settings")
                    client, runtime = configure_session(client, runtime, ui=ui)
                    session.client = client
                    for role in session.agents.values():
                        role.client = client
                    continue
                if reply.result is not None:
                    result_summary = reply.result.get("summary") if isinstance(reply.result, dict) else reply.result.summary
                    result_status = reply.result.get("status") if isinstance(reply.result, dict) else reply.result.status
                    result_style = "green" if result_status == "completed" else "yellow" if result_status == "incomplete" else "red"
                    if isinstance(reply.result, dict):
                        details = reply.result.get("plan", {})
                        if details.get('stage') == 'summary_preparation':
                            ui.render(show_summary_result, reply.result, title="Result", summary=result_summary, border_style=result_style, detail_kind="result")
                        elif details.get('stage') == 'phenotype_preparation':
                            ui.render(show_phenotype_result, reply.result, title="Result", summary=result_summary, border_style=result_style, detail_kind="result")
                        elif details.get("stage") == "cookhla_to_hped":
                            ui.render(show_conversion_result, reply.result, title="Result", summary=result_summary, border_style=result_style, detail_kind="result")
                        elif details.get("example") == "hatk_phasing":
                            ui.render(show_phasing_result, reply.result, title="Result", summary=result_summary, border_style=result_style, detail_kind="result")
                        else:
                            ui.render(show_hatk_result, reply.result, title="Result", summary=result_summary, border_style=result_style, detail_kind="result")
                    else:
                        ui.render(show_result, reply.result, title="Result", summary=result_summary, border_style=result_style, detail_kind="result")
                    if feedback is not None:
                        ui.render(feedback.show_recovery, reply.result, title="Recovery")
                if reply.message and reply.plan is None:
                    role_title = {"finemapping": "HLA Fine-mapping", "imputation": "HLA Imputation",
                                  "phasing": "Genotype Phasing", "conversion": "File Converter",
                                  "tutor": "HLA Tutor"}.get(session.active_agent, "Supervisor")
                    ui.message(reply.message, title=role_title,
                               error="check failed:" in reply.message.lower(),
                               actions=reply.actions, navigation=navigation, handoff=reply.handoff)
                if reply.end_session:
                    return 0
                if reply.plan is not None:
                    plan_summary = reply.plan.get("summary") if isinstance(reply.plan, dict) else None
                    presentation = dict(title="Review plan", summary=plan_summary, message=reply.message, detail_kind="plan",
                                        actions=reply.actions, navigation=navigation, handoff=reply.handoff)
                    if isinstance(reply.plan, dict):
                        if reply.plan.get('stage') == 'summary_preparation':
                            ui.render(show_summary_plan, reply.plan, **presentation)
                        elif reply.plan.get('stage') == 'phenotype_preparation':
                            ui.render(show_phenotype_plan, reply.plan, **presentation)
                        else:
                            ui.render(show_conversion_plan, reply.plan, **presentation)
                    elif isinstance(reply.plan, Node1Plan) and reply.plan.phasing:
                        ui.render(show_phasing_plan, reply.plan, **presentation)
                    elif isinstance(reply.plan, Node1Plan):
                        ui.render(show_hatk_plan, reply.plan, **presentation)
                    else:
                        ui.render(show_plan, reply.plan, verbose=False, **presentation)
            except (RuntimeError, ValueError, OSError) as error:
                session.discard_plan()
                ui.error(f"Could not process the request: {error}")
                if feedback is not None:
                    ui.render(feedback.show_recovery, error=error, title="Recovery")
                    session.active_agent = None
                    session.next_run = None
                    ui.message('Returned to Supervisor.', title='Supervisor')
            except (KeyboardInterrupt, EOFError):
                print("\nSession ended.")
                return 0
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    sys.exit(main())
