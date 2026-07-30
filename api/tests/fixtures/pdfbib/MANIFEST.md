# pdfbib test fixtures

Four real PDFs, all fetched from arXiv (licence-safe to commit under arXiv's
non-exclusive distribution licence -- the spec's licence rule allows committing
arXiv/CC-BY only; anything else would be a URL+SHA256 manifest with a fetch
script and skipped tests).

| File | arXiv id | Role | Bibliography style |
|---|---|---|---|
| `two_column_doi_rich.pdf` | 2607.26019v1 (quant-ph) | modern two-column, DOI-rich | REVTeX, bracketed numeric `[1]`-`[60]`, **no heading** (exercises the keyed-region fallback), 36 DOIs |
| `ams_alpha_math_ag.pdf` | 1503.08320 (math.AG) | AMS alpha-key | amsalpha `[BCHM10]`..`[Xu14]`, 35 entries, detached key column (exercises key-row merging), trailing address block |
| `apa_unnumbered_stats.pdf` | 1406.5823v1 (stat.CO) | unnumbered author-year | JSS style, 37 entries, hanging indent, references mid-document followed by appendices (exercises font-size region termination) |
| `pre2000_no_doi.pdf` | hep-th/9711200v3 (1997) | pre-2000, no DOIs | bracketed numeric `[1]`-`[65]`, old-style arXiv ids, no DOIs anywhere |

SHA256 (verify with `Get-FileHash -Algorithm SHA256` / `sha256sum`):

```
c541a7baf4f7191842f6f64f3e4c40f327bd8af26873a909fc04030e648b1215  two_column_doi_rich.pdf
c37b4dd741a5e3437796381396239254918a20c7f117031f14510440ec12bd2e  ams_alpha_math_ag.pdf
901030e6466b79afba9e87da51112a9da1302debd257ad504d759cf9d974d115  apa_unnumbered_stats.pdf
b711c300aac9a8c93d84451675554da8829d4b7d3c1ae23cdd16e94523975f06  pre2000_no_doi.pdf
```

Ground-truth entry counts were established INDEPENDENTLY of the splitter, by
regex over plain `extract_text` output (distinct bracketed keys for the keyed
styles; `"(year)."` author-year heads between the References heading and the
first appendix for the JSS style): 60 / 35 / 37 / 65. The committed
`expected.json` records these plus entry-prefix samples.

`*.layout.json.gz` are the hermetic layout-line artefacts (`(text, bbox,
font_size, page, column)` per line, gzipped JSON) produced by `generate.py`
from the PDFs. Splitter and scorer tests run against these and are immune to
pdfminer drift; one slow test per fixture re-derives them from the PDF and
compares entry output end-to-end.

Regenerate after a deliberate pdfminer upgrade or layout-code change:

    cd api && python tests/fixtures/pdfbib/generate.py
