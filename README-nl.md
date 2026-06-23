# wit — een git voor documenten

`wit` beheert **bestanden** (pdf, docx, jpg, tif, … alles) zoals git broncode beheert:
één centrale repository, content-addressed opslag, push/pull/clone/checkout. Het grote
verschil met git-annex en Git LFS: **in je werkmap staan altijd echte bestanden, nooit
symlinks**. Je opent, annoteert, doorzoekt en backupt ze als gewone bestanden; de interne
object store merk je nooit.

Het volledige ontwerp staat in [DOEL.md](DOEL.md). Dit is de praktische handleiding.

---

## Installeren

`wit` heeft Python ≥ 3.11 nodig en één afhankelijkheid (`blake3`).

```bash
cd wit
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Daarna is het commando `wit` beschikbaar (zolang de venv actief is). Test het:

```bash
wit --help
```

> Zonder de venv te activeren kun je ook `./.venv/bin/wit …` gebruiken.

---

## In één minuut

```bash
mkdir bibliotheek && cd bibliotheek
wit init                      # maak een lege repository (.wit/)
echo "hallo" > boek.txt
wit add boek.txt              # neem het bestand onder beheer
wit commit -m "eerste import" # leg de toestand vast
wit log                       # bekijk de historie
```

Dat is de hele kern: `init` → `add` → `commit`. De rest hieronder is uitbreiding.

---

## De basisworkflow

### `wit init`
Maakt een `.wit/`-map aan in de huidige map. Dat is je repository; verder zie je alleen je
eigen bestanden.

### `wit add <pad>…`
Neemt bestanden of hele mappen onder beheer. Een map wordt recursief afgelopen.

```bash
wit add boek.txt              # één bestand
wit add artikelen/            # een hele map
wit add .                     # alles in de huidige map
```

Wat je niet wilt meenemen, zet je in een `.witignore` (zie verderop).

### `wit status`
Toont wat er veranderd is t.o.v. wat je hebt vastgelegd: nieuw (untracked), gewijzigd,
toegevoegd (staged) en verwijderd.

### `wit commit -m "bericht"`
Legt de huidige toestand vast als een **commit** (een onveranderlijk momentpunt). Elke
commit heeft een unieke id en verwijst naar zijn voorganger(s).

### `wit log`
Toont de commit-historie, nieuwste eerst.

### `wit rm <pad>…`
Haalt een bestand uit beheer **en verwijdert het** uit je werkmap. Wil je het bestand laten
staan en alleen "untracken"?

```bash
wit rm --cached oud.txt       # uit beheer, bestand blijft op schijf
```

De eerstvolgende `commit` weerspiegelt de verwijdering vanzelf.

---

## Terughalen: checkout

`wit checkout` schrijft de bestanden van een commit terug naar je werkmap — als **echte
bestanden**. Dit is de "ramp-test": gooi alles weg en haal het terug.

```bash
rm -rf boek.txt artikelen     # werkmap leeggooien
wit checkout                  # HEAD terugzetten (byte-identiek)
```

Geef een commit-id mee om een oudere toestand terug te zetten:

```bash
wit checkout b3:a6e2cff5…
```

---

## Gedeeltelijke checkout (sparse)

Heb je een enorme collectie maar wil je op deze machine maar een deel materialiseren? Stel
een **sparse cone** in: alleen paden binnen die prefixen worden uitgecheckt.

```bash
wit sparse set artikelen/     # alleen deze submap materialiseren
wit sparse list               # toon de huidige cone
wit sparse set                # leeg = weer alles
```

`wit checkout` respecteert de cone, en `status` ziet de uitgesloten paden niet als
"verwijderd". Handig op een laptop met weinig schijfruimte.

---

## Synchroniseren met een andere plek

Een **remote** is een tweede kopie van de repository — een andere map, een schijf, of een
cloud-backend via [rclone](https://rclone.org/).

### Soorten remotes (slim vs. dom)

Net als bij git maken we onderscheid tussen **domme** en **slimme** remotes:
- **Domme remotes** slaan alleen bestanden op. Dit is prima voor back-ups of als je er in je eentje aan werkt, maar minder veilig als twee mensen tegelijk wijzigingen sturen.
- **Slimme remotes** snappen wat een 'push' is en voorkomen actief dat gegevens door elkaar raken als meerdere mensen tegelijkertijd wijzigingen sturen.

| Spec | Soort | Betekenis |
|---|---|---|
| `/pad/naar/remote` of `fs:/pad` | Dom | Een gewone map (lokaal of op een gemounte schijf). |
| `server:/pad` | Slim | Zelfde map, maar veilig om te gebruiken als meerdere mensen er tegelijk naar pushen. |
| `rclone:b2:bucket/repo` | Dom | Elk rclone-backend (S3, B2, Drive, SFTP, WebDAV, …). |

### Push, clone, pull

```bash
# machine A: stuur je repository naar de remote
wit push /pad/naar/remote

# machine B: haal de hele repository op
wit clone /pad/naar/remote bibliotheek
cd bibliotheek

# later: nieuwe commits ophalen
wit pull
```

**Hoe maak je een remote aan?**
Niet! Je hoeft een remote niet vooraf te initialiseren. Zodra je voor het eerst pusht naar een pad (lokaal, op een server of via rclone), maakt `wit` daar automatisch de benodigde opslagstructuur aan. Na een eerste `push` of `clone` onthoudt `wit` de remote, zodat je daarna simpelweg `wit push` / `wit pull` zonder pad kunt typen.

`push` is crash-veilig: eerst worden alle objecten geüpload, en pas als laatste stap
verspringt de branch-pointer. Een afgebroken push laat hooguit wat ongebruikte objecten
achter, nooit een kapotte repository.

### Als push wordt geweigerd

Heeft iemand anders intussen gepusht, dan weigert `wit push` (non-fast-forward). Doe eerst
`wit pull`: gelijklopende wijzigingen worden samengevoegd. Wijzigen twee kanten **hetzelfde**
bestand, dan blijft je eigen versie op de oorspronkelijke naam staan en komt de andere ernaast
als `bestand.conflict-<machine>-<commit>.ext`. `wit status` toont dan een **Conflicten**-groep;
je kiest de juiste versie, verwijdert de andere, en doet `add` + `commit` om het op te lossen.

---

## Online bladeren

```bash
wit serve                     # standaard op http://127.0.0.1:8000
wit serve --port 8137 --host 0.0.0.0
```

Open de URL in je browser: blader door commits, mappen en bestanden, en download bestanden.
Tekst, Markdown, afbeeldingen en PDF's worden **inline** getoond (een `README.md` in de
hoofdmap wordt gerenderd op de repo-pagina), en de interface volgt je systeemthema (licht/donker).
De webinterface is **alleen-lezen** (geen schrijfacties), precies om veilig te kunnen delen.

---

## Veel repository's hosten (`wit-hub`)

Eén `wit`-repository is voor `wit` wat één git-repository is voor git. Een **hub**
is de laag eromheen die *veel* repository's onder één service host — zoals GitHub veel
git-repository's host. Het voegt niets toe aan het repository-formaat: elke gehoste
repo is een gewone `wit`-repository, plus een register, een HTTP-router en een
toegangsbeleid. Het volledige ontwerp staat in [ARCHITECTURE-hub.md](ARCHITECTURE-hub.md).

### Een hub opzetten

```bash
wit-hub --root /srv/wit init                 # lege hub aanmaken
wit-hub --root /srv/wit create alice/library --public   # gehoste repo (owner/name)
wit-hub --root /srv/wit create alice/notes              # privé (de standaard)
wit-hub --root /srv/wit list                 # toon gehoste repo's
wit-hub --root /srv/wit serve --host 0.0.0.0 --port 8080
```

`--root` mag je weglaten als je `$WIT_HUB_ROOT` zet. `serve` valt terug op de `host`
en `port` uit het `hub.toml` van de hub.

### Een gehoste repo gebruiken

Vanaf elke machine werkt een hub-URL als remote — `clone`, `push`, `pull` zoals gewoonlijk:

```bash
wit clone http://hub.example:8080/alice/library lib
cd lib
# … bewerken, add, commit …
wit push                       # onthoudt de hub-URL na de eerste push/clone
```

### Een repo op afstand aanmaken

Je hoeft niet meer op de server in te loggen om een repo te maken. Met een token
voor jouw owner-namespace (zie hieronder) maakt een push naar een URL die nog niet
bestaat de repo **automatisch aan** — standaard privé, net zoals een dumb remote
bij de eerste push zijn opslag aanmaakt:

```bash
export WIT_TOKEN=<jouw-token>
wit push http://hub.example:8080/alice/nieuw-project   # repo wordt aangemaakt, dan gepusht
```

Wil je de zichtbaarheid vooraf kiezen, maak 'm dan eerst expliciet aan:

```bash
wit-hub create http://hub.example:8080/alice/nieuw-project --public
wit push http://hub.example:8080/alice/nieuw-project
```

Beide volgen dezelfde regel als een push: je hebt een token nodig waarvan de owner
overeenkomt met de owner van de repo. Aanmaken is idempotent — het opnieuw
uitvoeren op een bestaande repo doet niets (de oorspronkelijke zichtbaarheid blijft;
wijzig die later met `wit-hub visibility`).

### Toegang: tokens

Standaard draait een hub in **token**-modus: `public`-repository's kan iedereen lezen
en clonen, maar het lezen van een `private`-repo en **elke push** vereisen een token
waarvan de owner overeenkomt met de owner van de repo.

```bash
wit-hub --root /srv/wit token add alice      # print een vers token voor owner "alice"
```

Clients geven het mee via de omgeving:

```bash
export WIT_TOKEN=<het-token>
wit push http://hub.example:8080/alice/library
```

Zet `auth_mode = "open"` in `hub.toml` om de ingebouwde auth helemaal uit te zetten —
geschikt op een vertrouwd LAN, of als een reverse proxy vóór de hub de authenticatie doet.

### Bladeren en onderhoud

`serve` biedt ook dezelfde alleen-lezen webviewer als `wit serve`, nu per repo:
`http://hub.example:8080/` toont de repository's die een bezoeker mag zien, en
`/<owner>/<name>/` bladert er één. Retentie draait per repo:

```bash
wit-hub --root /srv/wit gc alice/library     # één repo
wit-hub --root /srv/wit gc                    # alle repo's
```

---

## Versies opruimen (retentie)

`wit` is geen volledig versiebeheer, maar onthoudt wel je historie. Wil je alleen de laatste
paar versies bewaren en de rest opruimen?

```bash
wit gc --keep 2               # bewaar de laatste 2 commits, ruim oudere op
wit gc                        # gewone opruiming van ongebruikte objecten
```

Dit is een **lokale** opruiming. Een remote met volledige historie blijft volledig; je kunt
na het opruimen nog gewoon pushen.

> `gc` verwijdert niet meteen: net-geschreven objecten zijn beschermd door een grace-venster
> (standaard ~2 weken). Tijdens experimenteren kun je `--grace 0` gebruiken om dat over te
> slaan.

---

## Controleren of alles klopt

```bash
wit fsck                      # herbereken alle hashes; meldt corruptie
```

Omdat elk object naar zijn eigen BLAKE3-hash is genoemd, is corruptie meteen detecteerbaar.
Bij `pull`/`clone` wordt elk binnengekomen object bovendien geverifieerd voordat het in de
store belandt.

---

## `.witignore`

Net als `.gitignore`. Eén per map mag; regels in een submap gelden alleen voor die submap.

```
*.tmp           # negeer alle .tmp-bestanden (op elk niveau)
build/          # negeer de map build/ en alles erin
/alleen-root    # alleen in de map waar dit .witignore staat
```

Ignore geldt alleen voor nog niet-gevolgde bestanden. Een bestand dat je expliciet noemt
(`wit add bestand.tmp`) wordt altijd toegevoegd, ook als een patroon het zou negeren.

---

## Debug-commando's

Voor wie onder de motorkap wil kijken:

```bash
wit hash-object boek.txt      # toon de BLAKE3-id van een bestand
wit hash-object -w boek.txt   # … en bewaar het als blob
wit cat-object blobs b3:…     # schrijf de ruwe bytes van een object naar stdout
```

---

## Spiekbriefje

| Commando | Doel |
|---|---|
| `wit init` | nieuwe repository |
| `wit add <pad>` | onder beheer nemen |
| `wit rm [--cached] <pad>` | uit beheer halen |
| `wit status` | wat is er veranderd |
| `wit commit -m "…"` | toestand vastleggen |
| `wit log` | historie tonen |
| `wit checkout [commit]` | bestanden terugzetten |
| `wit sparse set/list` | gedeeltelijke checkout |
| `wit clone <remote> <map>` | repository ophalen |
| `wit push [remote]` | wijzigingen versturen |
| `wit pull [remote]` | wijzigingen ophalen |
| `wit serve` | webinterface starten |
| `wit gc [--keep N]` | opruimen / retentie |
| `wit fsck` | integriteit controleren |

### Hub (`wit-hub`)

| Commando | Doel |
|---|---|
| `wit-hub init` | nieuwe hub op `--root` |
| `wit-hub create <owner>/<name> [--public]` | repository hosten (lokaal) |
| `wit-hub create <hub-url>/<owner>/<name> [--public]` | repo op een hub op afstand aanmaken (gebruikt `$WIT_TOKEN`) |
| `wit-hub rm <owner>/<name>` | gehoste repository verwijderen |
| `wit-hub list` | gehoste repository's tonen |
| `wit-hub visibility <owner>/<name> public\|private` | zichtbaarheid wijzigen |
| `wit-hub token add <owner>` | toegangstoken aanmaken |
| `wit-hub token list` | tokens tonen |
| `wit-hub serve [--host --port]` | hub-HTTP-server starten |
| `wit-hub gc [<owner>/<name>]` | retentie (één repo of alle) |

---

## Voor ontwikkelaars

```bash
.venv/bin/python -m pytest -q   # de volledige testsuite
```

De code is gelaagd: een dunne CLI (`wit/cli.py`) bovenop een porcelain-laag
(`wit/porcelain.py`, `wit/sync.py`) bovenop modules per objecttype (objects, trees, commits,
refs, index). Alleen `blake3` is een runtime-afhankelijkheid; de rest is Python-stdlib.
