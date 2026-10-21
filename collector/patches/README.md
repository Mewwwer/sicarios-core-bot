# EmpireCore transport backport

Base: EmpireCore **0.49.0**, exact commit
[`702f9d26cc65ea6c3b26a03e8ad801b2684f42f3`](https://github.com/eschnitzler/EmpireCore/tree/702f9d26cc65ea6c3b26a03e8ad801b2684f42f3).
Transport fix: upstream
[`f96ab419e79416be1f18f81697bdb22120aea58d`](https://github.com/eschnitzler/EmpireCore/commit/f96ab419e79416be1f18f81697bdb22120aea58d),
20 October 2026. `deflate.py` is an **unmodified copy** of that commit's
`src/empire_core/network/deflate.py`, SHA-256
`cf8a326cf80ac9836f981765d2049863190fc9e1402369727d2fd2b864eddf89`.
Source attribution: EmpireCore project, upstream commit author Ephraim Schnitzler.
The upstream project declares the MIT license in its pyproject.toml; see
[LICENSE.empire-core](LICENSE.empire-core).

The installer applies the same two connection edits as upstream: import the
deflate helper, offer `permessage-deflate; client_max_window_bits` during connect,
then install the inflater **before receive threads start**. It does not copy a
new Connection implementation, replace the socket factory, monkeypatch Python at
runtime, or change collector/game APIs. The upstream `<1.10` websocket-client
constraint is already satisfied by the existing exact **1.9.2** pin.

Why a backport: the full upstream range is **150 commits, 443 files, 49,175
insertions / 11,557 deletions**, with breaking service/model/API changes. Taking
all of 0.58.0 would expand an outage fix into an unrelated collector migration.
The original game API and dependency pins remain intact. No game defaults,
heartbeat, movement state, request waiters, login/relogin policy, SDI quarantine
or defense normalization are edited.

## Installation and verification

The collector Dockerfile applies this **at image build time**. For a local venv,
run after installing requirements, before importing EmpireCore in any process:

```sh
.venv/bin/python -m pip install -r collector/requirements.txt
.venv/bin/python collector/patches/apply_deflate.py
.venv/bin/python collector/patches/apply_deflate.py --check
.venv/bin/python -m pip check
```

The installer requires distribution versions 0.49.0 / 1.9.2 and checks the
**entire** connection source SHA-256 before editing. It refuses any other source
or conflicting deflate module. Reapplying is idempotent; `--check` does not write.
The package distribution version still reads `0.49.0`: this is explicitly a
patched installation, identified by this manifest, module hashes and `--check`.
Plain `pip install -r requirements.txt` in a fresh venv is insufficient.
Reinstallation of EmpireCore can remove the connection edit: apply and check
again. Never apply it to a running collector; build a new image instead.

## Removal rule

On a future deliberate SDK migration, remove this directory and the two backport
Dockerfile instructions together, update the exact reviewed dependency pin and
its API integration, and retain compressed/fragmented/context/error/limit and
reconnect regression coverage. Do not loosen the source hash or version checks
to make a new SDK build pass. The current checks intentionally stop such a build.

The 64 MiB bound is on each **inflated compressed message**, including fragments;
it is not an application-wide memory budget or an uncompressed-frame size limit.
Messages sent by the collector remain uncompressed, as RFC 7692 permits.
