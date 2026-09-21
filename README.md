# pypi-release-action

Composite GitHub Actions that build, sign, and publish a Python package to
PyPI using [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) and [Sigstore](https://www.sigstore.dev/) signing — no long-lived
API tokens, and every published artifact is signed and verifiable.

## Why

- **No PyPI tokens.** Publishing uses OIDC Trusted Publishing; nothing to
  rotate or leak.
- **Signed releases.** Every distribution is signed with Sigstore keyless
  signing and the signature is verified before publishing.
- **Fails closed.** Wrong version format, version/tag mismatch, a version
  already on PyPI, a checksum mismatch, or a bad signature — any of these
  stops the release instead of publishing something broken.
- **You keep control.** Three separate composite actions, not one
  do-everything action, so you decide job boundaries, permissions, and
  where the `pypi` environment gate sits.

## How it works

| Action                                        | Does                                                                                                                  | Needs                                |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| [`build-package`](./build-package/action.yml) | Builds the package, validates metadata, smoke-tests the wheel, uploads a `dist` artifact.                             | —                                    |
| [`sign`](./sign/action.yml)                   | Downloads `dist`, signs it with Sigstore, uploads a `signatures` artifact, attaches everything to the GitHub release. | `id-token: write`, `contents: write` |
| [`publish`](./publish/action.yml)             | Downloads `dist`/`signatures`, verifies signatures, publishes to PyPI via Trusted Publishing.                         | `id-token: write`                    |

Wire them into your own jobs in whatever order/conditions fit your
workflow — see [Usage](#usage) for a complete example.

## Requirements

- The consuming repo uses [`uv`](https://docs.astral.sh/uv/) and
  [`invoke`](https://www.pyinvoke.org/) as build tooling, and provides:
  - `make install-invoke` — installs the `inv` CLI.
  - `inv build-package` (or a custom `build-command`) — builds sdist/wheel
    into `dist/`.
- Every job that uses one of these actions runs `actions/checkout` first —
  composite actions run inside the caller's job/checkout, they don't do it
  for you.
- A GitHub Environment named `pypi`, configured as a
  [PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
  for the target PyPI project:
  - Workflow filename: your consumer workflow's own file (e.g.
    `release-pypi.yaml`), matching what you pass as `workflow-filename` to
    the `publish` action.
  - Environment name: `pypi`.

## Usage

In the consumer repo, add `.github/workflows/release-pypi.yaml`:

```yaml
name: Publish PyPI Package
on:
  workflow_dispatch: # Allow manual trigger
  push:
    tags:
      - "v*.*.*" # Trigger on version tags (e.g. v1.0.0)

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  build:
    if: github.event_name != 'workflow_dispatch' || github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v7
      - uses: durandtibo/pypi-release-action/build-package@v1

  sign-and-release:
    needs: build
    if: startsWith(github.ref, 'refs/tags/')
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      id-token: write # Sigstore OIDC keyless signing
      contents: write # attach assets to the GitHub release
    steps:
      - uses: durandtibo/pypi-release-action/sign@v1

  publish:
    needs: [build, sign-and-release]
    if: |
      always() &&
      needs.build.result == 'success' &&
      (needs.sign-and-release.result == 'success' || needs.sign-and-release.result == 'skipped')
    runs-on: ubuntu-latest
    timeout-minutes: 5
    environment:
      name: pypi
      url: https://pypi.org/project/<package-name>/
    permissions:
      id-token: write # PyPI Trusted Publishing
    steps:
      - uses: durandtibo/pypi-release-action/publish@v1
        with:
          workflow-filename: release-pypi.yaml
```

This gives you two release paths:

- **Tag push** (`vX.Y.Z`) — builds, requires the tag to match
  `pyproject.toml`'s version, signs with Sigstore, attaches assets to the
  GitHub release, and publishes to PyPI.
- **Manual run** (`workflow_dispatch` from `main`) — builds a dev/pre-release
  version only (e.g. `1.2.3a1`); signing is skipped since there's no tag,
  and `publish` runs straight after `build`.

## Reference

### `build-package`

**Inputs**

| Name             | Required | Default             | Description                                                                                                                                                                                             |
| ---------------- | -------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python-version` | no       | `3.14`              | Python version used to build and smoke test the package.                                                                                                                                                |
| `artifact-name`  | no       | `dist`              | Name of the uploaded distribution artifact. Change this only for jobs that don't feed into `sign`/`publish` (e.g. a matrix build) — those two actions always download an artifact named exactly `dist`. |
| `build-command`  | no       | `inv build-package` | Command used to build the package.                                                                                                                                                                      |

**Outputs**

| Name              | Description                                      |
| ----------------- | ------------------------------------------------ |
| `package-name`    | Package name extracted from `pyproject.toml`.    |
| `package-version` | Package version extracted from `pyproject.toml`. |

**Assumptions and logic**

- Assumes the package lives at the repo root (`pyproject.toml`, plus
  whatever `make install-invoke` / the build command need) — the calling
  job must check it out and stage it there first.
- Fails closed:
  - on `workflow_dispatch` runs, if the built version is a plain release
    version (e.g. `1.2.3`) instead of dev/pre-release (e.g. `1.2.3a1`,
    `1.2.3.dev1`) — manual runs must never publish a "real" release;
  - on tag pushes (`refs/tags/*`), if the tag name (`vX.Y.Z`, `v` stripped)
    doesn't match the version in `pyproject.toml`;
  - always, if `package-name`/`package-version` is already published on
    PyPI (a plain `HEAD`-equivalent lookup against `pypi.org/pypi/.../json`;
    any non-200 status, including errors, is treated as "not published");
  - if `twine check --strict` rejects the built metadata, or the built
    wheel fails to import after installing into a clean venv.
- Always writes `dist/SHA256SUMS` for the built `.tar.gz`/`.whl`, which
  `sign` and `publish` both verify before trusting the artifact.

### `sign`

**Inputs**

| Name       | Required | Default | Description                                                                                                                                                    |
| ---------- | -------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tag-name` | no       | `""`    | Tag/release to attach signed assets to. Defaults to `github.ref_name` (the tag that triggered the workflow); override only for testing from a non-tag trigger. |

**Assumptions and logic**

- Downloads the artifact named `dist` (hardcoded, not configurable) and
  verifies it against `dist/SHA256SUMS` before doing anything else — fails
  closed on a mismatch, before any signing or release changes.
- Signs every `.tar.gz`/`.whl` in `dist/` with Sigstore keyless (OIDC)
  signing, so the calling job needs `id-token: write`.
- Uploads the resulting `*.sigstore.json` bundles as an artifact named
  `signatures`, and attaches the distributions, signature bundles, and
  `SHA256SUMS` to the GitHub release for `tag-name`/`github.ref_name` — so
  the calling job also needs `contents: write` and, on a real tag push, a
  release must already exist for that tag (or the attach step fails).

### `publish`

**Inputs**

| Name                | Required | Default                           | Description                                                                                                                                                                                                                                                |
| ------------------- | -------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `workflow-filename` | yes      | –                                 | Filename of the calling workflow (e.g. `release-pypi.yaml`), used to verify the Sigstore certificate identity on tag pushes. Must be the _same_ filename the `sign` job ran from (see below) and must match what's registered as PyPI's Trusted Publisher. |
| `repository-url`    | no       | `https://upload.pypi.org/legacy/` | Target package index URL. Set to `https://test.pypi.org/legacy/` to publish to TestPyPI instead.                                                                                                                                                           |

**Assumptions and logic**

- Downloads the artifact named `dist` (hardcoded) and verifies it against
  `dist/SHA256SUMS`, failing closed on a mismatch — before touching
  signatures or PyPI.
- On tag pushes only (`refs/tags/*`), also downloads the `signatures`
  artifact and verifies each distribution's Sigstore signature against a
  certificate identity of
  `https://github.com/<repo>/.github/workflows/<workflow-filename>@<github.ref>`.
  This only proves anything if `sign` was run from a workflow file with
  that same name and on that same ref/tag — in the example above, `sign`
  and `publish` run as jobs of the _same_ workflow file, so this holds.
  Splitting them across different workflow files breaks identity
  verification. On non-tag runs (e.g. manual dev-version publishes) this
  check is skipped entirely, since there's no signature to verify.
- Publishes via Trusted Publishing (OIDC), so the calling job needs
  `id-token: write` and an environment matching what's registered with
  PyPI as the Trusted Publisher.
