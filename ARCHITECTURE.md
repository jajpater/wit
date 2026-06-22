# Architecture and Design

This document details the technical architecture, design decisions, and object model of `wit`, based on the original goals outlined in `DOEL.md`.

## Architecture

The design strictly separates two layers:

* **Repository layer ("wit")** — the truth: a content-addressed object store with commits, refs, and optimistic concurrency control. This is what we build.
* **Transport layer (rclone, optionally rsync)** — a *dumb* blob copier that moves missing objects. It does not know about commits or refs. We adopt this, we don't build it.

This is the model of git's "dumb" transport and of restic/kopia: the semantics are local, and the transport only copies immutable objects.

## Object Model

Similar to Git/restic minus packfiles. There are **three** object types, all content-addressed with **BLAKE3** (faster than SHA-256 on large files, native streaming/tree-hashing for verified chunk-reads later). Object IDs are **self-describing** (`b3:abcd…`, multihash style) and the algorithm is specified in `config.toml`, so a future sha256 mode is just a config flag and not a migration nightmare.

* **`blob`** — the contents of one file, stored as **raw bytes** (no header, no compression in v1; PDF/JPG/TIF are already compressed). Consequence: `id == b3sum of the standalone file` → externally verifiable with standard tools. Whole-file for v1; content-defined chunking is a later option (dedup yields little for compressed formats anyway).
* **`tree`** — a directory: `name → {type, hash, mode, size}`. Canonical JSON.
* **`commit`** — a snapshot in history. Canonical JSON, fixed format:

  ```json
  {
    "tree": "b3:…",
    "parents": ["b3:…"],
    "time": "2026-06-20T14:00:00Z",
    "message": "…",
    "host": "…"
  }
  ```

  The commit ID is the hash of the commit object → immutable. `parents` is a list and **merge commits (≥ 2 parents) are allowed from the start**: history is a DAG, not a line. This costs DAG traversal + merge-base (see reconcile), but in return reconcile preserves both history lines instead of rewriting them. `time` is deterministic (RFC3339-UTC or epoch-int) because it gets hashed along. `host` provides the machine identity needed for the conflict schema. ("Snapshot" is informal terminology for a commit; it is not a separate object type.)

The tree/commit split is not only elegant but **load-bearing for deduplication**: an unchanged directory yields the same tree hash and is reused across commits, while the commit object carries the changing history metadata (parents, time, message).

The working directory contains **always real files**; the object store is a separate, internal, append-only collection of hash-named objects. The user does not notice it.

## Disk Layout

```text
.wit/
  HEAD                       → "ref: refs/heads/main"
  config.toml                # object_format_version, hash = "blake3"
  index.sqlite               # rebuildable cache, not the truth
  refs/heads/main            → <commit-id>
  objects/
    blobs/ab/cdef…           # RAW bytes, id = b3(raw) → externally verifiable
    trees/ab/cdef…           # canonical JSON
    commits/ab/cdef…         # canonical JSON
  tmp/                       # same filesystem as objects/ → atomic rename
  locks/                     # flock targets
```

Separate directories per object type, for two reasons — the second being the most important:
1. A blob, tree, or commit is never ambiguous.
2. **Operational**: trees+commits are small metadata that you often want *wholesale*; blobs are heavy and you fetch them *selectively*. Separate dirs make "fetch all metadata first, diff locally, then fetch the missing blobs" a directory-level rclone operation — exactly the push/pull and partial-checkout pattern.

`tmp/` must reside inside `.wit`: write-then-rename is only atomic on the same filesystem.

## Design Principle: Cache vs. Truth

> **Everything that is a cache is rebuildable. The truth is `objects/` + `refs/`.**

`.wit/index.sqlite` may be deleted entirely without losing the repository — `wit fsck --rebuild-index` reconstructs it from `HEAD` + a working dir scan. This is a touchstone for any "object or cache?" doubt: does the repo survive its deletion? If not → object. If yes → cache.

Concretely, **machine-local** data therefore belongs in the cache, never in objects:
* `index.sqlite` columns: `path, hash, mode, size, mtime_ns, ctime_ns, (device, inode), staged`.
* `(device, inode)` is purely a local optimization for modification and rename detection. inode is only unique within a device, hence the pair. Windows/network shares lack a reliable inode → the index then degrades to `mtime + size`. It *may* be unreliable precisely because it is cache; putting it in a tree/commit object would break content-addressing.

## Refs + Optimistic Concurrency Control

* The remote keeps a `current-ref` per branch, e.g. `main → commit abc123`.
* A `push` from parent A to new B succeeds **only if remote-`main` is still on A** (compare-and-swap). If it is on C, the push is rejected: `pull`/reconcile first.

## Push Protocol (Crash-Safe)

**The core decision: the ref update is the truth transaction.** Objects may be uploaded "loosely" beforehand; only when `refs/heads/main` goes atomically from parent → new commit, does the new state *exist*. Everything before that is invisible and discardable.

Objects are immutable and content-addressed, so uploading is idempotent and harmless. The order is therefore strictly enforced:
1. Calculate commit B and its object set locally;
2. Upload the missing blobs/trees/commit objects (skip-if-hash-exists — free via CAS);
3. **Only when everything is up there:** CAS the ref A→B.

Never flip the ref to B before all objects of B are present. An aborted push will then at most leave some orphan objects behind, never a broken ref.

## Reconcile / Conflict

A 3-way merge at the **manifest/tree level** (not on file contents), using the common ancestor commit as the base:
* Same path changed on both sides (two different hashes, both deviating from base) → **conflict** (resolve manually / keep-both);
* New file on both sides → **merge** (union of the namespace);
* Rename detected via equal blob hash on different path → treat as **move**.

This is tractable exactly because we never merge *bytes* of binary documents, only the namespace. The reconcile produces a real **merge commit with two parents** (local tip + remote tip); the common ancestor is found via a **merge-base/LCA-walk** over the commit DAG. No rebase, so no history loss — both lines are preserved.

## Remote Interface: Object Transport ≠ Ref Storage

A remote does two fundamentally different things; `push`/`pull` is thus not the right abstraction boundary. Split them so that the dangerous part (atomicity) is visible in the type:

```python
class ObjectTransport(ABC):   # put(hash) / get(hash) / has(hash)  — dumb, idempotent
class RefStore(ABC):          # read_ref(branch) / compare_and_swap_ref(branch, old, new)
class Remote:                 # = ObjectTransport + RefStore
```

* **Object transport** can be any backend (rclone, fs, ssh) — dumb and idempotent.
* **Ref storage** requires atomicity and *cannot* be any backend. `FilesystemRemote` and `RcloneRemote` implement `compare_and_swap_ref` with a *weak* guarantee (best effort) and are strictly second-class for multi-writer; `SSHRemote` (flock) is the real deal.

You can run **hybrid** — rclone-to-S3 for the heavy blobs, a tiny SSH-ref-server for the CAS — exactly the solution for "rclone does not expose atomic ref".

## Transport: rclone, not rsync

rclone fits better than rsync because the objects are **immutable content-addressed blobs**. rsync's strengths (in-place delta, rename detection) are irrelevant here: a blob is never mutated, only added or skipped by hash. A CAS store *is* "a bucket full of hash-named immutable files" — rclone's sweet spot (checksum-skip, `--immutable`, parallel transfers, dozens of backends). The only two things rclone doesn't do — the ref CAS and repository semantics — are exactly what the "wit" layer provides.
So: **rclone = transport, "wit" = management.**

## Key Design Decisions

1. **Atomic compare-and-swap on the remote ref.** The entire OCC schema depends on this. Implemented via local filesystem `flock` in `WitServerRemote`; real network variants (SSH script, S3 `If-Match`) remain as transport/deploy choices.
2. **Remote protocol: object-per-file vs packfiles.** Large archives with millions of trees can be slow over rclone. Addressed via **batching** (`rclone copy --files-from`), avoiding per-object latency. Actual packfiles / content-defined chunking remain out of scope.
3. **Garbage Collection.** Conservative mark-and-sweep. GC uses a **generous grace period** to avoid deleting objects being pushed. Dumb remotes do not run GC (append-only), making them great for backups but unable to perform retention pruning.
4. **DAG from the start.** Commits can have ≥ 2 parents. Reconcile preserves history via real merge commits instead of rebasing.
5. **Conflict representation (keep-both).** Path conflicts materialize both versions as real files (`file.pdf` and `file.conflict-<machine>-<commit>.pdf`) and mark conflict status in the index, handled as an explicit manual resolution process.
6. **The mini-server.** For safe multi-writer scenarios, a minimal `wit-server` has exactly two tasks: atomic CAS of refs and safe garbage collection. It coordinates via `flock` but holds no object data itself.
7. **Working dir vs. object store.** Dual storage for v1: full copy in working dir, full copy in object store. Reflink/CoW is an optimization depending on filesystem support.
8. **Web interface scope.** Started strictly as a read-only viewer (`wit serve`) for browsing commits, files, and structure.
