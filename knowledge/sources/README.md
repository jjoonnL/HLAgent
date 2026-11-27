# Bundled Tutor papers

These papers are supplied as reference material for Tutor RAG. They are not
covered by HLAgent's MIT license. The original tools may support features beyond
HLAgent's implemented analysis scope.

## CookHLA

- Cook S, Choi W, Lim H, Luo Y, Kim K, Jia X, Raychaudhuri S, Han B.
  *Accurate imputation of human leukocyte antigens with CookHLA.*
  Nature Communications 12, 1264 (2021).
- DOI: https://doi.org/10.1038/s41467-021-21541-5
- Copyright: The Author(s) 2021.
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- File: `CookHLA-2021.pdf`, unmodified publisher PDF downloaded from
  https://www.nature.com/articles/s41467-021-21541-5.pdf

## HATK

- Choi W, Luo Y, Raychaudhuri S, Han B. *HATK: HLA analysis toolkit.*
  Bioinformatics 37(3), 416-418 (2021); first published online 31 July 2020.
- DOI: https://doi.org/10.1093/bioinformatics/btaa684
- Copyright: The Author(s) 2020. Published by Oxford University Press.
- License: [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).
  Non-commercial use, sharing and adaptation are permitted with attribution.
  For commercial reuse, contact journals.permissions@oup.com.
- File: `HATK-2021.pdf`, unmodified published article downloaded from the
  [Semantic Scholar copy](https://pdfs.semanticscholar.org/dbbf/9b188cc536cb459e591eb383191b58d35a45.pdf).
- Publisher: https://academic.oup.com/bioinformatics/article/37/3/416/5879278

## Index preparation

Setup extracts page text and splits it into overlapping chunks in a local SQLite
FTS5 index. The PDFs remain unchanged. Extraction normalizes whitespace and
line-break hyphenation; figures, equations and tables may not extract faithfully.
Retrieved excerpts are references for generated answers, not author endorsement.
Original license and third-party credit notices remain in the PDFs; material
excluded by a credit line is subject to its own terms.

`sources.json` records download URLs, licenses and SHA256 hashes. Generated
indexes and user-added documents are excluded from Git. RAG does not require
Tavily; a cloud answer model still receives the question and retrieved excerpts.
