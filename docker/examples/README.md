# Example-enabled analysis images

The fixed examples are included in the published analysis images:

- `jjoonnl/hlagent-hatk:v1-amd64`: the HATK environment, current analysis
  adapters, WTCCC RA example inputs, IMGT 3.32.0 dictionaries and prephased fixture.
- `jjoonnl/hlagent-cookhla:v1-amd64`: the handed-over CookHLA environment plus its
  1958BC example inputs, 1000G reference panel and adaptive map files.

The local names remain `hlagent-hatk:layout-v1` and `jaejoon/cookhla:v1`.
Setup pins each published image by digest and verifies it before updating its
local name. Existing incompatible images are replaced only after a successful check.

## Build locally

Obtain the existing base images and privately held example payloads. Do not
download restricted cohort data from an unofficial mirror. The local payload
directories must have the layout and original hashes described by
`examples/manifests/hatk/` and `examples/manifests/cookhla/`.

The `examples/hatk/` and `examples/cookhla/` build directories are ignored by Git.
They are build inputs only, not application runtime paths. Never commit them.
The original private development checkout retains the payloads, but no developer
filesystem path is hard-coded into the application.

From the distribution repository root, with those private payloads in place:

```bash
docker build --platform linux/amd64 --network none \
  -f docker/examples/hatk.Dockerfile -t hlagent-hatk:rebuilt .
docker build --platform linux/amd64 --network none \
  -f docker/examples/cookhla.Dockerfile -t hlagent-cookhla:rebuilt .
```

The base image tags must already exist locally. These Dockerfiles do not install
or update scientific tools. Record the base and new image IDs when rebuilding.
The CookHLA fingerprint checker is pinned to the recorded image, so a rebuilt
image with a different runtime fingerprint must be reviewed before updating it.
Use `bash setup.sh` to verify the local images and Python environment.

## Runtime behavior

Selecting an example creates a stopped temporary container from the local
image, copies `/opt/hlagent/examples/hatk/` or `/opt/hlagent/examples/cookhla/`,
and removes the container without ever starting it. No network access or image
pull is requested during extraction.

All payload files, including source manifests, are checked against the host's
recorded hashes. The checked cache is installed atomically under
`datasets/examples/hatk/` or `datasets/examples/cookhla/` and is ignored by Git.
Existing caches are checked and reused, not overwritten or silently repaired.
The analysis still requires the displayed plan and explicit approval.

Custom-input analyses do not need example extraction. The original dictionary
generator remains available for users who want other IMGT releases.

Removing host files from a new commit does not erase earlier Git history.
No Git history rewrite is performed by setup or example extraction.
