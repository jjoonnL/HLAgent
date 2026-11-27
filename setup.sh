#!/usr/bin/env bash
set -eu

setup_root=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$setup_root"
setup_env=hlagent
setup_image=hlagent-hatk:layout-v1
setup_image_status='not prepared'
setup_cookhla_image=jaejoon/cookhla:v1
setup_cookhla_status='not prepared'
setup_hatk_source=jjoonnl/hlagent-hatk@sha256:8ce47f6e90fa4a34312fa03f48346039907b3daa6920738e2aebfe5f33fb2b7c
setup_cookhla_source=jjoonnl/hlagent-cookhla@sha256:adfcf841f2c41b4324df907d1b1d5356ed2f95b4f01c2527ab72a6be1faeab68

if ! command -v conda >/dev/null 2>&1; then
    printf '%s\n' 'Conda was not found. Install Miniconda or Anaconda, reopen the terminal and run setup again.' >&2
    exit 1
fi

if conda list --name "$setup_env" --json >/dev/null 2>&1; then
    printf 'Checking existing Conda environment: %s\n' "$setup_env"
else
    printf 'Creating Conda environment: %s\n' "$setup_env"
    conda env create --file "$setup_root/environment.yml" --yes
fi

if ! conda run --no-capture-output -n "$setup_env" python -c '
from importlib.metadata import PackageNotFoundError, version
import shutil
import sys
if sys.version_info[:2] != (3, 12):
    raise SystemExit("Python 3.12 is required.")
for package, expected in {"streamlit": "1.51.0", "python-dotenv": "1.2.1", "rich": "13.7.1", "pypdf": "6.1.3"}.items():
    try:
        installed = version(package)
    except PackageNotFoundError:
        raise SystemExit(f"Missing package: {package}.") from None
    if installed != expected:
        raise SystemExit(f"Expected {package} {expected}.")
try:
    import streamlit, dotenv, rich, pypdf
except (ImportError, OSError) as error:
    raise SystemExit(f"Could not load CLI/Web dependencies: {error}") from None
if not shutil.which("ollama"):
    raise SystemExit("The environment is missing the Ollama executable.")
print("CLI and Web dependencies verified.")
'; then
    printf '%s\n' \
        'The Python environment is incompatible or incomplete. Existing environments are not updated automatically.' \
        'To install separately, see docs/manual-setup.md.' >&2
    exit 1
fi

if [ -f knowledge/tutor.sqlite3 ] || [ -f knowledge/teacher.sqlite3 ]; then
    printf '%s\n' 'Keeping the existing Tutor document index.'
else
    printf '%s\n' 'Preparing the bundled CookHLA and HATK papers for Tutor RAG.'
    conda run --no-capture-output -n "$setup_env" python scripts/tools/tutor_knowledge.py build \
        knowledge/sources/CookHLA-2021.pdf knowledge/sources/HATK-2021.pdf
fi

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker is not installed. Follow the Docker prerequisites in README.md before running analyses.'
elif ! docker info >/dev/null 2>&1; then
    printf '%s\n' 'Docker is not available. Start Docker and rerun setup, or download the images manually.'
else
    check_hatk_image() {
        conda run --no-capture-output -n "$setup_env" python -c '
import sys
from hlagent.analysis import hatk
hatk.IMAGE = sys.argv[1]
try:
    hatk.check_environment()
except (RuntimeError, ValueError, OSError) as error:
    print(f"HATK image check failed: {error}", file=sys.stderr)
    raise SystemExit(1)
print("HATK image verified.")
' "$1"
    }

    if docker image inspect "$setup_image" >/dev/null 2>&1; then
        if check_hatk_image "$setup_image"; then
            setup_image_status=verified
        fi
    fi

    if docker image inspect "$setup_cookhla_image" >/dev/null 2>&1; then
        if conda run --no-capture-output -n "$setup_env" python docker/cookhla/check_image.py "$setup_cookhla_image"; then
            setup_cookhla_status=verified
        fi
    fi

    if [ "$setup_image_status" != verified ] || [ "$setup_cookhla_status" != verified ]; then
        printf 'HATK image: %s. CookHLA image: %s.\n' "$setup_image_status" "$setup_cookhla_status"
        printf '%s' 'Download missing or incompatible analysis images from Docker Hub now? [y/N] '
        setup_reply=''
        if ! IFS= read -r setup_reply; then
            setup_reply=n
        fi
        case "$setup_reply" in
            [yY]|[yY][eE][sS])
                if [ "$setup_image_status" != verified ]; then
                    docker pull --platform linux/amd64 "$setup_hatk_source"
                    check_hatk_image "$setup_hatk_source"
                    docker tag "$setup_hatk_source" "$setup_image"
                    setup_image_status=downloaded
                fi
                if [ "$setup_cookhla_status" != verified ]; then
                    docker pull --platform linux/amd64 "$setup_cookhla_source"
                    conda run --no-capture-output -n "$setup_env" python docker/cookhla/check_image.py "$setup_cookhla_source"
                    docker tag "$setup_cookhla_source" "$setup_cookhla_image"
                    setup_cookhla_status=downloaded
                fi
                ;;
            *) printf '%s\n' 'Image downloads skipped. Prepare the required images before running analyses.' ;;
        esac
    fi
fi

printf '\nPython environment ready. HATK image: %s. CookHLA image: %s.\n' "$setup_image_status" "$setup_cookhla_status"
printf '%s\n' \
    'To use HLAgent from this repository:' \
    '  conda activate hlagent' \
    '  python agent.py                         # CLI' \
    '  python -m streamlit run web.py           # Web' \
    'Choose either interface. Model settings are described under Getting Started in README.md.'
