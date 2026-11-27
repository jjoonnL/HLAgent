"""Install the checkout's hlagent command in the user's local bin directory."""

from pathlib import Path


def install(directory):
    launcher = Path(__file__).resolve().parents[1] / 'bin/hlagent'
    directory = Path(directory).expanduser()
    target = directory / 'hlagent'
    if target.is_symlink() and target.resolve() == launcher:
        return target
    if target.exists() or target.is_symlink():
        raise FileExistsError(f'An existing command was left unchanged: {target}')
    directory.mkdir(parents=True, exist_ok=True)
    target.symlink_to(launcher)
    return target


if __name__ == '__main__':
    print(f'Installed: {install(Path.home() / ".local/bin")}')
    print('Run hlagent for the CLI or hlagent web for the browser interface.')
