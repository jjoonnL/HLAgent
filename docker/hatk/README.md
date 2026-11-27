# HATK analysis image

The example-enabled analysis image is published as
[jjoonnl/hlagent-hatk:v1-amd64](https://hub.docker.com/r/jjoonnl/hlagent-hatk).
Its local name is `hlagent-hatk:layout-v1`. Run `bash setup.sh` to download and
verify it. See [example-enabled images](../examples/README.md) for packaging details.

These build inputs are retained for source verification and reproducibility.
The runtime checks the image-side source hashes before an approved analysis.
The image targets Linux x86_64. Apple Silicon uses emulation.

See [installation](../../README.md#installation) and
[license information](../../README.md#license).
