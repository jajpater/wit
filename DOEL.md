Ik wil graag een git voor documenten. We kunnen het dit of doc of wit noemen.
Het moet niet code maar bestanden (pdf/docx/jpg/tif en verder alles) beheren.
Het moet:

- werken vanaf de commandline
- alles opslaan in één centrale repository 
- syncen met andere repositories
- er moet een volledige en gedeeltelijke checkout op een machine gedaan kunnen worden
- per bestand of dir moet in ieder geval beschikbaar zijn: push, pull
- de bestanden en bestandstructuur moet online te browsen zijn
- er moet expliciet bepaald kunnen worden welke bestanden onder beheer komen
- het gaat niet om versie beheer, hoewel dat op termijn zeker van belang kan zijn (bijvoorbeeld: bewaar de laatste 3 versies o.i.d.)
- het mag geen symlinks gebruiken: in de  werkdirectory heb je ede echte bestanden, content-adressable opslag moet volledig intern afgehandeld worden

Bijvoorbeeld

doc add boek.pdf
doc commit
doc push

andere computer:

doc clone
doc pull
doc checkout

De gebruiker merkt alleen dat push, pull, clone en checkout efficiënt zijn, niet hoe dat onder de motorkap wordt bereikt. (Het soort transparantie dat Git voor broncode zo aantrekkelijk maakt.)

Geen symlinks omdat veel programma’s een echt bestandspads verwachten

Als ze een symlink krijgen kunnen dingen misgaan met:

* PDF-readers en annotatieprogramma’s;
* Word/LibreOffice lockfiles;
* indexers zoals Recoll/Spotlight;
* OCR-tools;
* cloudsync-tools;
* back-upprogramma’s;
* scripts die `stat`, inode-info of write-permissions verwachten.

Voor mijn use-case is dat een showstopper. Ik wilt documenten openen, annoteren, doorzoeken, kopiëren en back-uppen alsof het gewone bestanden zijn.

Daarom vallen git-annex en Git LFS feitelijk af.

Het is van belang dat het bestandssysteem de waarheid blijft.

Dus als je in je shell doet:

```bash
cd ~/Bibliotheek
find . -name "*.pdf"
fd calvijn
rg "verbond" *.md
```

of in een GUI:

* een PDF opent in Okular;
* aantekeningen maakt;
* een DOCX bewerkt in LibreOffice;
* een bestand naar een USB-stick sleept;
* Recoll laat indexeren;
* Syncthing of een backup laat draaien;

Dan wil ik niet hoeven nadenken over de vraag: "Is dit eigenlijk wel een echt bestand?"

Bij Git is de working tree gewoon een directory met echte bestanden.

De object store zit in `.git/objects`, maar daar merk je niets van.

Je hoeft nooit te weten hoe Git intern blobs, trees en commits opslaat.

Dat is een heel elegant ontwerp.

Bij git-annex wordt de interne architectuur zichtbaar.

Je ziet ineens:

* symlinks;
* ontbrekende content;
* `git annex get`;
* `git annex drop`;
* meerdere remotes.

Dat zijn allemaal legitieme concepten, maar ze worden onderdeel van je dagelijkse werk.

Mijn ideale systeem moet ongeveer deze eigenschappen hebben:

* De working directory bevat **altijd gewone bestanden**.
* De repository gebruikt intern content-addressable storage.
* Deduplicatie gebeurt automatisch.
* `push`, `pull`, `clone` en `checkout` werken zoals bij Git.
* Er is een webinterface zoals Forgejo.
* Grote bestanden worden efficiënt opgeslagen.
* De gebruiker hoeft nooit over symlinks of object stores na te denken.

Met andere woorden: Git's gebruikersmodel, maar geoptimaliseerd voor documenten in plaats van broncode.

De doelen die git heeft zijn niet (zo) belangrijk:

* mergebaarheid,
* branches,
* tekstuele diffs,
* rebases.

Een grote documentencollectie heeft heel andere prioriteiten:

* het bestand moet zich gedragen als een normaal bestand;
* het archief moet betrouwbaar synchroniseren;
* het moet eenvoudig te doorzoeken zijn;
* de opslag moet efficiënt zijn;
* de repository moet online door te bladeren zijn.

## Architectuur

Het ontwerp scheidt twee lagen strikt:

* **Repositorylaag ("wit")** — de waarheid: een content-addressed object store met
  commits, refs en optimistic concurrency control. Dit is wat we bouwen.
* **Transportlaag (rclone, evt. rsync)** — een *domme* blob-kopieerder die ontbrekende
  objecten verplaatst. Kent geen commits of refs. Dit adopteren we, niet bouwen.

Dit is het model van git's "dumb" transport en van restic/kopia: de semantiek zit lokaal,
het transport kopieert alleen onveranderlijke objecten.

### Objectmodel

Git/restic-minus-packfiles. **Drie** objecttypes, alles content-addressed met **BLAKE3**
(sneller dan SHA-256 op grote bestanden, native streaming/tree-hashing voor geverifieerde
chunk-reads later). Object-id's zijn **zelf-beschrijvend** (`b3:abcd…`, multihash-stijl) en
het algoritme staat in `config.toml`, zodat een toekomstige sha256-modus een config-vlag is
en geen migratiehel.

* `blob` — de inhoud van één bestand, opgeslagen als **ruwe bytes** (geen header, geen
  compressie in v1; PDF/JPG/TIF zijn al gecomprimeerd). Gevolg: `id == b3sum van het losse
  bestand` → extern verifieerbaar met standaardtools. Whole-file voor v1; content-defined
  chunking is een latere optie (dedup levert voor gecomprimeerde formaten toch weinig op).
* `tree` — een directory: `naam → {type, hash, mode, size}`. Canonieke JSON.
* `commit` — een moment in de geschiedenis. Canonieke JSON, vast formaat:

  ```json
  { "tree": "b3:…", "parents": ["b3:…"], "time": "2026-06-20T14:00:00Z",
    "message": "…", "host": "…" }
  ```

  De commit-id is de hash van het commit-object → immutable. `parents` is een lijst en
  **merge-commits (≥ 2 parents) zijn vanaf het begin toegestaan**: de historie is een DAG, geen
  lijn. Dat kost DAG-traversal + merge-base (zie reconcile), maar in ruil bewaart reconcile
  beide historielijnen i.p.v. ze te herschrijven. `time` is deterministisch (RFC3339-UTC of
  epoch-int), want het wordt mee-gehasht. `host` levert de machine-identiteit die het
  conflictschema nodig heeft. ("Snapshot" is informeel taalgebruik voor een commit; het is
  geen apart objecttype.)

De splitsing tree/commit is niet alleen netjes maar **dragend voor dedup**: een ongewijzigde
directory geeft dezelfde tree-hash en wordt over commits heen hergebruikt, terwijl het
commit-object de wisselende historie-metadata (parents, tijd, message) draagt.

De working directory bevat **altijd echte bestanden**; de object store is een aparte,
interne, append-only verzameling hash-genaamde objecten. De gebruiker merkt er niets van.

### Schijflayout

```
.wit/
  HEAD                       → "ref: refs/heads/main"
  config.toml                # object_format_version, hash = "blake3"
  index.sqlite               # herbouwbare cache, geen waarheid
  refs/heads/main            → <commit-id>
  objects/
    blobs/ab/cdef…           # RUWE bytes, id = b3(raw) → extern verifieerbaar
    trees/ab/cdef…           # canonieke JSON
    commits/ab/cdef…         # canonieke JSON
  tmp/                       # zelfde filesystem als objects/ → atomic rename
  locks/                     # flock-doelen (M6)
```

Aparte dirs per objecttype, om twee redenen — de tweede is de belangrijkste:

1. een blob, tree of commit is nooit ambigu;
2. **operationeel**: trees+commits zijn kleine metadata die je vaak *wholesale* wilt; blobs
   zijn dik en haal je *selectief*. Gescheiden dirs maken "haal eerst alle metadata, diff
   lokaal, haal dán de ontbrekende blobs" een directory-niveau rclone-operatie — exact het
   push/pull- én partial-checkout-patroon.

`tmp/` moet binnen `.wit` staan: write-then-rename is alleen atomair op hetzelfde filesystem.

### Ontwerpprincipe: cache vs. waarheid

> **Alles wat een cache is, is herbouwbaar. De waarheid is `objects/` + `refs/`.**

`.wit/index.sqlite` mag in z'n geheel verwijderd worden zonder dat de repository verloren
gaat — `wit fsck --rebuild-index` reconstrueert hem uit `HEAD` + een werkdir-scan. Dit is een
toetssteen bij elke "object of cache?"-twijfel: overleeft de repo het wissen ervan? Zo niet →
object. Zo ja → cache.

Concreet horen **machine-lokale** gegevens daarom in de cache, nooit in objecten:

* `index.sqlite` kolommen: `path, hash, mode, size, mtime_ns, ctime_ns, (device, inode), staged`.
* `(device, inode)` is puur lokale optimalisatie voor verander- en rename-detectie. inode is
  alleen uniek binnen een device, vandaar het paar. Windows/netwerkshares kennen geen
  betrouwbare inode → de index degradeert dan naar `mtime + size`. Het mág onbetrouwbaar zijn,
  juist omdat het cache is; in een tree/commit-object zou het content-addressing breken.

### Refs + optimistic concurrency control

* De remote houdt per branch een `current-ref`, bv. `main → commit abc123`.
* Een `push` van parent A naar nieuw B slaagt **alleen als remote-`main` nog op A staat**
  (compare-and-swap). Staat hij op C, dan wordt de push geweigerd: eerst `pull`/reconcile.

```
local parent: A,  local new: B
remote main = A   → push OK (ref flip A→B)
remote main = C   → push geweigerd (eerst pull)
```

### Push-protocol (crash-veilig)

**Het kernbesluit: de ref-update is de waarheidstransactie.** Objecten mogen vooraf "los"
geüpload zijn; pas als `refs/heads/main` atomair van parent → nieuwe commit gaat, *bestaat* de
nieuwe toestand. Alles daarvóór is onzichtbaar en weggooibaar.

Objecten zijn immutable en content-addressed, dus uploaden is idempotent en onschadelijk.
De volgorde is daarom dwingend:

1. bereken lokaal commit B en zijn objectset;
2. upload de ontbrekende blobs/trees/commit-objecten (skip-if-hash-exists — gratis via CAS);
3. **pas als alles boven staat:** CAS de ref A→B.

Nooit de ref naar B flippen vóór alle objecten van B er staan. Een afgebroken push laat dan
hooguit wat wees-objecten achter, nooit een kapotte ref. (Letterlijk git's "push objects,
then update ref".)

### Reconcile / conflict

3-way merge op het **manifest/tree-niveau** (niet op bestandsinhoud), met de gemeenschappelijke
voorouder-commit als basis:

* zelfde pad aan beide kanten gewijzigd (twee verschillende hashes, beide afwijkend van basis)
  → **conflict** (handmatig oplossen / keep-both);
* nieuw bestand aan beide kanten → **samenvoegen** (union van de namespace);
* rename gedetecteerd via gelijke blob-hash op ander pad → als **move** behandelen.

Dit is tractabel juist omdat we nooit *bytes* van binaire documenten mergen, alleen de
namespace. De reconcile produceert een echte **merge-commit met twee parents** (lokale tip +
remote tip); de gemeenschappelijke voorouder vind je via een **merge-base/LCA-walk** over de
commit-DAG. Geen rebase, dus geen historieverlies — beide lijnen blijven bewaard.

### Remote-interface: objecttransport ≠ ref-opslag

Een remote doet twee fundamenteel verschillende dingen; `push`/`pull` is dus niet de juiste
abstractiegrens. Splits ze, zodat het gevaarlijke deel (atomiciteit) zichtbaar in het type zit:

```python
class ObjectTransport(ABC):   # put(hash) / get(hash) / has(hash)  — dom, idempotent
class RefStore(ABC):          # read_ref(branch) / compare_and_swap_ref(branch, old, new)
class Remote:                 # = ObjectTransport + RefStore
```

* **Objecttransport** kan elke backend (rclone, fs, ssh) — dom en idempotent.
* **Ref-opslag** vereist atomiciteit en kan *niet* elke backend. `FilesystemRemote` en
  `RcloneRemote` implementeren `compare_and_swap_ref` met een *zwakke* garantie (best effort)
  en zijn daarmee eerlijk tweederangs voor multi-writer; `SSHRemote` (flock) is de echte.

Het mooie: je kunt **hybride** draaien — rclone-naar-S3 voor de dikke blobs, een minuscule
SSH-ref-server voor de CAS — wat exact de oplossing is voor "rclone exposeert geen atomaire
ref". Voor M5 volstaat één `FilesystemRemote` (andere map) om byte-identieke pull te bewijzen.

### Transport: rclone, niet rsync

rclone past beter dan rsync, en wel omdat de objecten **immutable content-addressed blobs**
zijn. rsync's sterke punten (in-place delta, rename-detectie) zijn hier irrelevant: een blob
wordt nooit gemuteerd, alleen toegevoegd of overgeslagen op hash. Een CAS-store *is* "een
bucket vol hash-genaamde onveranderlijke bestanden" — rclone's sweet spot (checksum-skip,
`--immutable`, parallelle transfers, tientallen backends). De enige twee dingen die rclone
níet doet — de ref-CAS en de repositorysemantiek — zijn precies wat de "wit"-laag levert.
Dus: **rclone = transport, "wit" = beheer.**

### Bouwvolgorde (MVP-milestones)

Eerst opslagcorrectheid, dan tracking, dan historie, dan pas checkout/transport. Elke
milestone heeft een hard "klaar wanneer"-criterium.

| M | Inhoud | Klaar wanneer | Status |
|---|---|---|---|
| **M0** | object store + `wit fsck` | put/get werkt; hash klopt; corrupt object gedetecteerd; partial write laat geen half object achter | ✅ |
| **M1** | `index.sqlite` + `add` + `status` | untracked/modified correct op een echte map | ✅ |
| **M2** | trees + commits + refs + `log` (DAG-traversal) | commit-DAG loopt, id's stabiel; `log` ordent op tijd met visited-set | ✅ |
| **M3** | `checkout` / materialisatie | **round-trip byte-identiek** (add → commit → werkdir wissen → checkout → bytes gelijk) | ✅ |
| **M4** | hardening: grote bestanden streamend, `.witignore` | TIF van enkele GB zonder geheugenpiek | ✅ |
| **M5a** | `FilesystemRemote`, geen packs | clone/pull vanaf lege map byte-identiek | ✅ |
| **M5b** | `DumbRcloneRemote` (single-writer, geen remote-GC standaard) | mirror/backup naar cloud werkt; best-effort clobber-detectie op de ref | ✅ |
| **M6** | `WitServerRemote`: ref-CAS + locks; reconcile = merge-commit (merge-base/LCA) | push faalt als remote-ref niet meer op parent staat; divergentie → merge-commit, geen historieverlies | ✅ |
| **M7** | packs/batching voor cloud-scale | groot-archief push/pull niet gebottleneckt door per-object-latency | ✅ |

De volledige bouwvolgorde (M0–M7 + lokale GC) is geïmplementeerd en getest. Partial checkout
was **optioneel** gemarkeerd, maar is inmiddels gebouwd (sparse cone, zie hieronder), naast de
volledige materialisatie.

### Voltooid na de MVP

Bovenop de milestones zijn de resterende `DOEL.md`-eisen en een ronde
correctheid/robuustheid uitgevoerd. Alles porcelain-laag + dunne CLI, alleen `blake3` als
runtime-dep, met tests (63 in totaal).

| Fase | Inhoud | Sluit aan op |
|---|---|---|
| **1** | `wit rm [--cached]` — untracken (+ optioneel verwijderen) | "expliciet bepalen welke bestanden onder beheer komen" |
| **2** | read-only **webinterface** (`wit serve`, stdlib `http.server`): branches/commits/trees bladeren, blobs streamend serveren | "bestanden en bestandstructuur online te browsen" |
| **3** | **gedeeltelijke (sparse) checkout** (`.wit/sparse`, `wit sparse set/list`); `status` ziet uitgesloten paden niet als verwijderd | "volledige en gedeeltelijke checkout" |
| **4** | **retentie "bewaar laatste N"** (`wit gc --keep N`) via een shallow-grens (`.wit/shallow`) die `log` en GC-mark afkappen | "bewaar de laatste 3 versies o.i.d." |
| **5** | **integriteit in transit**: `ObjectStore.ingest` herhasht vóór de atomic rename en weigert bij mismatch; rclone-bulkpaden krijgen een verificatiepass | crash-/corruptieveiligheid |
| **6** | **conflict-status** in de index: `reconcile` schrijft keep-both-paden weg, `status` toont een Conflicten-groep tot ze opgelost zijn | reconcile-UX (ontwerpbesluit #5) |
| **7** | **smart-server GC**: `WitServerRemote.gc()` markt vanaf de remote-refs en veegt onder dezelfde `flock` als de ref-CAS | tweede heilige servertaak (ontwerpbesluit #6) |
| **8** | **genest `.witignore`**: elke map mag regels hebben voor zijn subboom; root-regels blijven globaal | tracking-fijnafstemming |

Bewust **niet** in scope (zoals eerder afgesproken): een echte netwerkdaemon voor `wit-server`
(nu lokaal filesystem; de flock-logica is de kern), schrijf-endpoints in de webinterface
(read-only by design), content-defined chunking / echte packfiles (M7-batching volstaat), en
shallow **clone/fetch** over het netwerk (retentie is een lokale opruiming).

**Smart vs. dumb remote** — de eerlijke scheiding:

```
smart remote (wit-server)  = veilig multi-writer + GC
dumb remote (fs / rclone)  = single-writer / backup / mirror
```

Een dumbe remote kan een tweede schrijver niet *voorkomen*, alleen *detecteren* (best-effort
read-after-write op de ref). "Single-writer" is daar dus een belofte die jíj geeft, geen
garantie die de tool afdwingt.

### Ontwerpbesluiten & open vragen

**1. De dragende vraag: atomaire compare-and-swap op de remote ref** (M6).
rclone/rsync naar domme opslag geeft géén atomaire "set main=B if main==A". Het hele
OCC-schema hangt hierop. Twee werkbare routes:

* **SSH + minuscuul server-side script** dat onder een `flock` de vergelijk-en-wissel doet.
  Werkt op elke SSH-server; prijs: een sliver server-side logica, *alleen* voor de ref-update.
* **Backend met native conditional write** (S3 `If-Match`/`If-None-Match`, GCS
  `x-goog-if-generation-match`). Echte CAS zonder eigen daemon, maar rclone exposeert die
  preconditie niet rechtstreeks — daarvoor de backend-SDK apart aanspreken.

Lockfiles op een eventually-consistent store: vermijden (racy). **Te beslissen vóór M6.**
*Stand:* de **flock**-route is gebouwd in `WitServerRemote` (lokaal filesystem) en bewijst de
exactly-one-winner-semantiek; de echte netwerkvarianten (SSH-script, S3 `If-Match`) blijven open
en zijn een transport-/deploykeuze, niet een wijziging van de wit-laag.

**2. Remote-protocol: object-per-file vs packfiles** (M5).
Het mechanisme is grotendeels beslist door de typed dirs: "haal alle metadata wholesale
(`objects/trees/` + `objects/commits/` zijn klein), diff blob-hashes lokaal, haal dán de
ontbrekende blobs". Maar een groot archief = miljoenen kleine tree-objecten; over
rclone-naar-cloud met per-operatie-latency wordt dat traag. **Batching/packing** is een open
beslissing — uitstellen (M5 op fs-remote zonder packs), maar nu benoemd. *Stand:* M7-**batching**
is gebouwd (`rclone copy --files-from` → O(1) calls i.p.v. per object; metadata wholesale via
`fetch_metadata`). Echte packfiles / content-defined chunking blijven bewust buiten scope.

**3. Garbage collection — conservatief beleid (besloten).**
"Bewaar laatste 3 versies" = mark-and-sweep vanaf de refs, maar nooit onmiddellijk verwijderen:

```
mark → grace-periode → sweep
```

De grace-periode is een **royaal vast venster** (dagen — vgl. git's `gc.pruneExpire` default van
2 weken), *niet* "de maximale push-duur": die is niet begrensbaar, want een multi-GB-push over
een trage link kan uren duren. Het venster dekt de GC↔push-race: een net-geüploade blob is jonger
dan T en wordt niet geveegd. Reikwijdte per remote-type:

* **lokale GC:** toegestaan; *geïmplementeerd* (`wit gc`, mark → grace → sweep);
* **smart remote (wit-server):** *geïmplementeerd* — `WitServerRemote.gc()` markt vanaf de
  remote-refs en veegt onder dezelfde `flock` als de ref-CAS;
* **dumb remote:** standaard uit (geen veilige plek om reachability + delete te coördineren).
  Gevolg: een dumbe remote is **append-only, onbegrensd groeiend** — prima voor backup/mirror
  (immutability is daar zelfs gewenst), maar "bewaar laatste 3" werkt er niet.

**4. DAG vanaf het begin (besloten).**
Commits mogen ≥ 2 parents hebben — de historie is een DAG, geen lijn. Kosten: `log` als
DAG-traversal (visited-set, ordening op tijd) en een merge-base/LCA-walk voor reconcile. In ruil
is reconcile een echte merge die beide lijnen bewaart, i.p.v. een rebase die de lokale commit als
losse knoop weggooit. Voor een archief waar historieverlies onwenselijk is, is dat de juiste ruil.

**5. Conflictrepresentatie — keep-both (besloten).**
Bij een pad-conflict materialiseren we beide versies als echte bestanden:

```
pad.pdf
pad.conflict-<machine>-<commit>.pdf
```

plus een conflictstatus in `index.sqlite`. Lelijk maar begrijpelijk, en voor binaire documenten
beter dan een abstract merge-model (we mergen toch geen bytes). Beide bestanden landen in de tree
van de **merge-commit**; resolutie-loop: ze bestaan echt → de gebruiker kiest, verwijdert de
andere, commit → conflict opgeheven. *Geïmplementeerd:* `reconcile` schrijft de conflictpaden naar
een `conflicts`-tabel in de index; `wit status` toont een Conflicten-groep tot een pad opnieuw
gestaged (`add`) of verwijderd (`rm`) wordt.

**6. De mini-server — twee heilige taken (besloten).**
Ref-CAS (#1) én GC (#3) willen allebei serverlogica. Voor het volledige doel (veilig
multi-writer) komt er een minimale `wit-server` met precies twee taken:

```
1. atomaire compare-and-swap van refs
2. veilige garbage collection (mark → grace → sweep)
```

De rest blijft domme objectopslag. Cruciaal: **de server houdt zelf géén objectdata** — hij is
pure coördinatie (lock + ref-CAS + GC-worker) die dezelfde domme `objects/`-opslag leest.
Deploy-gevolg: voor GC-reachability heeft de server leestoegang tot `objects/` nodig →
co-loceren met de opslag. Een volledig domme remote blijft veilig voor backup/single-writer,
niet voor het volledige doel. *Geïmplementeerd:* beide taken zitten in `WitServerRemote`
(`compare_and_swap_ref` en `gc()`, beide onder `flock`); een echte netwerkdaemon zou exact deze
logica omhullen — nu draait ze op een lokaal filesystem.

**7. Werkdir vs. object store: dubbele opslag.**
De working dir heeft echte bestanden, de store heeft de blob → in principe 2× opslag. Voor v1:
**volledige kopie** (simpel, robuust). Reflink/CoW als optimalisatie waar het filesystem het
ondersteunt; hardlinks vermijden (in-place edits muteren dan de vermeende immutable blob).

**8. Scope van de webinterface.**
Een volledige Forgejo-achtige UI is enorme scope. Begin read-only (bladeren door commits,
bestanden en structuur) en bouw die laag pas na de opslagkern + `add`/`push`/`pull`.
*Geïmplementeerd:* `wit serve` draait een read-only browser op stdlib `http.server` (geen
nieuwe dependency) met routes voor branches/commits/trees en streamend serveren van blobs;
schrijf-endpoints zijn bewust afwezig.

### Prior art (eerst evalueren)

* **DVC (Data Version Control)** — ligt het dichtst bij deze spec: `dvc add/push/pull/checkout`,
  CLI als git, centrale remote, partial checkout, en een instelbare link-strategie
  (`cache.type = reflink,copy,hardlink`). Met reflink/copy precies "echte bestanden, geen
  symlinks, interne CAS-cache". **Eerst een halve dag op een kopie van de echte Bibliotheek
  proberen** voordat we zelf bouwen.
* **git-annex unlocked-modus** (`git annex adjust --unlock` + `annex.thin`) — geeft tegenwoordig
  echte bestanden via hardlink/CoW, géén symlinks. De premisse dat annex symlinks afdwingt is
  dus deels verouderd. Nadeel: je erft alsnog de git-annex mentale last die we juist willen
  vermijden.
* **Perkeep** — content-addressed personal storage mét web-UI.
* **restic / borg / kopia** — CAS + dedup, maar backup-georiënteerd; "working tree = waarheid"
  ontbreekt.

De toegevoegde waarde van dit project zit niet in "nog een CAS-tool" — die bestaan — maar in
het specifieke gebruikersmodel: git-gemak zonder dat de gebruiker ooit iets van de object
store merkt, plus een prettige webbrowse-laag.

