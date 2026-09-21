# pypi-release-action

Composite GitHub Actions that build, sign, and publish a Python package to
PyPI using [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) and [Sigstore](https://www.sigstore.dev/) signing.

Three composite actions, one per stage — you wire them into your own jobs so
you keep full control over job boundaries, permissions, and the release
environment gate:

- [`build-package`](./build-package/action.yml) — builds the package, validates metadata,
  smoke-tests the wheel, uploads a `dist` artifact.
- [`sign`](./sign/action.yml) — downloads `dist`, signs it with Sigstore,
  uploads a `signatures` artifact, attaches everything to the GitHub release.
- [`publish`](./publish/action.yml) — downloads `dist`/`signatures`, verifies
  signatures, publishes to PyPI via Trusted Publishing.

## Requirements for consuming repos

- Uses [`uv`](https://docs.astral.sh/uv/) and [`invoke`](https://www.pyinvoke.org/)
  as the build tooling. The repo must provide:
  - `make install-invoke` — installs the `inv` CLI.
  - `inv build-package` (or a custom `build-command`) — builds sdist/wheel
    into `dist/`.
- Each job that uses one of these actions must `actions/checkout` first —
  composite actions run inside the caller's job/checkout, they don't do it
  for you.
- A GitHub Environment named `pypi` configured as a
  [PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
  for the target PyPI project, with:
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

### `build-package` inputs

| Name             | Required | Default             | Description                                              |
| ---------------- | -------- | ------------------- | -------------------------------------------------------- |
| `python-version` | no       | `3.14`              | Python version used to build and smoke test the package. |
| `build-command`  | no       | `inv build-package` | Command used to build the package.                       |

### `build-package` outputs

| Name              | Description                                      |
| ----------------- | ------------------------------------------------ |
| `package-name`    | Package name extracted from `pyproject.toml`.    |
| `package-version` | Package version extracted from `pyproject.toml`. |

### `sign` inputs

None.

### `publish` inputs

| Name                | Required | Default | Description                                                                                    |
| ------------------- | -------- | ------- | ---------------------------------------------------------------------------------------------- |
| `workflow-filename` | yes      | -       | Filename of the calling workflow (e.g. `release-pypi.yaml`), used to verify Sigstore identity. |

## Behavior

- **Manual runs** (`workflow_dispatch`) are only allowed from `main`, and must
  build a dev/pre-release version (e.g. `1.2.3a1`); plain release versions are
  rejected.
- **Tag pushes** (`vX.Y.Z`) must match the package's `pyproject.toml` version,
  are signed with Sigstore, and get artifacts + signatures attached to a
  GitHub release.
- Publishing is skipped if the version is already live on PyPI.

## Release process for this repo

Tag releases (e.g. `v1`, `v1.0.0`) so consumers can pin a version:

```
git tag v1.0.0
git tag -f v1
git push origin v1.0.0 v1 --force
```
