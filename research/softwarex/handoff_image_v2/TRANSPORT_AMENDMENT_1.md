# Registry startup transport amendment

The first V2 image-preparation attempt built the dependency image and created
the private loopback registry, then the HTTP readiness probe encountered a
`ConnectionResetError` during startup. The attempt failed before a derived
registry digest or any V2 pilot result was obtained. Its original source commit
`c9b7ebe` and failure records remain retained separately; they are not overwritten
or classified as a successful build.

The corrected readiness helper catches that specific transient connection-reset
exception in the existing 15-second startup polling window. It does not extend
the window, change the image recipe/dependencies, weaken Docker configuration,
retry a study outcome, change case selection, or modify ZeroRun runtime bytes.
The corrected image preparation uses a new `handoff-image-build-v2b` directory.
Both the original V1 pilot results and the first image-build failure remain
reportable. V2 is still the previously planned separate deployment variant.
