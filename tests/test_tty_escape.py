# SPDX-License-Identifier: Apache-2.0
"""Control bytes in names are escaped when, and only when, stdout is a terminal in LF mode.

SPEC: "Common rules" (Records, Terminal output). The terminal is a real pseudo-terminal from the
pty module; nothing is mocked. Pipes and -z must stay byte-identical to the raw records.
"""
import os
import select
import subprocess
import sys
import unittest

# Imported as a top-level module: unittest discovery puts tests/ on sys.path.
from fixtures import PACKAGE_ROOT, Repo, Sandbox

# ESC [ 31 m is a CSI colour; ESC ] 0 ; ... BEL is an OSC that retitles the window.
CSI = b"\x1b[31mred"
OSC = b"\x1b]0;pwned\x07"


def run_on_tty(sandbox, name, args, stdin, cwd):
    """Run `python -m git_overlap.<name>` with stdout on a raw pseudo-terminal.

    Args:
        sandbox: The Sandbox whose environment to use.
        name: The command module, e.g. "overlap".
        args: Command-line arguments.
        stdin: Input bytes, sent through a pipe.
        cwd: The directory to run in.

    Returns:
        A tuple (stdout bytes as the terminal received them, exit status, stderr bytes).
    """
    # Imported here: both modules exist only on POSIX, and the class is skipped elsewhere.
    import pty
    import tty
    master, slave = pty.openpty()
    # Raw mode: no LF -> CRLF translation, so the bytes read back are the bytes written.
    tty.setraw(slave)
    env = dict(sandbox.env, PYTHONPATH=PACKAGE_ROOT)
    argv = [sys.executable, "-m", "git_overlap." + name] + list(args)
    # stdin and stderr stay pipes: only stdout's terminal-ness is under test.
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=slave, stderr=subprocess.PIPE,
                            cwd=cwd, env=env)
    _, err = proc.communicate(stdin, timeout=120)
    # Keep our slave open while reading: macOS discards unread output once every slave
    # handle is closed. The child has exited, so whatever is buffered is all there is.
    chunks = []
    while select.select([master], [], [], 0.2)[0]:
        chunks.append(os.read(master, 65536))
    os.close(slave)
    os.close(master)
    return b"".join(chunks), proc.returncode, err


@unittest.skipIf(os.name == "nt", "the pty module needs POSIX")
class TestTerminalEscape(unittest.TestCase):
    """overlap needs no repository, so it carries most cases; footprint covers the audit's."""

    def setUp(self):
        """Create the sandbox and a directory that is not a repository."""
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.cwd = self.sb.mkdir("no-repo-here")

    def pair_input(self, path):
        """Footprint records in which refs a and b both touch PATH."""
        return b"a\tA\t" + path + b"\nb\tA\t" + path + b"\n"

    def on_tty(self, path, args=()):
        """Run overlap on a terminal for PATH and return what the terminal received."""
        out, code, err = run_on_tty(self.sb, "overlap", list(args), self.pair_input(path),
                                    self.cwd)
        self.assertEqual(code, 1, err)
        return out

    def test_csi_is_escaped_on_a_terminal(self):
        """A colour sequence is shown as text, not obeyed."""
        # Only ESC itself is a control byte; "[31m" is printable and stays as written.
        self.assertEqual(self.on_tty(CSI), b"a\tb\t\\x1b[31mred\n")

    def test_osc_is_escaped_on_a_terminal(self):
        """A window-title sequence, ESC through BEL, is shown as text."""
        self.assertEqual(self.on_tty(OSC), b"a\tb\t\\x1b]0;pwned\\x07\n")

    def test_c1_controls_and_invalid_bytes(self):
        """C1 controls (U+0080-U+009F, here the 8-bit CSI) and invalid UTF-8 are escaped."""
        # UTF-8 for U+009B is C2 9B; FF is never valid UTF-8; DEL is 7F.
        self.assertEqual(self.on_tty(b"c\xc2\x9bd\xffe\x7f"), b"a\tb\tc\\xc2\\x9bd\\xffe\\x7f\n")

    def test_backslash_is_doubled(self):
        """A literal backslash is doubled, so an escape on screen is never ambiguous."""
        # Without doubling, the name `a\x1b` would look the same as a real ESC byte.
        self.assertEqual(self.on_tty(b"a\\x1b"), b"a\tb\ta\\\\x1b\n")

    def test_printable_utf8_unchanged(self):
        """Printable non-ASCII text such as `cafe` with an accent is shown as itself."""
        name = "café/日本".encode("utf-8")
        self.assertEqual(self.on_tty(name), b"a\tb\t" + name + b"\n")

    def test_z_mode_on_a_terminal_is_raw(self):
        """-z output is for programs: raw bytes even on a terminal."""
        out, code, err = run_on_tty(self.sb, "overlap", ["-z"],
                                    b"a\tA\t" + CSI + b"\0b\tA\t" + CSI + b"\0", self.cwd)
        self.assertEqual((out, code), (b"a\tb\t" + CSI + b"\0", 1), err)

    def test_pipe_is_raw(self):
        """Through a pipe the record is the raw bytes, exactly as before."""
        proc = self.sb.run_cmd("overlap", [], stdin=self.pair_input(CSI), cwd=self.cwd)
        self.assertEqual((proc.stdout, proc.returncode), (b"a\tb\t" + CSI + b"\n", 1),
                         proc.stderr)

    def test_footprint_on_a_terminal(self):
        """The audit's case: footprint of a ref adding a file named with a CSI sequence."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"README": "base\n"}, ref="refs/heads/main")
        # A real tree entry whose name holds ESC, as an untrusted branch could carry.
        repo.commit(base, {CSI: "x\n"}, ref="refs/heads/a")
        # A footprint is not a finding: exit 0 on success.
        out, code, err = run_on_tty(self.sb, "footprint", ["main"], b"a\n", repo.path)
        self.assertEqual((out, code), (b"a\tA\t\\x1b[31mred\n", 0), err)


if __name__ == "__main__":
    unittest.main()
