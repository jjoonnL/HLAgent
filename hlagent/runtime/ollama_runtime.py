"""Prepare Ollama without changing the user's installed environment."""

from hlagent.paths import new_run_id

from hlagent.paths import ROOT

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class OllamaRuntime:
    def __init__(self, client):
        self.client = client
        self.process = None
        self.log = None
        self.log_path = None

    def models(self):
        try:
            with urlopen(self.client.base_url + "/api/tags", timeout=3) as response:
                body = json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"Ollama model check failed: HTTP {error.code}.") from error
        except (URLError, OSError):
            return None
        try:
            return [entry["name"] for entry in body["models"]]
        except (KeyError, TypeError) as error:
            raise RuntimeError("The server did not return an Ollama model list.") from error

    def prepare(self, *, allow_download=True):
        models = self.models()
        if models is None:
            parsed = urlparse(self.client.base_url)
            if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
                raise RuntimeError(f"Cannot reach {self.client.base_url}. Start that Ollama server first.")
            binary = shutil.which("ollama")
            if binary is None:
                raise RuntimeError("Ollama is not installed or not on PATH. Create and activate the environment in environment.yml.")
            self.start(binary, parsed.port or 80)
            models = self.models()
        else:
            print("Using the running Ollama server.")
        name = self.client.model
        installed_name = name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"
        if name not in models and installed_name not in models:
            if not allow_download:
                raise RuntimeError(f"Model {name} is not installed. Install it with ollama pull {name}, then try again.")
            size = "approximately 4.9 GB" if name == "llama3.1:8b" else "size depends on the model"
            print(f"Model {name} is not installed ({size}).")
            if input("Download this model? [y/n]: ").strip().lower() not in ("y", "yes"):
                raise RuntimeError("Model download cancelled. No analysis was started.")
            self.pull()
            models = self.models()
            if models is None or (name not in models and installed_name not in models):
                raise RuntimeError("The requested model is still unavailable after downloading.")
        print(f"Model ready: {name}")

    def start(self, binary, port):
        env = os.environ.copy()
        env["OLLAMA_HOST"] = f"127.0.0.1:{port}"
        if not env.get("OLLAMA_MODELS") and env.get("CONDA_PREFIX"):
            env["OLLAMA_MODELS"] = str(Path(env["CONDA_PREFIX"]) / "ollama_models")
        log_dir = ROOT / "runs" / "ollama"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / (new_run_id('ollama', log_dir, '.log') + '.log')
        self.log = self.log_path.open("w")
        print(f"Starting Ollama. Log: {self.log_path}", flush=True)
        self.process = subprocess.Popen([binary, "serve"], env=env, stdout=self.log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Ollama stopped during startup. See {self.log_path}.")
            if self.models() is not None:
                return
            time.sleep(0.25)
        raise RuntimeError(f"Ollama did not become ready within 30 seconds. See {self.log_path}.")

    def pull(self):
        request = Request(self.client.base_url + "/api/pull",
                          data=json.dumps({"model": self.client.model, "stream": True}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        print(f"Downloading {self.client.model}...", flush=True)
        last_progress = None
        with urlopen(request, timeout=120) as response:
            for line in response:
                item = json.loads(line)
                if item.get("error"):
                    raise RuntimeError(f"Model download failed: {item['error']}")
                status = item.get("status", "")
                total = item.get("total", 0)
                if total:
                    percent = int(100 * item.get("completed", 0) / total)
                    status += f" {percent // 10 * 10}%"
                if status and status != last_progress:
                    print(status, flush=True)
                    last_progress = status

    def close(self):
        try:
            if self.process is not None:
                # Only signal the process group created by this instance.
                if self.process.poll() is None:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.process.pid, signal.SIGKILL)
                        self.process.wait(timeout=5)
        except ProcessLookupError:
            pass
        finally:
            if self.log is not None:
                self.log.close()
