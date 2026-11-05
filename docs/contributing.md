---
title: Contributing
---

--8<-- "CONTRIBUTING.md"

## Building the docs locally

The site is built with mkdocs-material and mkdocstrings; the API pages are
generated from the docstrings of the installed package, so install promptdrift
first.

```bash
pip install -e .
pip install -r docs/requirements.txt
mkdocs build --strict        # fails on any warning, including a broken link
mkdocs serve                 # live preview at http://127.0.0.1:8000/
```

`mkdocs.yml` sets `font: false`, so neither the build nor the pages fetch
anything from the network. `tests/test_docs.py` checks that `mkdocs.yml`
parses, that every page in the nav exists, that no page contains an em or en
dash, that every CLI flag of `compare` and `diff` is documented in the CLI
reference, and it runs every command in a fenced bash block that starts with
the line `# docs-test: run`. Mark a command that way when you add one to the
docs and it needs no API key.

The deploy workflow (`.github/workflows/docs.yml`) builds with `--strict` on
every pull request and publishes to this repository's GitHub Pages on a push
to `main`.
