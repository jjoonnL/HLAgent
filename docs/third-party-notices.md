# Third-party components and data sources

Application source is [MIT-licensed](../LICENSE). Third-party code, reference data
and example genotypes retain their own terms. HATK's original code is available
for non-commercial academic research; commercial licensing is handled by its authors.

The HLAgent MIT license and recorded provenance do not establish redistribution
permission for every component. Confirm upstream terms and example-data rights
before public release, or replace/remove the affected assets.

- The bundled HATK subset originates from original HLAgent commit `a2e1761`
  and [WansonChoi/HATK](https://github.com/WansonChoi/HATK). Attribution and file
  provenance remain in `vendor/hatk/`.
- RA examples include WTCCC-derived genotypes and IMGT 3.32.0-derived dictionaries.
  CookHLA examples include genotype and 1000G-derived reference files. Example
  `sources.json` files record their origins and hashes.
- PLINK, BEAGLE, R, Java, Python packages and the separate CookHLA image have
  their own license and distribution conditions.
- Tutor includes the CookHLA paper (CC BY 4.0) and the HATK paper
  (CC BY-NC 4.0, non-commercial use). Their original terms remain separate
  from the application MIT license. See [paper sources and terms](../knowledge/sources/README.md).

API keys, private documents, model weights and run history are not included.

Development tests, evaluation tools and internal records are maintained
separately from this distribution.
