# SPDX-License-Identifier: Apache-2.0
"""git-overlap: git subcommands that read and write TAB-separated records.

The package holds three commands, each a module with a ``main()`` console-script entry point:
``footprint`` (SPEC.md "git footprint BASE"), ``overlap`` (SPEC.md "git overlap") and
``pairmerge`` (SPEC.md "git pairmerge"). ``_common`` holds what they share: records,
arguments, errors and running git (SPEC.md "Common rules").

Documentation conventions, enforced by tests/test_documentation.py:

* Every module, class and function has a PEP 257 docstring in Google style: a one-line
  summary, then, where relevant, ``Args:``, ``Returns:``, ``Raises:`` and a ``SPEC:`` line
  naming the SPEC.md section the code implements.
* Comments explain why (git quirks, platform behaviour, byte handling, ordering), never
  restate what the code says. Each file carries at least one comment line per ten code lines.
"""

# Single source of the version string, printed by every command's --version
# (SPEC.md "Common rules": `--version` prints `git-<name> <version>`).
__version__ = "0.1.0"
