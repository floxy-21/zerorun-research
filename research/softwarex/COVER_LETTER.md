# Cover letter for author approval

Prepared for SoftwareX, Original Software Publication. This text has not been sent. Before submission, the author must approve the exact manuscript, verify the public code/evidence links in its metadata, and complete the declarations listed in `SUBMISSION_CHECKLIST.md`.

---

Dear Editors of SoftwareX,

Please consider "ZeroRun: Reproducible test-result reuse for AI coding tools" as an Original Software Publication.

ZeroRun is a Python tool for conservative reuse of deterministic test results in configured coding workflows. It combines a command-line interface and an MCP interface with explicit eligibility review, input validation, isolated execution, and auditable reuse decisions. Its intended audience includes researchers and developers building AI coding workflows who need to distinguish a previously validated test result from an unsupported request to skip execution.

The accompanying note focuses on the implemented software, its operating contract, reproducible examples, and evidence of its capabilities and limits. The contribution is the reusable implementation and evaluation apparatus, not a claim to have invented content-addressed caching or to accelerate language-model inference.

The evaluation separates controlled execution measurements from observations of public AI-agent trajectories. The main observational sample contains 128 recorded episodes, of which 122 meet the parser's completeness requirements. It retains the six exclusions and the original rate-limited collection attempt. All 120 observed exact-command repeat pairs include intervening state uncertainty; they are therefore not represented as validated cache hits or as measured AI speedups. The software's input-identity checks are also assessed against an independent explicit inventory, and the controlled comparisons expose both favorable and unfavorable performance conditions.

This scope fits SoftwareX's emphasis on inspectable, reusable research software and documented potential impact. The manuscript explains how researchers can use the tool and its evidence machinery to study eligibility and validation costs in repeated test execution. It does not claim established external adoption, commercial demand, or end-to-end improvements in AI task completion. Related work is distinguished from the implemented contribution, and output-transcript limitations are stated explicitly.

The code and evidence availability statements, precise version identifiers, installation requirements, and applicable licenses are provided in the manuscript's metadata and accompanying artifact documentation. Please assess this work as a bounded software contribution with reproducible operating examples and explicit limitations.

Thank you for considering the manuscript.

Jishan Kapoor  
Independent researcher  
Toronto, Canada  
kapoorjishan2@gmail.com

---

Author confirmation required before sending: final manuscript approval; originality and no concurrent consideration elsewhere; accurate funding and competing-interest disclosures; public artifact access and distribution rights; and the complete generative-AI disclosure. This prepared letter does not assert that those confirmations or journal submission have already occurred.
