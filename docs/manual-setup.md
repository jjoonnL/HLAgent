# Manual setup

Use these commands when setup cannot use an existing environment or when
preparing the environment manually. Run them from the repository directory.

Create and activate the Python environment:

```bash
conda env create -f environment.yml
conda activate hlagent
```

If that name is already used by an incompatible environment, create a separate
one with `conda env create -n hlagent-release -f environment.yml` and activate
`hlagent-release` instead. Setup does not overwrite an existing environment.
Use your chosen name wherever Getting Started shows `hlagent`.

With Docker running, download both example-enabled images and set their local names:

```bash
docker pull --platform linux/amd64 jjoonnl/hlagent-hatk:v1-amd64
docker tag jjoonnl/hlagent-hatk:v1-amd64 hlagent-hatk:layout-v1
docker pull --platform linux/amd64 jjoonnl/hlagent-cookhla:v1-amd64
docker tag jjoonnl/hlagent-cookhla:v1-amd64 jaejoon/cookhla:v1
```

Prepare the bundled CookHLA and HATK papers for Tutor RAG (skip this if you
already have a document index you want to keep):

```bash
python scripts/tools/tutor_knowledge.py build knowledge/sources/CookHLA-2021.pdf knowledge/sources/HATK-2021.pdf
```
