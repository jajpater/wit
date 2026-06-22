# Een wit-hub server opzetten (Debian LXC) — stap voor stap

Deze handleiding zet een **wit-hub** op: een server die meerdere `wit`-repository's
host, zodat je vanaf meerdere machines kunt clonen, pushen en pullen. We gaan uit van
een **Debian 12 (bookworm) of nieuwer** in een LXC-container, met de data op
`/staging/wit`.

> `wit-hub` zit in de hub-uitbreiding (branch `feat/hub` / PR #1). Zolang die nog niet
> in `main` is gemerged, gebruik je die branch — dat staat in stap 3.

De volledige werking staat in [ARCHITECTURE-hub.md](ARCHITECTURE-hub.md); dit is de
praktische installatie.

---

## 1. Pakketten installeren

Debian 12+ heeft Python ≥ 3.11 (wit vereist dat). `blake3` — de enige afhankelijkheid —
heeft kant-en-klare wheels, dus je hebt **geen** compiler of Rust nodig.

```bash
apt update
apt install -y python3 python3-venv python3-pip git
```

Controleer de versie (moet 3.11 of hoger zijn):

```bash
python3 --version
```

---

## 2. Datamap voorbereiden

De hub bewaart alles onder één map. Wij gebruiken `/staging/wit`.

```bash
mkdir -p /staging/wit
```

> **Let op als `/staging` een aparte mount/bind-mount is.** Zorg dat die gemount is
> vóórdat de hub-service start, anders zou de hub zijn data in een lege, niet-gemounte
> map kunnen aanmaken. In stap 6 dwingen we dat af met `RequiresMountsFor=/staging`.

---

## 3. Code ophalen en installeren

We klonen de repo naar `/opt/wit` en installeren in een venv (Debian 12 blokkeert
systeembrede `pip`, dus een venv is sowieso de juiste weg).

```bash
cd /opt
git clone https://github.com/jajpater/wit.git
cd wit
git checkout feat/hub          # tot PR #1 in main zit; daarna volstaat main

python3 -m venv .venv
.venv/bin/pip install -e .
```

Verifiëren:

```bash
.venv/bin/wit --help
.venv/bin/wit-hub --help
```

### Commando's op je PATH

Zet symlinks zodat `wit` en `wit-hub` overal werken:

```bash
ln -sf /opt/wit/.venv/bin/wit     /usr/local/bin/wit
ln -sf /opt/wit/.venv/bin/wit-hub /usr/local/bin/wit-hub
hash -r
```

> Krijg je `command not found` terwijl het bestand bestaat? Dan staat `/usr/local/bin`
> niet in je PATH (kale containers hebben dat soms). Voeg toe:
> ```bash
> echo 'export PATH="/usr/local/sbin:/usr/local/bin:$PATH"' >> ~/.bashrc
> source ~/.bashrc
> ```

---

## 4. De hub initialiseren

Typ `--root` niet telkens; zet de omgevingsvariabele:

```bash
export WIT_HUB_ROOT=/staging/wit
echo 'export WIT_HUB_ROOT=/staging/wit' >> ~/.bashrc
```

Initialiseer en maak je eerste repository + token aan. De vorm is `owner/naam`
(zoals `gebruiker/repo` op GitHub):

```bash
wit-hub init
wit-hub create jajpater/documenten          # privé (de standaard)
wit-hub create jajpater/scans --public      # iedereen mag lezen/clonen
wit-hub token add jajpater                  # NOTEER het getoonde token
wit-hub list
```

Resultaat op schijf:

```
/staging/wit/
  hub.toml                       # config (auth_mode, host, port)
  tokens.toml                    # tokens -> owner
  repos/
    jajpater/
      documenten.wit/            # een gewone wit-repository
      scans.wit/
```

> **Toegangsmodel (standaard `token`):** `public`-repo's kan iedereen lezen/clonen;
> een `private`-repo lezen en **elke push** vereisen een token waarvan de owner
> overeenkomt met de repo-owner. Wil je auth volledig uitzetten (vertrouwd LAN, of een
> reverse proxy doet de auth)? Zet `auth_mode = "open"` in `/staging/wit/hub.toml`.

---

## 5. Lokaal testen

Start de server handmatig om te zien of alles werkt:

```bash
wit-hub serve --host 0.0.0.0 --port 8080
```

Open op een andere machine `http://<lxc-ip>:8080/` in de browser — je ziet de lijst met
zichtbare repository's. Stop daarna met Ctrl-C; in stap 6 maken we er een service van.

---

## 6. Als systemd-service draaien

Draai de hub als eigen, onbevoorrechte gebruiker — niet als root.

```bash
adduser --system --group --home /staging/wit wit
chown -R wit:wit /staging/wit
```

Maak `/etc/systemd/system/wit-hub.service`:

```ini
[Unit]
Description=wit hub
After=network.target
RequiresMountsFor=/staging

[Service]
User=wit
Group=wit
Environment=WIT_HUB_ROOT=/staging/wit
ExecStart=/opt/wit/.venv/bin/wit-hub serve --host 0.0.0.0 --port 8080
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Inschakelen en starten:

```bash
systemctl daemon-reload
systemctl enable --now wit-hub
systemctl status wit-hub
```

Logs bekijken:

```bash
journalctl -u wit-hub -f
```

> `RequiresMountsFor=/staging` zorgt dat systemd wacht tot `/staging` echt gemount is.
> De `ExecStart` gebruikt het volle venv-pad, dus de service heeft geen PATH nodig.

---

## 7. Netwerk en poort

Poort 8080 (> 1024) werkt zonder extra rechten, ook in een onbevoorrechte LXC.

- Draait er een firewall (`ufw`/`nftables`)? Open poort 8080 voor je LAN.
- Wil je poort 80/443? Doe dat **niet** in de container zelf, maar via een reverse
  proxy (zie stap 8).

---

## 8. (Aanbevolen) Reverse proxy met TLS

HTTP is platte tekst — je token gaat dan onversleuteld over de lijn. Op een vertrouwd
LAN is dat acceptabel; over internet zet je TLS ervoor. Het eenvoudigst is **Caddy**
(automatisch Let's Encrypt-certificaat).

`/etc/caddy/Caddyfile`:

```
wit.voorbeeld.nl {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
apt install -y caddy
systemctl reload caddy
```

Daarna benader je de hub als `https://wit.voorbeeld.nl/...`. Twee veelgebruikte opties:

- **Auth in wit houden** (`auth_mode = "token"`): de proxy doet alleen TLS, tokens
  blijven wits eigen mechanisme.
- **Auth aan de proxy delegeren** (`auth_mode = "open"` in `hub.toml`): laat Caddy/nginx
  basic-auth of OIDC afhandelen; wit vertrouwt dan iedereen die er doorheen komt.

---

## 9. Vanaf een clientmachine gebruiken

Op je werkstation (waar `wit` geïnstalleerd is):

```bash
# token meegeven via de omgeving (nodig voor private repo's en elke push)
export WIT_TOKEN=<het-token-uit-stap-4>

# clonen
wit clone https://wit.voorbeeld.nl/jajpater/documenten docs
cd docs

# … bestanden toevoegen, committen …
wit add .
wit commit -m "eerste import"

# pushen — de hub-URL wordt onthouden na de eerste push/clone
wit push
```

Een bestaande lokale repo voor het eerst naar de hub sturen:

```bash
cd ~/bestaande-map        # met een .wit (anders eerst: wit init)
wit push https://wit.voorbeeld.nl/jajpater/documenten
```

> Publieke repo's clonen/pullen kan **zonder** token; alleen private lezen en pushen
> vereist `WIT_TOKEN`.

---

## 10. Beheer en onderhoud

**Nieuwe repo of token toevoegen** (op de server):

```bash
wit-hub create jajpater/fotos
wit-hub token add anderegebruiker
wit-hub token list
```

**Zichtbaarheid wijzigen** (bijv. een private repo publiek maken zodat je 'm in de
browser kunt bladeren):

```bash
wit-hub visibility jajpater/scans public      # of: private
```

Dit wordt direct van kracht — geen herstart nodig.

**Retentie / opruimen** (per repo of alles):

```bash
wit-hub gc jajpater/documenten      # één repo
wit-hub gc                          # alle repo's
```

**Back-up:** de waarheid is gewoon de map `/staging/wit/repos/`. Een bestandskopie
(rsync, restic, of een `wit clone` naar elders) volstaat als back-up.

**Updaten naar nieuwere wit-code:**

```bash
cd /opt/wit
git pull
.venv/bin/pip install -e .
systemctl restart wit-hub
```

---

## 11. Problemen oplossen

- **`wit-hub: command not found`** terwijl de binary bestaat → `/usr/local/bin` niet in
  PATH (zie stap 3) of bash-cache: `hash -r`.
- **Service start niet, data verschijnt op de verkeerde plek** → `/staging` was niet
  gemount; controleer `RequiresMountsFor=/staging` en `systemctl status wit-hub`.
- **Push geweigerd met 401** → geen of verkeerd `WIT_TOKEN`; de token-owner moet gelijk
  zijn aan de repo-owner. Met `wit-hub token add <owner>` maak je een passend token.
- **Push geweigerd (non-fast-forward)** → iemand pushte ondertussen; doe eerst
  `wit pull` en push opnieuw.
- **Private repo "bestaat niet" (404) vanaf een client** → dat is opzet bij ontbrekend
  token (het bestaan wordt niet gelekt); zet `WIT_TOKEN` en probeer opnieuw.

---

## Spiekbriefje (server)

| Commando | Doel |
|---|---|
| `wit-hub init` | lege hub aanmaken op `$WIT_HUB_ROOT` |
| `wit-hub create <owner>/<naam> [--public]` | repository hosten |
| `wit-hub list` | gehoste repo's tonen |
| `wit-hub visibility <owner>/<naam> public\|private` | zichtbaarheid wijzigen |
| `wit-hub rm <owner>/<naam>` | repository verwijderen |
| `wit-hub token add <owner>` | toegangstoken aanmaken |
| `wit-hub token list` | tokens tonen |
| `wit-hub serve [--host --port]` | server starten |
| `wit-hub gc [<owner>/<naam>]` | retentie (één repo of alle) |
