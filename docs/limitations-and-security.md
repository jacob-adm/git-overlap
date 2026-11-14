# Limitations and security

## Limitations

- **Paths, not content moves.** `git overlap` pairs refs that touch the same path, or a path and
  a path under it (a file `foo` on one branch and `foo/bar` on another). A directory renamed on
  one branch and a file added to the old directory on another share no path, yet can conflict.
  To catch that, give `git pairmerge` every pair, as the Measured p recipe does, not only the
  overlapping ones.
- **Case.** Paths and refs are compared bytewise and case-sensitively, as git's trees are.
  `Foo.txt` and `foo.txt` on two branches are not an overlap, although they collide on a
  case-insensitive file system.
- **Directory/file conflicts.** git reports the file under a generated name, `PATH~<commit id>`,
  and `git pairmerge` prints that name unchanged.
- **Pairs only.** `git pairmerge` merges two refs at a time.
- **Text only.** A clean merge says nothing about whether the result builds or passes its tests.
- **Shallow or unrelated histories.** A ref with no merge base with BASE, or a pair with no merge
  base, is an error (exit 2) whose message contains `no merge base`.
- **Windows.** Program arguments are Unicode there, so a ref that is not valid UTF-8 cannot be
  passed to git: exit 2 with `cannot pass a name that is not valid UTF-8 to git on this
  platform`. git must be an executable; a `.cmd` or `.bat` shim alone is not found.

## Security

- `git footprint` and `git overlap` write nothing. `git overlap` runs no git at all.
- `git pairmerge` writes nothing to the repository. Every git call that can write an object runs
  with `GIT_OBJECT_DIRECTORY` set to a private temporary directory, `git-pairmerge-*` in the
  temporary directory Python chooses (`TMPDIR` if set), and the repository's object directory as
  an alternate, so new objects land in the temporary directory. It is removed on normal exit, on
  error, and on SIGINT, SIGTERM and SIGHUP; only SIGKILL can leave it behind.
- `git pairmerge` reads the refs and objects of the repository, git config,
  `$GIT_DIR/info/attributes`, `core.attributesFile` and the `--attributes` FILE. It does not read
  the worktree, the index, or the `.gitattributes` files committed on the branches, so a branch
  cannot choose how it is merged.
- Merge drivers from git config still run. A driver (`merge.<name>.driver`) is a command; git
  runs it as the user who runs `git pairmerge`, on the three versions of the file, whenever an
  attribute selects it. Check what every driver in the system, global and repository git config
  does before merging branches that are not trusted.
- The commands start git without a shell. A ref or path quoted in an error message is escaped,
  so the message stays one line.
- On a terminal, control bytes in a ref or path are shown as `\xHH`, so a file name from an
  untrusted branch cannot drive the terminal. Records in a pipe, a file or `-z` output are raw;
  escape them yourself before showing them anywhere a terminal will interpret them.
