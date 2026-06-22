# wit — a git for documents

`wit` manages **files** (pdf, docx, jpg, tif, … everything) like git manages source code:
one central repository, content-addressed storage, push/pull/clone/checkout. The big
difference with git-annex and Git LFS: **your working directory always contains real files, never symlinks**. You open, annotate, search, and back them up like regular files; you will never notice the internal object store.

The complete design is in [ARCHITECTURE.md](ARCHITECTURE.md). This is the practical manual.

---

## Installation

`wit` requires Python ≥ 3.11 and one dependency (`blake3`).

```bash
cd wit
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

After that, the `wit` command is available (as long as the venv is active). Test it:

```bash
wit --help
```

> Without activating the venv, you can also use `./.venv/bin/wit …`.

---

## In one minute

```bash
mkdir library && cd library
wit init                      # create an empty repository (.wit/)
echo "hello" > book.txt
wit add book.txt              # put the file under management
wit commit -m "first import"  # record the state
wit log                       # view the history
```

That is the entire core: `init` → `add` → `commit`. The rest below is extension.

---

## The basic workflow

### `wit init`
Creates a `.wit/` directory in the current directory. That is your repository; otherwise, you only see your
own files.

### `wit add <path>…`
Puts files or entire directories under management. A directory is traversed recursively.

```bash
wit add book.txt              # one file
wit add articles/             # an entire directory
wit add .                     # everything in the current directory
```

What you do not want to include, you put in a `.witignore` (see below).

### `wit status`
Shows what has changed compared to what you have committed: new (untracked), modified,
added (staged) and deleted.

### `wit commit -m "message"`
Records the current state as a **commit** (an immutable snapshot). Each
commit has a unique id and refers to its predecessor(s).

### `wit log`
Shows the commit history, newest first.

### `wit rm <path>…`
Removes a file from management **and deletes it** from your working directory. Do you want to keep the file
and only "untrack" it?

```bash
wit rm --cached old.txt       # remove from management, file remains on disk
```

The next `commit` will reflect the removal automatically.

---

## Restoring: checkout

`wit checkout` writes the files of a commit back to your working directory — as **real
files**. This is the "disaster test": throw everything away and bring it back.

```bash
rm -rf book.txt articles      # empty working directory
wit checkout                  # restore HEAD (byte-identical)
```

Pass a commit-id to restore an older state:

```bash
wit checkout b3:a6e2cff5…
```

---

## Partial checkout (sparse)

Do you have a massive collection but only want to materialize a part on this machine? Set
a **sparse cone**: only paths within those prefixes are checked out.

```bash
wit sparse set articles/      # only materialize this subdirectory
wit sparse list               # show the current cone
wit sparse set                # empty = everything again
```

`wit checkout` respects the cone, and `status` does not see the excluded paths as
"deleted". Handy on a laptop with little disk space.

---

## Synchronizing with another location

A **remote** is a second copy of the repository — another directory, a disk, or a
cloud backend via [rclone](https://rclone.org/).

### Types of remotes (smart vs. dumb)

Just like with git, we distinguish between **dumb** and **smart** remotes:
- **Dumb remotes** only store files. This is fine for backups or if you are working on it alone, but less safe if two people push changes at the same time.
- **Smart remotes** understand what a 'push' is and actively prevent data from getting mixed up when multiple people send changes simultaneously.

| Spec | Type | Meaning |
|---|---|---|
| `/path/to/remote` or `fs:/path` | Dumb | A regular directory (local or on a mounted disk). |
| `server:/path` | Smart | Same directory, but safe to use if multiple people push to it at the same time. |
| `rclone:b2:bucket/repo` | Dumb | Any rclone backend (S3, B2, Drive, SFTP, WebDAV, …). |

### Push, clone, pull

```bash
# machine A: send your repository to the remote
wit push /path/to/remote

# machine B: fetch the entire repository
wit clone /path/to/remote library
cd library

# later: fetch new commits
wit pull
```

**How do you create a remote?**
You don't! You don't have to initialize a remote beforehand. As soon as you push to a path for the first time (locally, on a server, or via rclone), `wit` automatically creates the necessary storage structure there. After a first `push` or `clone`, `wit` remembers the remote, so you can simply type `wit push` / `wit pull` without a path afterwards.

`push` is crash-safe: all objects are uploaded first, and only as the last step does the branch pointer jump. An aborted push leaves at most some unused objects behind, never a broken repository.

### If push is rejected

If someone else has pushed in the meantime, `wit push` refuses (non-fast-forward). First do `wit pull`: concurrent changes are merged. If both sides modify the **same** file, your own version stays on the original name and the other one appears next to it as `file.conflict-<machine>-<commit>.ext`. `wit status` then shows a **Conflicts** group; you pick the correct version, delete the other, and do `add` + `commit` to resolve it.

---

## Browsing online

```bash
wit serve                     # defaults to http://127.0.0.1:8000
wit serve --port 8137 --host 0.0.0.0
```

Open the URL in your browser: browse through commits, directories, and files, and download files.
The web interface is **read-only** (no write actions), exactly to be able to share safely.

---

## Hosting many repositories (`wit-hub`)

A single `wit` repository is to `wit` what one git repository is to git. A **hub**
is the layer on top that hosts *many* repositories under one service — the way
GitHub hosts many git repositories. It adds nothing to the repository format: each
hosted repo is an ordinary `wit` repository, plus a registry, an HTTP router, and an
access policy. The full design is in [ARCHITECTURE-hub.md](ARCHITECTURE-hub.md).

### Setting up a hub

```bash
wit-hub --root /srv/wit init                 # lay out an empty hub
wit-hub --root /srv/wit create alice/library --public   # a hosted repo (owner/name)
wit-hub --root /srv/wit create alice/notes              # private (the default)
wit-hub --root /srv/wit list                 # show hosted repos
wit-hub --root /srv/wit serve --host 0.0.0.0 --port 8080
```

`--root` may be omitted if you set `$WIT_HUB_ROOT`. `serve` falls back to the `host`
and `port` from the hub's `hub.toml`.

### Using a hosted repo

From any machine, a hub URL works as a remote — `clone`, `push`, `pull` as usual:

```bash
wit clone http://hub.example:8080/alice/library lib
cd lib
# … edit, add, commit …
wit push                       # remembers the hub URL after the first push/clone
```

### Access: tokens

By default a hub runs in **token** mode: `public` repositories can be read and cloned
by anyone, but reading a `private` repo and **every push** require a token whose owner
matches the repo's owner.

```bash
wit-hub --root /srv/wit token add alice      # prints a fresh token for owner "alice"
```

Clients pass it through the environment:

```bash
export WIT_TOKEN=<the-token>
wit push http://hub.example:8080/alice/library
```

Set `auth_mode = "open"` in `hub.toml` to disable built-in auth entirely — appropriate
on a trusted LAN, or when a reverse proxy in front of the hub handles authentication.

### Browsing and maintenance

`serve` also exposes the same read-only web viewer as `wit serve`, now per repo:
`http://hub.example:8080/` lists the repositories a viewer may see, and
`/<owner>/<name>/` browses one. Retention runs per repo:

```bash
wit-hub --root /srv/wit gc alice/library     # one repo
wit-hub --root /srv/wit gc                    # all repos
```

---

## Cleaning up versions (retention)

`wit` is not a full version control system, but it does remember your history. Do you only want to keep the last
few versions and clean up the rest?

```bash
wit gc --keep 2               # keep the last 2 commits, clean up older ones
wit gc                        # regular cleanup of unused objects
```

This is a **local** cleanup. A remote with a full history remains full; you can
still push normally after cleaning up.

> `gc` does not delete immediately: recently written objects are protected by a grace window
> (default ~2 weeks). During experimentation, you can use `--grace 0` to skip that.

---

## Checking if everything is correct

```bash
wit fsck                      # recalculate all hashes; reports corruption
```

Because each object is named after its own BLAKE3 hash, corruption is immediately detectable.
With `pull`/`clone`, every incoming object is additionally verified before it ends up in the
store.

---

## `.witignore`

Just like `.gitignore`. One per directory is allowed; rules in a subdirectory only apply to that subdirectory.

```
*.tmp           # ignore all .tmp files (at any level)
build/          # ignore the build/ directory and everything in it
/only-root      # only in the directory where this .witignore is located
```

Ignore only applies to not-yet-tracked files. A file you explicitly name
(`wit add file.tmp`) is always added, even if a pattern would ignore it.

---

## Debug commands

For those who want to look under the hood:

```bash
wit hash-object book.txt      # show the BLAKE3 id of a file
wit hash-object -w book.txt   # … and save it as a blob
wit cat-object blobs b3:…     # write the raw bytes of an object to stdout
```

---

## Cheat sheet

| Command | Purpose |
|---|---|
| `wit init` | new repository |
| `wit add <path>` | put under management |
| `wit rm [--cached] <path>` | remove from management |
| `wit status` | what has changed |
| `wit commit -m "…"` | record state |
| `wit log` | show history |
| `wit checkout [commit]` | restore files |
| `wit sparse set/list` | partial checkout |
| `wit clone <remote> <dir>` | fetch repository |
| `wit push [remote]` | send changes |
| `wit pull [remote]` | fetch changes |
| `wit serve` | start web interface |
| `wit gc [--keep N]` | clean up / retention |
| `wit fsck` | check integrity |

### Hub (`wit-hub`)

| Command | Purpose |
|---|---|
| `wit-hub init` | new hub at `--root` |
| `wit-hub create <owner>/<name> [--public]` | host a repository |
| `wit-hub rm <owner>/<name>` | delete a hosted repository |
| `wit-hub list` | list hosted repositories |
| `wit-hub token add <owner>` | create an access token |
| `wit-hub token list` | list tokens |
| `wit-hub serve [--host --port]` | start the hub HTTP server |
| `wit-hub gc [<owner>/<name>]` | retention (one repo or all) |

---

## For developers

```bash
.venv/bin/python -m pytest -q   # the full test suite
```

The code is layered: a thin CLI (`wit/cli.py`) on top of a porcelain layer
(`wit/porcelain.py`, `wit/sync.py`) on top of modules per object type (objects, trees, commits,
refs, index). Only `blake3` is a runtime dependency; the rest is Python stdlib.
