# Optional dependencies

The shared CLI and Web environment is defined in `environment.yml`, including
Streamlit and pypdf. The files below also support minimal environments; install
additional packages only in the environment used by the corresponding interface.

| File | Purpose |
| --- | --- |
| `cli.txt` | Rich terminal presentation |
| `tutor.txt` | PDF document ingestion |
| `retrieval.txt` | Optional local semantic document retrieval |

PDF ingestion is included in the shared environment. For a minimal environment
without pypdf, use `python -m pip install -r requirements/tutor.txt`.
Lexical document retrieval does not need the semantic retrieval packages.
Semantic retrieval also needs an explicitly prepared local model snapshot as
described in the Tutor guide.

Scientific tool dependencies are installed inside the analysis container using
`docker/hatk/requirements.txt`.
