# Releasing promptdrift

## Release checklist for 0.3.0

Nothing in this repository releases anything on its own: no workflow tags,
publishes or deploys unless a maintainer takes one of the steps below. Do
them in this order.

1. **Merge the v0.2.0 pull request** into `main` first. The v0.3.0 branch was
   cut from it, so merging it first keeps the v0.3.0 diff small and keeps the
   history linear.
2. **Enable GitHub Pages once, before merging v0.3.0.** In the repository
   settings open Pages and set Source to "GitHub Actions" (this works before
   any Pages workflow has run on `main`). The docs workflow
   (`.github/workflows/docs.yml`) deploys on every push to `main`, so the merge
   in step 3 publishes https://dakshtrehan.github.io/promptdrift/. If Pages was
   enabled only after the merge, the deploy job of that first run fails: run the
   docs workflow again from the Actions tab ("Run workflow" on `main`, it has a
   `workflow_dispatch` trigger) or re-run its failed jobs.
3. **Merge the v0.3.0 pull request.** CI must be green on every job: the test
   matrix (Python 3.9 to 3.13), mypy, the core-only install, the examples and
   plugin job, the package job, and the `promptdrift example` workflow that
   dogfoods the GitHub Action on this repository's own pull requests. Read the
   log of that dogfood run and the PR comment it posted before tagging; the
   first run from an external repository is step 8. After the merge, check that
   the docs deploy succeeded and that the quickstart and the API reference
   pages render.
4. **Tag the release** from a clean checkout of `main`:

   ```bash
   git checkout main && git pull origin main
   git tag -a v0.3.0 -m "promptdrift 0.3.0"
   git push origin v0.3.0
   ```

5. **Moving major tag for the action.** Users pin `uses: dakshtrehan/promptdrift@v0.3`
   to get patch releases without editing their workflow. Create it now and move
   it on every 0.3.x release:

   ```bash
   git tag -f v0.3 v0.3.0
   git push -f origin v0.3
   ```

6. **GitHub release notes.** Create a GitHub Release for tag `v0.3.0` and paste
   the `[0.3.0]` section of `CHANGELOG.md` as the body. Note: publishing the
   release triggers `.github/workflows/publish.yml`, which only succeeds if PyPI
   trusted publishing is configured (see "A note on publish.yml" below). If you
   publish to PyPI by hand instead, either disable that workflow first or accept
   one failed run.
7. **Publish to PyPI, maintainer only, with your own token.** Follow sections 0
   to 4 below with `0.3.0` in place of `0.2.0`. No CI job and no agent uploads
   to PyPI; the token never enters the repository.
8. **Post-release checks:**

   ```bash
   python -m venv /tmp/pd-check && /tmp/pd-check/bin/pip install "git+https://github.com/dakshtrehan/promptdrift.git@v0.3.0"
   /tmp/pd-check/bin/promptdrift --version      # promptdrift 0.3.0
   /tmp/pd-check/bin/promptdrift diff examples/offline/outputs.jsonl --summary
   ```

   Once the PyPI upload is done, repeat with `pip install promptdrift==0.3.0`.
   Then, in any repository, run a workflow with `uses: dakshtrehan/promptdrift@v0.3.0`
   and one with `@v0.3` on a pull request and confirm the comment, the job
   summary and the artifact appear.
9. **After the PyPI upload**, follow section 6 below to restore the
   `pip install promptdrift` instructions in the README, drop the "not yet
   published" line from `README.md` and from the `[0.3.0]` entry of
   `CHANGELOG.md`, and mark the never uploaded `[0.2.0]` entry as superseded
   (section 6, step 3).

The sections below are the original 0.2.0 publishing notes; the commands are
the same for 0.3.0 with the version number changed.

# Releasing promptdrift 0.2.0 to PyPI

promptdrift has never been published. On 2026-11-04, `promptdrift` (and the
variants `prompt-drift` and `prompt_drift`) returned 404 on both PyPI and
TestPyPI, so the name is free. Nothing in this repository uploads anything on
its own; every step below is run by you, with your own token.

The build was verified before this file was written: `python -m build` produced
`promptdrift-0.2.0.tar.gz` and `promptdrift-0.2.0-py3-none-any.whl`, and
`twine check --strict dist/*` passed for both. The wheel was installed in a
clean virtualenv and run, and the test suite passed from the unpacked sdist.

## 0. One-time setup

1. Create an account on https://pypi.org and turn on two-factor authentication
   (required for uploads).
2. Optional but recommended: create a separate account on https://test.pypi.org
   for a dry run.
3. Create an API token at https://pypi.org/manage/account/token/. For the very
   first upload the project does not exist yet, so the token must have scope
   "Entire account". After the first upload, delete it and create a new token
   scoped to the `promptdrift` project only.

Keep the token out of the repository and out of your shell history where you
can. The commands below read it from an environment variable.

## 1. Build from a clean checkout of main

Run these after the v0.2.0 pull request is merged.

```bash
git checkout main
```

```bash
git pull origin main
```

```bash
python -m pip install --upgrade build twine
```

```bash
rm -rf dist build *.egg-info
```

```bash
python -m build
```

```bash
twine check --strict dist/*
```

You should see exactly two files in `dist/`: `promptdrift-0.2.0.tar.gz` and
`promptdrift-0.2.0-py3-none-any.whl`, both `PASSED`.

Optional: run the tests one last time.

```bash
pip install -e ".[dev]" && pytest -q
```

## 2. Dry run on TestPyPI (optional)

```bash
export TWINE_USERNAME=__token__
```

```bash
export TWINE_PASSWORD=pypi-REPLACE_WITH_YOUR_TESTPYPI_TOKEN
```

```bash
twine upload --repository testpypi dist/*
```

Then install it into a throwaway environment. The extra index is needed because
PyYAML is not on TestPyPI.

```bash
python -m venv /tmp/pd-test && /tmp/pd-test/bin/pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ promptdrift==0.2.0
```

```bash
/tmp/pd-test/bin/promptdrift --version
```

## 3. Upload to PyPI

```bash
export TWINE_USERNAME=__token__
```

```bash
export TWINE_PASSWORD=pypi-REPLACE_WITH_YOUR_PYPI_TOKEN
```

```bash
twine upload dist/*
```

```bash
unset TWINE_PASSWORD
```

A version number can only be uploaded once. If something is wrong after the
upload, bump to 0.2.1 rather than trying to replace 0.2.0.

## 4. Verify

```bash
python -m venv /tmp/pd-check && /tmp/pd-check/bin/pip install promptdrift==0.2.0
```

```bash
/tmp/pd-check/bin/promptdrift --version
```

It should print `promptdrift 0.2.0`. The project page is
https://pypi.org/project/promptdrift/.

## 5. Tag the release

```bash
git tag -a v0.2.0 -m "promptdrift 0.2.0"
```

```bash
git push origin v0.2.0
```

### A note on `.github/workflows/publish.yml`

That workflow runs when a GitHub Release is **published**, and uploads with
PyPI trusted publishing (no token). It only works if you have configured a
trusted publisher for this repository on PyPI and a `pypi` environment on
GitHub. Pick one route:

* **Manual upload (this document).** Pushing a tag does not trigger the
  workflow, so steps 1 to 5 are safe. If you also create a GitHub Release for
  v0.2.0, the workflow will run, try to upload the same version, and fail
  because it already exists. That failure is harmless, or you can disable the
  workflow first.
* **Trusted publishing instead of a token.** Add a pending publisher at
  https://pypi.org/manage/account/publishing/ (owner `dakshtrehan`, repository
  `promptdrift`, workflow `publish.yml`, environment `pypi`), create the `pypi`
  environment in the GitHub repository settings, then publish a GitHub Release
  for tag `v0.2.0` and skip step 3.

## 6. After publishing: restore the install instructions

The README deliberately says the package is not on PyPI yet. Once
`pip install promptdrift` works:

1. In `README.md`, replace the "not on PyPI yet" note and the
   `git+https://...` install block with:

   ```bash
   pip install promptdrift
   pip install "promptdrift[openai]"      # OpenAI
   pip install "promptdrift[anthropic]"   # Anthropic / Claude
   pip install "promptdrift[all]"         # both
   ```

2. Add the PyPI badge back under the title:

   ```markdown
   [![PyPI version](https://img.shields.io/pypi/v/promptdrift.svg)](https://pypi.org/project/promptdrift/)
   ```

3. In `CHANGELOG.md`, delete the "Not yet published to PyPI" line under the
   version you just uploaded (for 0.3.0, the line under `[0.3.0]`). If an
   older entry such as `[0.2.0]` was never uploaded, change its line to "Never
   published to PyPI; superseded by 0.3.0." instead of deleting it.
