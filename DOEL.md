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
  snapshots, refs en optimistic concurrency control. Dit is wat we bouwen.
* **Transportlaag (rclone, evt. rsync)** — een *domme* blob-kopieerder die ontbrekende
  objecten verplaatst. Kent geen snapshots of refs. Dit adopteren we, niet bouwen.

Dit is het model van git's "dumb" transport en van restic/kopia: de semantiek zit lokaal,
het transport kopieert alleen onveranderlijke objecten.

### Objectmodel

Git/restic-minus-packfiles. Alles content-addressed (sha256):

* `blob` — de inhoud van één bestand. Whole-file voor v1 (dedup op bestandsniveau, één blob =
  één transfer). Content-defined chunking is een latere optie; voor reeds-gecomprimeerde
  PDF/JPG/TIF levert dedup sowieso weinig op.
* `tree` — een directory: `naam → {hash, mode, size}`.
* `snapshot` — `{root-tree-hash, parent-snapshot-id, timestamp, message}`. De snapshot-ID is
  de hash van het snapshot-object → immutable, en identieke snapshots dedupen vanzelf.

De working directory bevat **altijd echte bestanden**; de object store is een aparte,
interne, append-only verzameling hash-genaamde blobs. De gebruiker merkt er niets van.

### Refs + optimistic concurrency control

* De remote houdt per branch een `current-ref`, bv. `main → snapshot abc123`.
* Een `push` van parent A naar nieuw B slaagt **alleen als remote-`main` nog op A staat**
  (compare-and-swap). Staat hij op C, dan wordt de push geweigerd: eerst `pull`/reconcile.

```
local parent: A,  local new: B
remote main = A   → push OK (ref flip A→B)
remote main = C   → push geweigerd (eerst pull)
```

### Push-protocol (crash-veilig)

Objecten zijn immutable en content-addressed, dus uploaden is idempotent en onschadelijk.
De volgorde is daarom dwingend:

1. bereken lokaal snapshot B en zijn objectset;
2. upload de ontbrekende blobs/trees/snapshot-objecten (skip-if-hash-exists — gratis via CAS);
3. **pas als alles boven staat:** CAS de ref A→B.

Nooit de ref naar B flippen vóór alle objecten van B er staan. Een afgebroken push laat dan
hooguit wat wees-objecten achter, nooit een kapotte ref. (Letterlijk git's "push objects,
then update ref".)

### Reconcile / conflict

3-way merge op het **manifest/tree-niveau** (niet op bestandsinhoud), met de gemeenschappelijke
voorouder-snapshot als basis:

* zelfde pad aan beide kanten gewijzigd (twee verschillende hashes, beide afwijkend van basis)
  → **conflict** (handmatig oplossen / keep-both);
* nieuw bestand aan beide kanten → **samenvoegen** (union van de namespace);
* rename gedetecteerd via gelijke blob-hash op ander pad → als **move** behandelen.

Dit is tractabel juist omdat we nooit *bytes* van binaire documenten mergen, alleen de
namespace. Snapshots dragen daarvoor een parent-pointer (een DAG) zodat de voorouder vindbaar is.

### Open ontwerpvragen

**1. De dragende vraag: atomaire compare-and-swap op de remote ref.**
rclone/rsync naar domme opslag geeft géén atomaire "set main=B if main==A". Het hele
OCC-schema hangt hierop. Twee werkbare routes:

* **SSH + minuscuul server-side script** dat onder een `flock` de vergelijk-en-wissel doet.
  Werkt op elke SSH-server; prijs: een sliver server-side logica, *alleen* voor de ref-update.
  De objecten blijven dom gekopieerd.
* **Backend met native conditional write** (S3 `If-Match`/`If-None-Match`, GCS
  `x-goog-if-generation-match`). Echte CAS op het ref-object zonder eigen daemon, maar rclone
  exposeert die preconditie niet rechtstreeks — daarvoor de backend-SDK apart aanspreken.

Lockfiles op een eventually-consistent store: vermijden (racy). **Te beslissen vóór bouw.**

**2. Garbage collection.**
"Bewaar laatste 3 versies" = mark-and-sweep vanaf de refs: verwijder objecten die vanuit geen
enkele ref bereikbaar zijn. Valkuil: GC tijdens een concurrente push (verwijdert een object dat
de push al geüpload waande). Houd het simpel: GC onder dezelfde lock als de ref-update, en
verwijder alleen objecten ouder dan een grace-periode.

**3. Werkdir vs. object store: dubbele opslag.**
De working dir heeft echte bestanden, de store heeft de blob → in principe 2× opslag. Voor v1:
**volledige kopie** (simpel, robuust). Reflink/CoW als optimalisatie waar het filesystem het
ondersteunt; hardlinks vermijden (in-place edits muteren dan de vermeende immutable blob).

**4. Scope van de webinterface.**
Een volledige Forgejo-achtige UI is enorme scope. Begin read-only (bladeren door snapshots,
bestanden en structuur) en bouw die laag pas na de opslagkern + `add`/`push`/`pull`.

### Transport: rclone, niet rsync

rclone past beter dan rsync, en wel omdat de objecten **immutable content-addressed blobs**
zijn. rsync's sterke punten (in-place delta, rename-detectie) zijn hier irrelevant: een blob
wordt nooit gemuteerd, alleen toegevoegd of overgeslagen op hash. Een CAS-store *is* "een
bucket vol hash-genaamde onveranderlijke bestanden" — rclone's sweet spot (checksum-skip,
`--immutable`, parallelle transfers, tientallen backends). De enige twee dingen die rclone
níet doet — de ref-CAS en de repositorysemantiek — zijn precies wat de "wit"-laag levert.
Dus: **rclone = transport, "wit" = beheer.**

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

