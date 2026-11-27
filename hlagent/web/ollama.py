"""Start local Ollama before Web requests without interactive downloads."""

import fcntl

from hlagent.paths import ROOT
from hlagent.runtime.llm import OllamaClient
from hlagent.runtime.ollama_runtime import OllamaRuntime


class WebOllamaClient(OllamaClient):
    def decide(self, messages, schema):
        directory = ROOT / 'runs/ollama'
        directory.mkdir(parents=True, exist_ok=True)
        # Workers share one service. Serialize the check/start pair across sessions.
        with (directory / 'web-startup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            runtime = OllamaRuntime(self)
            try:
                runtime.prepare(allow_download=False)
            except BaseException:
                runtime.close()
                raise
            finally:
                if runtime.log is not None:
                    runtime.log.close()
            # A successfully started service stays available to other Web sessions.
            # Deleting its originating conversation must not stop that service.
        return super().decide(messages, schema)
