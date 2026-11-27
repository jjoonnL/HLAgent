"""Save LLM settings for subsequent agent sessions."""

import argparse

from getpass import GetPassWarning, getpass
import os
from pathlib import Path
import sys
import tempfile
import warnings

from hlagent.runtime.settings import ENV_PATH, MODEL_SUGGESTIONS, dotenv_tools, normalize, read_settings


def save_settings(path, updates):
    _, set_key = dotenv_tools()
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".hlagent-settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if path.exists():
                stream.write(path.read_text(encoding="utf-8"))
        for key, value in updates.items():
            set_key(temporary, key, value, quote_mode="always")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(path=None, *, save=True, current=None, ask=None):
    if ask is None:
        from hlagent.cli.terminal import TerminalUI
        ask = TerminalUI().ask
    path = ENV_PATH if path is None else Path(path)
    dotenv_values, _ = dotenv_tools()
    current = read_settings(path) if current is None else dict(current)
    saved = normalize(dotenv_values(path, interpolate=False)) if path.exists() else {}
    print(f"Save LLM settings to {path}")
    print("API keys are stored as plain text, readable only by your account on Linux/macOS.")
    print("Enter keeps the displayed setting. Ctrl+C cancels without saving.")
    provider = current.get("DEFAULT_LLM_PROVIDER", "ollama")
    provider = (ask(f"Provider [ollama/openai/anthropic/gemini] ({provider}): ").strip() or provider).lower()
    if provider not in ("ollama", "openai", "anthropic", "gemini"):
        raise ValueError("Choose ollama, openai, anthropic, or gemini.")
    model_key = provider.upper() + "_MODEL"
    suggestion = MODEL_SUGGESTIONS[provider]
    print(f"Suggested model: {suggestion}")
    model = current.get(model_key) or suggestion
    prompt = f"Model [{model}] (press Enter to use, or type a model name): "
    model = ask(prompt).strip() or model
    if not model:
        raise ValueError("A model name is required.")
    updates = {"DEFAULT_LLM_PROVIDER": provider, model_key: model}
    if provider != "ollama":
        key_name = provider.upper() + "_API_KEY"
        if current.get(key_name) and not saved.get(key_name):
            print("A shell API key is available. Enter it below to save it for future sessions.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            try:
                key = getpass("API key (hidden, Enter keeps the saved key): ").strip()
            except GetPassWarning:
                raise ValueError("Hidden input is unavailable. Run configure.py in an interactive terminal.") from None
        if key:
            updates[key_name] = key
        elif not saved.get(key_name) and not current.get(key_name):
            raise ValueError("An API key is required. No settings were saved.")
    print(f"Provider: {provider}; model: {model}")
    if save:
        print("Exported shell variables and command options take precedence over saved settings.")
    else:
        print("These choices apply to this session. Shell variables and command options can override saved settings on the next start.")
    if ask("Save settings? [y/n]: ").strip().lower() not in ("y", "yes"):
        print("Settings were not changed.")
        return
    if save:
        save_settings(path, updates)
        print("Settings saved. Start the assistant with python agent.py.")
    return updates


def configure_search(path=None, *, save=True, ask=None):
    if ask is None:
        from hlagent.cli.terminal import TerminalUI
        ask = TerminalUI().ask
    path = ENV_PATH if path is None else Path(path)
    print('Save the Tavily Web search API key. Queries are sent to Tavily and may use search credits.')
    print('Local document text is not sent to Tavily. Enter leaves settings unchanged.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', GetPassWarning)
        try:
            key = getpass('Tavily API key (hidden): ').strip()
        except GetPassWarning:
            raise ValueError('Hidden input is unavailable. Use an interactive terminal.') from None
    if not key or ask('Save search settings? [y/n]: ').strip().lower() not in ('y', 'yes'):
        print('Settings were not changed.')
        return
    updates = {'TAVILY_API_KEY': key}
    if save:
        save_settings(path, updates)
        print('Search key saved. In HLA Tutor, select web or hybrid.')
    return updates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search', action='store_true', help='Configure optional Tavily Web search only.')
    args = parser.parse_args()
    try:
        if args.search:
            configure_search()
        else:
            configure()
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nConfiguration cancelled. Settings were not changed.")
        return 0
    except (RuntimeError, ValueError, OSError) as error:
        print(f"Could not save settings: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
