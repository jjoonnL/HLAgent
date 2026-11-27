"""Read project settings without searching parent directories or changing the shell."""

import os
from pathlib import Path


from hlagent.paths import ROOT

ENV_PATH = ROOT / ".env"

MODEL_SUGGESTIONS = {
    'ollama': 'llama3.1:8b',
    'openai': 'gpt-6-luna',
    'anthropic': 'claude-sonnet-4-6',
    'gemini': 'gemini-3.5-flash-lite',
}


def dotenv_tools():
    try:
        from dotenv import dotenv_values, set_key
    except ImportError:
        raise RuntimeError("Install python-dotenv in the active environment: python -m pip install python-dotenv") from None
    return dotenv_values, set_key


def normalize(values):
    values = dict(values)
    if "OLLAMA_MODEL" not in values:
        for name in ("OLLAMA_LLM_MODEL", "DEFAULT_LLM_MODEL"):
            if values.get(name):
                values["OLLAMA_MODEL"] = values[name]
                break
    return values


def read_settings(path=None):
    path = ENV_PATH if path is None else Path(path)
    saved = {}
    if path.exists():
        dotenv_values, _ = dotenv_tools()
        saved = {key: value for key, value in dotenv_values(path, interpolate=False).items()
                 if value is not None}
    return {**normalize(saved), **normalize(os.environ)}
