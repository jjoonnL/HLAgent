# HLA Tutor guide

Use HLA Tutor to ask about HLA methods and supported HLAgent workflows.
Enter `tutor` at Supervisor, or choose `HLA Tutor` in Web.
Tutor answers questions; it cannot approve analyses or execute tools.

## Choose an answer source

| Mode | Sources | Tavily API key |
| --- | --- | --- |
| `local` (default) | Built-in HLAgent usage and implementation notes | Not needed |
| `rag` | Indexed documents, including the bundled CookHLA and HATK papers after setup | Not needed |
| `web` | Tavily Web search and implementation notes | Required |
| `hybrid` | Indexed documents, Tavily search and implementation notes | Required |

`local` describes the answer source, not the LLM provider. Every mode uses your
configured Ollama or cloud model. If you use a cloud model, the question and
selected source excerpts are sent to that provider.

In CLI, prefix a question with the mode to select its source:

```text
Supervisor> tutor
HLA Tutor> local What inputs does marker association require?
HLA Tutor> rag What does the HATK paper say about IMGT2Seq?
HLA Tutor> back
```

In Web, select a mode under `Answer sources`, then enter your question.
Use `details` for the latest answer's sources and saved record.

## Enable Web search

Before selecting `web` or `hybrid`, configure a **separate Tavily API key**:

| Interface | Where to configure it |
| --- | --- |
| Before launch | Run `python configure.py --search`. |
| CLI | At Supervisor, enter `config`, then choose `search`. |
| Web | In HLA Tutor, open `Answer sources → Search settings`. |

An LLM provider key does not replace the Tavily key. `local` and `rag` do not
silently fall back to Web search.

## Use the bundled papers

CookHLA and HATK PDFs are included in `knowledge/sources/`. Running `bash setup.sh`
prepares their RAG index unless a current or legacy index already exists.
Existing indexes are preserved.

If you installed manually and have no index yet, run:

```bash
python scripts/tools/tutor_knowledge.py build \
  knowledge/sources/CookHLA-2021.pdf \
  knowledge/sources/HATK-2021.pdf
```

Then select `rag` and ask a question about either paper. These papers describe
the original tools, whose features may differ from HLAgent's supported subset.

CookHLA is licensed under CC BY 4.0. HATK is licensed under CC BY-NC 4.0 and is
provided for non-commercial use. See [paper sources and terms](../knowledge/sources/README.md).

## Add your own documents

1. Place PDF, UTF-8 TXT or Markdown files you have permission to use in
   `knowledge/sources/`.
2. Rebuild the index, explicitly listing **every document you want to retain**.
3. Check the resulting source list with `status`.

For example, to retain the bundled papers and add `MY_PAPER.pdf`:

```bash
python scripts/tools/tutor_knowledge.py build --replace \
  knowledge/sources/CookHLA-2021.pdf \
  knowledge/sources/HATK-2021.pdf \
  knowledge/sources/MY_PAPER.pdf
python scripts/tools/tutor_knowledge.py status
```

`--replace` replaces the entire index; it does not append documents. Include any
other previously indexed files you want to keep. Source files are not deleted.
Build accepts named files rather than scanning a folder. Scanned PDFs need
external OCR before indexing. The shared Conda environment includes pypdf for
PDF ingestion.

## Check retrieval without calling an LLM

Use these commands to inspect the index and retrieve source excerpts locally:

```bash
python scripts/tools/tutor_knowledge.py status
python scripts/tools/tutor_knowledge.py search "HATK IMGT2Seq"
```

These checks make no model or Web calls. Retrieved excerpts and fluent answers
can still miss or misinterpret evidence; review the cited sources.

## Saved answers

CLI answer records are saved under `runs/tutor/`; Web records are saved within
the corresponding Web session directory. Use `details` to find the latest
answer's sources and record.

Records are JSON files and may contain questions and document excerpts.
Keep private records out of public distributions.

## Advanced retrieval settings

The default retriever uses SQLite FTS5 BM25 and needs no embedding model.
Generated indexes and user-added documents are ignored by Git.

| Setting | Behavior |
| --- | --- |
| Default index | `knowledge/tutor.sqlite3` |
| Legacy index | `knowledge/teacher.sqlite3` is reused if the current index is absent. |
| Optional semantic retrieval | Requires `requirements/retrieval.txt` and a compatible local all-MiniLM-L6-v2 snapshot. |

To enable semantic retrieval, install its dependencies in the active environment:

```bash
python -m pip install -r requirements/retrieval.txt
```

Prepare the compatible model snapshot explicitly and set
`TUTOR_EMBEDDING_MODEL_DIR` to its directory. HLAgent does not download it
automatically. An unusable configured snapshot reports an error rather than
silently falling back to lexical retrieval. BM25 remains available when no
semantic model is configured.

The old `teacher` command remains an entry alias for compatibility.
