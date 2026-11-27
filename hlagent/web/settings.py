"""Web settings suggestions and writes through the existing configuration writer."""

from hlagent.cli.configure import save_settings
from hlagent.runtime.settings import MODEL_SUGGESTIONS



def save_defaults(path, updates):
    # Separate worker processes may save different providers at the same time.
    import fcntl
    lock = path.parent / 'runs/web-sessions/.settings.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        save_settings(path, updates)


class UnconfiguredClient:
    """Keep settings accessible without silently switching to another provider."""

    def __init__(self, provider, model):
        self.provider, self.model = provider, model

    def decide(self, messages, schema):
        raise ValueError('Open Model settings to set a model and API key, then apply the settings.')
