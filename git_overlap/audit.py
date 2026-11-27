# SPDX-License-Identifier: Apache-2.0
"""--audit-json: one audit result object (format audit-result/1) for git overlap or pairmerge.

Each record becomes a finding that keeps the --json record unchanged under "record" and adds a
rule id, a fixed severity, one sentence and its evidence. A clean pair is listed as "info".
"""

import json
import os

from . import __version__
from ._common import EXIT_ERROR, Fail, _error, write_stdout

# The format id; a change that breaks a reader gets a new number.
FORMAT = "audit-result/1"

# Fixed per rule; see docs/commands.md. Nothing here depends on the files' contents.
SEVERITY = {"overlap": "low", "pairmerge.conflict": "high", "pairmerge.stale": "high",
            "pairmerge.clean": "info"}


def overlap_finding(row, with_ref):
    """The finding for one `git overlap` record."""
    # Overlap reads no git objects, so the evidence is the path and a ref, never a commit.
    return {
        "rule": "git-overlap.overlap",
        "severity": SEVERITY["overlap"],
        "message": "%s and %s both change %s" % (row["ref_a"], row["ref_b"], row["path"]),
        "evidence": {"path": row["path"], "ref": with_ref or row["ref_a"]},
        "record": row,
    }


def pairmerge_finding(row, onto):
    """The finding for one `git pairmerge` record; ONTO is --onto BASE or None."""
    a, b, result, path = row["ref_a"], row["ref_b"], row["result"], row["path"]
    # With --onto the question is a landing order, so the sentence names BASE and the order.
    landing = " when both land on %s, %s first" % (onto, a) if onto else ""
    if result == "clean":
        text = "%s and %s merge cleanly%s" % (a, b, landing)
    elif result == "stale":
        text = "%s or %s no longer merges into %s: conflict in %s" % (a, b, onto, path)
    else:
        text = "%s and %s conflict in %s%s" % (a, b, path, landing)
    evidence = {"ref": a}
    # A clean record has an empty path; the evidence then names only the ref.
    if path:
        evidence["path"] = path
    rule = "pairmerge." + result
    return {
        "rule": "git-overlap." + rule,
        "severity": SEVERITY.get(rule, "high"),
        "message": text,
        "evidence": evidence,
        "record": row,
    }


def run(name, body):
    """Run BODY(state) and write the audit result object; return the exit code.

    BODY fills state["target"] (a dict of target keys) as soon as it knows them, and returns
    the exit code and the findings. A Fail is written to stderr as without --audit-json, and
    becomes the object's one error with no findings.
    """
    state = {"target": {}}
    errors = []
    try:
        result = body(state)
        if isinstance(result, int):
            return result  # -h or --version: printed, nothing audited
        code, findings = result
    except Fail as exc:
        # The same stderr line as without --audit-json, then the object instead of a traceback.
        _error(name, exc)
        code, findings = EXIT_ERROR, []
        errors.append(str(exc))
    # The current directory names the repository; overlap needs no repository to run.
    target = {"kind": "repository", "name": os.path.basename(os.getcwd()) or "."}
    target.update(state["target"])
    doc = {
        "format": FORMAT,
        "tool": {"name": "git-overlap", "version": __version__},
        "target": target,
        "exit": code,
        "findings": findings,
        "errors": [{"message": m} for m in errors],
    }
    # ASCII JSON, as --json writes, so the object is safe on a terminal.
    write_stdout(json.dumps(doc, indent=2, sort_keys=True).encode("ascii") + b"\n")
    return code
