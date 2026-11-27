# Bundled HATK subset

These modules originate from the original HLAgent checkout at commit
`a2e176149d1df03b7ac72d8d331638f9f9bd7a9a`, with patches recorded in
`sources.json`. The underlying project is
[WansonChoi/HATK](https://github.com/WansonChoi/HATK). Original comments,
attribution and the inherited `LICENSE.HLAgent` are retained. That license file
alone does not establish the licensing of every upstream component. See
[license information](../../README.md#license) before public distribution.

The subset supports nomenclature normalization, marker generation, hg18
IMGT2Seq dictionary preparation and the original omnibus model. It is not the
full HATK command interface. IMGT source alignments are not included. The
bundled exon-start coordinates cover hg18 only.

Recorded patches remove automatic dotenv loading and downloader imports from
the worker, support the pinned pandas API, retain wide numeric allele fields and
report omnibus GLM convergence failures. The omnibus model still treats the two
haplotype rows per person without adjusting within-person dependence. No
scientific source was changed while preparing this distribution candidate.

Host adapters stage checked inputs and the offline worker performs the approved
calculation. Image-side source hashes are checked before execution. Original and
bundled file hashes in `sources.json` document provenance, not proof of
biological equivalence to every upstream release.
