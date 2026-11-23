# Contributing

## Tests

```sh
python3 -m unittest discover -s tests
```

The suite builds real git repositories in temporary directories and runs the three commands on
their branches. It also checks the README and recipe examples against what the commands print,
and that every Python file is licensed and documented. It needs git 2.38 or later on `PATH`; the
`git pairmerge` tests skip on a git older than 2.43.

To run it on the oldest git CI tests (2.39, Debian bookworm) and Python 3.9, with Docker:

```sh
docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.9-slim-bookworm sh -c 'apt-get update -qq &&
  apt-get install -y -qq --no-install-recommends git >/dev/null &&
  git --version && python -m unittest discover -s tests -v'
```

## Contributing

Changes reach `main` only through pull requests.

1. Fork the repository and create a branch.
2. Make the change with a test that fails without it, then run `python3 -m unittest discover -s tests`.
3. Open a pull request against `main`. CI runs the tests on Linux, macOS and Windows with Python
   3.9 and 3.13, and on git 2.39 in a container, and builds the package; the required checks must
   pass before merging. Pull requests are merged with a merge commit.

Keep git-overlap standard-library-only, and keep it running nothing but `git`.
