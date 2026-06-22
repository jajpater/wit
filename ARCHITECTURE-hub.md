# Architecture: the multi-repo host layer ("hub")

This document sketches a **hub**: a layer that hosts many `wit` repositories under one
service, the way GitHub hosts many git repositories. It is a *design sketch*, not yet
implemented.

The guiding constraint mirrors the split between git and GitHub: **the `wit` core stays
single-repo.** The hub adds a registry, a router, and an access policy *on top* of the
existing object store — without changing a single byte of the content-addressed truth.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the per-repo model this builds on.

## Conceptual model

A hub is a directory holding many independent `wit` remotes. One repository is one
existing `WitServerRemote` (the "smart" remote from `remote.py`, with atomic ref-CAS via
`flock`). The hub adds exactly three things the core does not have:

1. a **registry** — which repositories exist, under which name;
2. a **router** — an HTTP layer that mounts the right `ObjectStore` / `RefStore` per
   `<owner>/<name>`;
3. an **access policy** — who may read / push.

None of the three touches objects or refs. The truth stays per repo in `objects/` +
`refs/`, exactly as today.

## Disk layout

```text
/srv/wit/                         # the hub root
  hub.toml                        # config: bind address, auth-mode, default visibility
  registry.sqlite                 # CACHE: owner/name → path, description, visibility
  repos/
    alice/
      library.wit/                # = an ordinary .wit/ (objects, refs, locks, …)
        objects/ refs/ locks/ tmp/ config.toml
        repo.toml                 # hub metadata: description, default_branch, visibility
      photos.wit/
    bob/
      thesis.wit/
  tokens/                         # or: delegate to reverse proxy / SSH authorized_keys
```

`registry.sqlite` is **rebuildable** by scanning `repos/*/*.wit/` — so it is cache, not
truth (the same touchstone as `index.sqlite`: does the repo survive its deletion? Yes →
cache). The truth of "which repos exist" is simply the directory structure; the
per-repo `repo.toml` carries descriptive metadata.

## New module `wit/hub.py`

```python
@dataclass
class RepoRef:
    owner: str
    name: str
    path: Path            # .../repos/<owner>/<name>.wit
    visibility: str       # "public" | "private"

class Hub:
    def __init__(self, root: Path): ...

    # registry = cache; this scans repos/ and (re)builds registry.sqlite
    def rescan(self) -> None: ...
    def list(self, viewer: Principal | None) -> list[RepoRef]: ...
    def resolve(self, owner: str, name: str) -> RepoRef | None: ...

    # repo management: simply lays out an empty WitServerRemote directory
    def create(self, owner: str, name: str, visibility="private") -> RepoRef: ...
    def delete(self, owner: str, name: str) -> None: ...

    # the bridge to the existing core — no new storage logic:
    def remote_for(self, ref: RepoRef) -> WitServerRemote:
        return WitServerRemote(ref.path)        # reuses CAS + gc() as-is
    def store_for(self, ref: RepoRef) -> ObjectStore:
        return ObjectStore(ref.path)            # for the read-only viewer
```

`create()` does no more than lay out a directory — push creates the object structure on
its own (as today: "you do not have to initialize a remote").

## The router (web layer, building on `web.py`)

`web.py` currently takes one `wit: Path`. The hub generalizes that to a prefix router;
the existing render functions (`render_index`, `render_tree`, `render_commit`,
`_serve_blob`) stay **unchanged** — they only get a different `ObjectStore` injected.

```text
GET  /                         → hub index: list of visible repos
GET  /<owner>/<name>/          → existing render_index(store, wit)   [read-only]
GET  /<owner>/<name>/tree/…    → render_tree(...)
GET  /<owner>/<name>/blob/…    → _serve_blob(...)
```

In `_Handler.do_GET` you split `<owner>/<name>` off the path, `hub.resolve(...)`, and
delegate to the current functions with `hub.store_for(ref)`. The viewer stays read-only
— exactly the current scope choice (decision #8 in ARCHITECTURE.md).

## Push/pull over the network — the real extension

This is the only part `wit` does not have today: remotes are currently **filesystem
paths**. A GitHub-style host needs a network endpoint that exposes, *per repo*, exactly
the two existing abstractions:

```text
ObjectTransport                       RefStore
  HEAD  …/objects/<kind>/<oid>  (has)     GET  …/refs/<branch>    (read_ref)
  GET   …/objects/<kind>/<oid>  (download) POST …/refs/<branch>   (compare_and_swap)
  PUT   …/objects/<kind>/<oid>  (upload)        body: {expected, new}
  GET   …/objects/<kind>/       (list)
```

Two new classes go with it, both implementing the **existing** ABCs, so `sync.py`
(push/pull/clone) need not change:

* **Server side** in the hub: translates these routes through to the repo's
  `WitServerRemote`. The CAS-POST therefore still goes through `flock` — atomicity stays
  in one place, as intended.
* **Client side** `HttpRemote(Remote)` in `remote.py`: `make_remote()` grows a branch
  `https://host/owner/name` → `HttpRemote(...)`. The `upload_objects` / `download_objects`
  bulk paths map nicely onto a batched PUT, just like the rclone override.

```python
# make_remote() addition
if spec.startswith(("http://", "https://")):
    from .http_remote import HttpRemote
    return HttpRemote(spec)
```

Hybrid stays possible and even becomes natural: heavy blobs via an `rclone:` backend, the
ref-CAS via the hub's HTTP endpoint — exactly the "split transport from ref storage"
reasoning from ARCHITECTURE.md.

## Access policy — deliberately outside the core

Authentication/authorization belongs in the **router**, not in `objects.py` / `refs.py`
(otherwise you leak policy into the content-addressed truth). Minimal form:

* `private` repos do not appear in `GET /` and refuse transport without a valid principal;
* a `Principal` from a token (`tokens/`) or, simpler, **delegate entirely**: put the hub
  behind a reverse proxy (basic-auth / OIDC) or expose only the SSH path and use
  `authorized_keys` + per-key `command=`. Then the hub itself stays policy-free.

Write right = may this principal `compare_and_swap` on `refs/heads/*` of this repo. Read
right = may download / `list_objects` / see the viewer.

## CLI surface: a separate `wit-hub` entry point

Keep server administration separate from the ordinary `wit` porcelain:

```text
wit-hub init  /srv/wit                      # lays out hub.toml + repos/
wit-hub create alice/library --public
wit-hub list                                # reads the registry cache
wit-hub rm alice/library
wit-hub serve --host 0.0.0.0 --port 8080    # the router above
wit-hub gc [alice/library]                  # calls WitServerRemote.gc()
wit-hub fsck                                # per-repo existing fsck
```

The user side barely changes:

```bash
wit clone https://hub.example/alice/library lib
cd lib && wit push          # CAS via the hub, blobs via http or rclone hybrid
```

## Why this fits

| Hub component        | Reused / new                                             |
|----------------------|----------------------------------------------------------|
| Per-repo storage, CAS, GC | **existing** `WitServerRemote`, unchanged           |
| Push/pull semantics  | **existing** `sync.py`, unchanged                        |
| Read-only viewer     | **existing** `web.py` render functions, other store      |
| Registry             | **new**, but cache (rebuildable by scan)                 |
| Network endpoint     | **new** `HttpRemote` + server routes, implement existing ABCs |
| Auth                 | **new**, in the router or delegated to proxy / SSH       |

So the `wit` core remains the "git", and the hub is the thin "GitHub" around it: a
registry + router + policy, without changing a single byte of the content-addressed
truth.

## Implementation status

| Part | Module | Status |
|------|--------|--------|
| Repo lifecycle + registry | `hub.py` | done (registry is a live directory scan) |
| HTTP router + viewer | `hubserver.py`, `web.py` (`base`) | done |
| Object transport client | `http_remote.py` | done |
| Batched transport (M7) | `wire.py` + `objects`/`fetch` routes | done (one request per direction; bounded memory) |
| Access policy (token / open) | `access.py` | done |
| `wit-hub` CLI | `hubcli.py` | done (`init`/`create`/`rm`/`list`/`token`/`serve`/`gc`) |
| `registry.sqlite` cache | — | TODO (scan suffices until repo counts grow) |
| Token scopes / multi-owner / SSH | — | TODO (single owner-per-repo today) |

The batched protocol is deliberately simple: a stream of `"<kind> <oid> <length>\n"`
+ raw-bytes records (see `wire.py`), one request to upload all missing objects and one
to fetch them, instead of a round-trip per object.
