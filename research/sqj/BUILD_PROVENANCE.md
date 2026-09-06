# Journal document build provenance

The official Springer Nature author template was downloaded on 2026-09-06:

- URL: https://cms-resources.apps.public.k8s.springernature.io/springer-cms/rest/v1/content/18782940/data/v12
- ZIP SHA-256: `812e76dcaa9c28dc1bff1fb6065d51729b67d4ea140552a05088317414a3ecae`
- Class/style files are reused unchanged; they are not ZeroRun-owned code.

The free Tectonic 0.17.0 Windows compiler was downloaded from its official
GitHub release and verified against the release asset's published SHA-256:

- URL: https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip
- ZIP SHA-256: `f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f`
- Version check: `Tectonic 0.17.0`.

The compiler itself is a temporary workspace dependency, not included in the
reviewer archive. The final package records its actual compilation and visual
checks separately. No publisher manuscript-processing fee or model API fee is
incurred by this build.

The manuscript uses the stock class option `pdflatex` together with `sn-apa`.
In this class, that option suppresses the legacy PostScript `breakurl` path;
it does not claim that the actual compiler is pdfTeX. A Tectonic/XeTeX preflight
exposed the default path's undefined `headerps@out` on first-page output. The
document option corrects that compatibility issue without editing the publisher's
class or bibliography style. The compiler identity remains Tectonic 0.17.0.

The document also explicitly imports `xcolor`, required by the stock class's
corresponding-email formatting in this compiler setup. The publisher files
remain unchanged. The final PDF has 18 pages and SHA-256
`45129ac0de34d3212d27cecf672a7045ba60fc2ec8cf0d0426e31f6de8f296e6`.
`generated/pdf-mechanical-review.json` records font, boundary, table, abstract,
and compiler-log checks. `generated/pdf-review.json` separately records actual
visual inspection of every rendered page. Neither receipt is peer review.
