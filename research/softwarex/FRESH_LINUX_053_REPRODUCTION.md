# Fresh public Linux installation: setup, intervention and retained evidence

This supplement makes the fresh-environment setup explicit. It concerns the
small account-free synthetic MCP demonstration, not the real-repository timing
cohorts or a human usability study. The new Linux userland and Docker store were
created from public inputs; the existing Windows hardware and WSL2 kernel were
shared. No old VirtualBox disk, private registry, model account or paid service
was used. The original daemon-lifecycle and Git-metadata refusals are retained.

The fixed evidence root is `research/softwarex/evidence/public-fresh-linux-053-v1/`.
Its top-level `RECORD_MANIFEST.json` seals 139 retained files across
`host-preparation`, `original-attempt`, `corrected-attempt` and `failure-supplement`.
The checks below are not claims that all initial attempts succeeded.

## Create a separate Linux userland on Windows

These PowerShell commands require an already functioning WSL2 installation.
They create a new named distribution and a new C directory. They do not alter an
existing Ubuntu distribution, enable Windows features or delete old data. Use a
new name and directory; stop if either already exists.

```powershell
$freshRoot = 'C:\ZeroRun-fresh-reader'
$distro = 'ZeroRunFreshReader053'
if (Test-Path -LiteralPath $freshRoot) { throw 'Choose a new directory' }
New-Item -ItemType Directory -Path $freshRoot | Out-Null
$rootfsUrl = 'https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-minirootfs-3.24.1-x86_64.tar.gz'
Invoke-WebRequest -Uri $rootfsUrl -OutFile "$freshRoot\rootfs.tar.gz"
Invoke-WebRequest -Uri "$rootfsUrl.sha256" -OutFile "$freshRoot\publisher.sha256"
$expected = '41f73e3cf5fa919b8aa5ca6b30dc48f0da2720776d7423e2a7748211456fe081'
if ((Get-FileHash -LiteralPath "$freshRoot\rootfs.tar.gz" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Rootfs hash differs' }
if ((Get-Content -LiteralPath "$freshRoot\publisher.sha256" -Raw).Split()[0] -ne $expected) { throw 'Publisher checksum differs' }
wsl --import $distro "$freshRoot\distro" "$freshRoot\rootfs.tar.gz" --version 2
wsl -d $distro -u root --exec /bin/sh -c 'apk add --no-cache ca-certificates python3 py3-pip py3-virtualenv git docker && adduser -D -u 1000 reviewer && addgroup reviewer docker'
```

The recorded rootfs was 3,698,422 bytes. APK used the official Alpine v3.24 main
and community repositories with their normal signed-package checks. Its exact
package inventory, Python/Docker versions and operating-system output are retained
in `host-preparation/package-version-inventory.stdout.txt`. Package repositories
can receive updates; the record does not promise an identical future OS build.

## Keep the new Docker daemon's WSL client alive

The initial shell-background daemon launch did not persist after its WSL client
ended. The correction was to keep a dedicated hidden foreground WSL client for
the daemon. These are the actual daemon options used, not a runtime workaround:

```powershell
$daemonArgs = @('-d', $distro, '-u', 'root', '--exec', 'dockerd',
  '--data-root=/var/lib/docker', '--exec-root=/run/docker',
  '--pidfile=/run/zerorun-fresh-dockerd.pid',
  '--host=unix:///var/run/docker.sock', '--iptables=false', '--bridge=none')
$daemon = Start-Process -FilePath "$env:WINDIR\System32\wsl.exe" -ArgumentList $daemonArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput "$freshRoot\dockerd.stdout.txt" -RedirectStandardError "$freshRoot\dockerd.stderr.txt"
$daemon.Id
wsl -d $distro -u reviewer --exec docker info
wsl -d $distro -u reviewer --exec docker image ls --quiet
```

Wait for `docker info` to succeed before continuing. In the recorded new daemon,
the image list was empty before the explicit public pull. Its data directory and
the new user's Docker/cache directories were also absent before launch. The
fixture uses `--network none`; disabling the daemon bridge/iptables does not
change that task contract. Do not substitute another user's existing daemon and
then describe it as empty.

## Obtain and execute the public software

Open the non-root account with `wsl -d $distro -u reviewer`. Then use the
[current metadata-corrected quickstart](QUICKSTART_053_METADATA_V2.md). A fresh
home/configuration and anonymous Git configuration can be made explicit:

```sh
lab_dir=$(mktemp -d -t zerorun-reader-XXXXXXXX)
mkdir "$lab_dir/home"
export HOME="$lab_dir/home"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=false SSH_ASKPASS=false
git -c credential.helper= -c http.extraHeader= clone https://github.com/floxy-21/zerorun-research.git "$lab_dir/source"
git -C "$lab_dir/source" rev-parse HEAD
docker pull --platform linux/amd64 docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef
python3 -I -B "$lab_dir/source/research/softwarex/quickstart_053_metadata_v2.py" \
  --source-root "$lab_dir/source" --create-tools-env "$lab_dir/source-tools" \
  --installation-output "$lab_dir/install.json" --output "$lab_dir/check.json" \
  --approve-synthetic-formative-authority
python3 -I -B "$lab_dir/source/research/softwarex/quickstart_053_metadata_v2.py" \
  --source-root "$lab_dir/source" --installation-receipt "$lab_dir/install.json" \
  --check "$lab_dir/check.json"
```

For a separate wheel installation, use another new virtual environment and the
wheel shipped by the public clone:

```sh
python3 -I -m venv "$lab_dir/wheel-tools"
"$lab_dir/wheel-tools/bin/python" -I -m pip --isolated install --no-cache-dir --no-deps \
  "$lab_dir/source/output/packages/zerorun-softwarex/zerorun-0.5.3-py3-none-any.whl"
"$lab_dir/wheel-tools/bin/python" -I -B "$lab_dir/source/research/softwarex/quickstart_053_metadata_v2.py" \
  --source-root "$lab_dir/source" --zerorun-command "$lab_dir/wheel-tools/bin/zerorun" \
  --zerorun-python-command "$lab_dir/wheel-tools/bin/python" \
  --output "$lab_dir/wheel-check.json" --approve-synthetic-formative-authority
```

The wheel SHA256 is
`dbac896587365b010b6c1c2c5913e1f75e79da99cfdb00c407e67f3442deddb9`.
The original recorded anonymous checkout was release r1 commit
`13a3ceae84913a1ddbed412bdad9cd6fbf7e2e54`, with public manifest SHA256
`2e9d3960d252d6ee3d27f85662dc3cd6a67cbbfcaa705021d2c059001deb7651`.
Its Git index stream was 2,112,171 bytes and exceeded the old 2-MiB checker cap.
The original source installation passed; the original MCP check refused before
any tool call. The corrected helper was executed outside that unchanged clone,
with its exact source and guide copied into the retained records. The later
delivery publishes this correction. It is not claimed to have existed in r1.

Both corrected installations recorded five actual requests: missing-authority
refusal, authorized readiness, fresh success, historical-status reuse and fresh
verification. These are synthetic conformance observations. Installation and
request intervals are retained separately; no acceleration or operator-time
saving is inferred from them.

## The separate fresh-failure control

The failure supplement inspected the unchanged public fixture program, which
only reads the declared UTF-8 `fixture.txt`, compares it with one literal and
raises `SystemExit(73)` on inequality. Its prospective review covers exactly
the original literal and a second explicit unequal literal; no other inputs,
network, environment or outputs were authorized. `qualification.json`, both
data files, the unchanged program, exact manifest, raw requests and complete
wire responses are retained under `failure-supplement/`.

The first request asked for fresh verification and the second used ordinary
execution. Both actually returned `MISS_FAILED`, exit 73, matching text and
structured content, and wire `isError: true`; the repeated failure was not
reused. `verified: false` correctly remains distinct from a successful
`VERIFY_MATCH`. Temporary fixture/authority data were cleaned; authority keys
are not published. This is an additional controlled state, not an extra model
decision or a rewrite of the original five-stage example.

## Verify the whole recorded demonstration without execution

From an untouched public checkout:

```sh
python3 -B -m research.softwarex.verify_fresh_public_053 \
  --source-root . \
  --evidence-dir research/softwarex/evidence/public-fresh-linux-053-v1
```

This separately checks the outer acquisition/preparation records and sealed raw
streams as well as the source/wheel MCP receipts and failure control. It does
not create a WSL distribution, install packages, pull an image, grant authority,
make a model call or repeat the experiment. The live preparation drivers and
their complete commands remain available for inspection; absolute original
paths describe the author run and are not assumed to exist on a reader's machine.

The author's task-owned daemon was stopped after the demonstration by checking
its pidfile against `/proc/<pid>/cmdline`, sending SIGTERM only to that daemon,
and recording that it had exited. The new distro was retained. Readers should
stop only the daemon/distribution they created; no cleanup of pre-existing
projects, distributions or disks is part of this recipe.
