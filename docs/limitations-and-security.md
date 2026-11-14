# Limitations and security

## Limitations

- **Exact paths.** `git overlap` compares paths exactly. A file `foo` added on one branch and a
  file `foo/bar` on another are not an overlap, although `git pairmerge` reports the pair as a
  conflict. A directory renamed on one branch and a file added to the old directory on another
  share no path either, and also conflict. To catch these, give `git pairmerge` every pair, as
  the Measured p recipe does, not only the overlapping ones.
- **Case.** Paths and refs are compared bytewise and case-sensitively, as git's trees are.
  `Foo.txt` and `foo.txt` on two branches are not an overlap, although they collide on a
  case-insensitive file system. For the same reason `git pairmerge` can report such a pair
  `clean`, as git's trees allow both names, while checking the result out on macOS or Windows
  defaults leaves only one of the two files.
- **Scale.** `git overlap` writes one pair for every two refs that share a path, so n refs on one
  path give n(n-1)/2 pairs: 100 refs give 4,950 and 250 give 31,125. `git pairmerge` runs git
  for each pair, with no limit and no timeout of its own. Bound the input yourself, for example
  with `--with REF`, `head`, or `timeout`.
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
  error, and on SIGINT, SIGTERM and SIGHUP; SIGKILL, a crash of the Python process or a power
  loss can leave it behind. It never holds anything the repository needs, so it is safe to
  delete.
- `git pairmerge` reads the refs and objects of the repository, git config,
  `$GIT_DIR/info/attributes`, `core.attributesFile` and the `--attributes` FILE. It does not read
  the worktree, the index, or the `.gitattributes` files committed on the branches, so a branch
  cannot choose how it is merged.
- Merge drivers from git config still run, by design: that is how a driver such as Mergiraf is
  evaluated. A driver (`merge.<name>.driver`) is a command; git runs it as the user who runs
  `git pairmerge`, on the three versions of the file, whenever an attribute selects it.
- Who can make `git pairmerge` run a program: whoever can write the system, global or
  repository git config, `$GIT_DIR/info/attributes`, `core.attributesFile` or the
  `--attributes` FILE. A branch cannot, because committed `.gitattributes` are not read. This is
  the trust plain git already places in that configuration, which can also run programs
  through hooks and `core.fsmonitor`. `git pairmerge` is not a sandbox: on a CI runner or any
  machine that merges untrusted branches, make sure the git configuration and the files above
  come from you, and check what every configured driver does.
- The commands start git without a shell. A ref or path quoted in an error message is escaped,
  so the message stays one line.
