# Ověření SICARIOS v0.2 — 8. 10. 2026

Implementovaný a offline ověřený runtime: `ca7452a47fb198709b2f0e34c334646c3e1fda20`.
Následující dokumentační commit nemění runtime. Výchozí main:
`4f82d9efacd9e236bb607fbc69e329c7d41197ef`. Větev `codex/alliance-read-commands-v0.2`.
Původní pracovní adresář na větvi `work` zůstal čistý; změny mají vlastní worktree.
Nebyl nalezen žádný platný AGENTS.md v repozitáři ani nadřazených adresářích.

Pokračování existujícího draft PR #2 z `066e09737ec30a8f532b9665b26db81de3cf0a16`.
Živý pilot /obrana již proběhl u uživatele a odhalil nesoulad; výsledky níže jsou nové
**offline** kontroly opravy a diagnostiky. Codex do hry ani Discordu nevstoupil.

## Doložený nesoulad a závěr pro obranu

Uživatel potvrzuje nezměněný stav a stejné výsledky při opakovaném novém lookupu cíle
KrakenQ / Hrad KrakenQ / hlavní hrad Velké říše / 574:528.

| Pole | Bot v pilotu | Herní dialog / screenshot |
|---|---|---|
| Kapacita hradeb | 8998 | 9180 |
| Vojáci na hradbách | 12128 | 12374 |
| Vlevo, ID → počet | 489 → 744, 227 → 2894, 238 → 1213 | Screenshot levé pozice není doložen |
| Střed, ID → počet | 489 → 759, 227 → 2953, 238 → 3565 | Viditelné počty 3638, 765, 3021; přesné přiřazení screenshotových ikon k ID není doložené |
| Vpravo | Pouze nástroje | Screenshot pravé pozice není doložen |
| UYL / AUYL | 1029100 / 286100 | Nádvoří bez aliance 743000; aliance 286100 |

**Potvrzená chyba a oprava:** UYL nebylo samotné nádvoří. Připnutý
`GetSupportDefenseResponse.yard_limit` výslovně zahrnuje alianční část; také
`KeepDefense.keep_unit_slot_count` je UYL − AUYL. Discord nově ukazuje nádvoří bez aliance,
kapacitu alianční podpory a celkovou kapacitu. Odvození jen při explicitních konzistentních
hodnotách. Pro uvedené UYL/AUYL vychází 743000; nejde o konstantu zadanou do runtime.

**Nepotvrzená příčina:** rozdíl UWL a S. S se parsuje jako ID/počet v pořadí
left/middle/right/keep/stronghold/support/reserve; kladná celá čísla se v připnutém parseru
ani naší normalizaci/renderingu nenásobí a nepřepočítávají. Původní šest uvedených počtů
dává 12128, nástroje vpravo se do tohoto součtu nepočítají. Knihovní
`get_total_defenders()` sčítá celé S včetně nástrojů; implementace ho nepoužívá. Synthetic
regrese s uvedenými počty prokazuje přenos, **není zachyceným živým paketem SDI** a
nedokazuje shodu zdroje s dialogem. Žádný koeficient ani hardcode 9180/12374 nebyl přidán.

Screenshot říká „Špionáž brány (50 sekundy zpátky)“. AS původní odpovědi SDI nemáme.
AS knihovna dokumentuje jako stáří špionážního zdroje, ale SDI model ho má pouze v extras.
Nově se zachovává validní AS a zobrazuje stáří **při načtení**. fetched_at/observed_at jsou
čas přijetí odpovědi, nikdy potvrzený čas měření. TTL/cache ani opakované načtení
nezaručují obnovu zdrojového stavu. Stáří AS nebo rozdílný kontext podpory/špionáže zatím
nejsou prokázaným vysvětlením rozdílu. Z AS neodvozujeme potvrzený absolutní čas měření.

Omezená diagnostika je default false, collector-only `DEFENSE_DIAGNOSTICS_ENABLED`.
Nejvýše 5 nových SDI pokusů za proces, 16 KiB na řádek, 3 pozice hradeb × 20 řádků,
jen whitelisted AS/UWL/UYL/AUYL/S a ověřený numerický request kontext. Porovnává wire,
model a normalized; žádný celý paket/gui/gli/credentials/shared secret ani nový endpoint.
`accepted` označuje přijetí workerem, ne potvrzení doručení Discordu či identity SDI cíle.
Chyba/pozdní výsledek dál vyvolá karanténu. Příčina se do získání diagnostiky označuje
**nevyřešená**, hodnoty v Discordu **neověřené proti hernímu dialogu**.

## Skutečné offline výsledky

| Kontrola | Výsledek |
|---|---|
| `npm test` | **30 Node testů prošlo**, 0 fail, 0 skipped |
| Python unittest discovery | **41 Python testů prošlo**, 0 fail, žádný přeskočený |
| Celkem odlišných testů na hostu | **71**, včetně původních 30 regresí |
| `npm run check` | Syntax všech stávajících i nových JS modulů prošla |
| Python compileall (`collector scripts tests`) | Prošlo |
| `pip check` | `No broken requirements found.` |
| Závislosti | Instalace ověřené v předchozí iteraci; tento follow-up je nemění, Docker dependency vrstvy cache hit; EmpireCore 0.49.0 zachován |
| `git diff --check` | Prošlo |
| Core Docker build (`deploy/Core.Dockerfile`, context `/`) | Úspěšný |
| Collector Docker build (`collector/Dockerfile`, context `/`) | Úspěšný |
| Core obraz, `--network none`, UID **1000** | **30 testů prošlo** |
| Collector obraz, `--network none`, UID **10001** | **38 testů prošlo** |

Testy v obrazech opakují odpovídající host testy; nejsou přičítány k 71 odlišným testům.
Collector obraz nemá Node, proto v něm běžel výslovně vybraný Python subset bez dvou
Python→Node integračních testů a jednoho Core→collector command harness testu. Všechny tři
prošly na hostu. Testovací soubory byly připojené read-only, bez skutečných credentials;
loopback HTTP funguje i s `--network none`. Skutečný bot.mjs se testuje s mockovaným Discord
loginem a zpracuje SIGTERM při monitoru vypnutém, v1 zapnutém i v0.2 zapnutém. Collector
run/shutdown se ověřuje s falešnou relací a nezávislými workery.

Host: Linux, Node **24.19.0**, Python **3.12.14**. Sestavené tagy `sicarios-core:v0.2`
a `sicarios-collector:v0.2` vycházejí z původních Node 24 / Python 3.12 Dockerfiles.
Lokální finální image IDs (nepublikované do registry):

- Core: `sha256:a7214d29c9765fd4615f1f99ff539a1a0ab74dd1beb8f8e12d8616ffa3e55971`
- Collector: `sha256:d6698980dfe074e7654e72818149534d9b42cd6f97d048710608fcc135a350a6`

Buildy používají volitelný BuildKit `proxy_ca` mount systémové CA při dependency instalaci,
`NODE_EXTRA_CA_CERTS` / `PIP_CERT` a zapnuté TLS ověřování. Session CA se neukládá do image
ani repozitáře. Bez tohoto mountu Dockerfiles používají běžný systémový trust. Původní
neprivilegovaní uživatelé a cloud start `npm start` zůstávají zachované.

## Co testy skutečně ověřují

- V1 alert delivery, retry, trojitý movement klíč, souběh, dedup při běžícím Core a reset RAM
  po jeho restartu, heartbeat/readiness, role ping a no-ping dry run. Snapshot retry a čtení
  přehledů neodesílají další attack karty a nezasahují do dedupu.
- Stará v1-only cesta proti novému Core: UTF-8 normalizace přes skutečný Movement model,
  HTTP auth, retry, dedup i healthy heartbeat. Nové přehledy bez snapshotu čekají na v0.2 data.
- Kompletní prázdné sekce, update stejného ID, dva útoky stejného cíle i stejné ID v jiném světě,
  chybějící sekce, nezávislá čerstvost, neúplný snapshot bez odstranění starých položek,
  překročení počtu i kombinovaného UTF-8 body limitu. Atomic validation/publication a
  bounded coalescing nezpřístupňují malformed či starší pending snapshot.
- Sequence, změna instance, pozdní vyřazená instance a ochrana i po evikci 64 UUID,
  časový regres, chybné server/AID/secret/count a limity 32 KiB v1 / 1 MiB v2.
- Chybějící AMI a login_activity v krátkém reálném modelovém řádku, unknown enum a explicitní
  ONLINE=0. Chyba členského čtení neomladí data ani nesrazí attack health.
- Guild/channel/role/DM/runtime flag a autocomplete autorizace, čerstvé role po odebrání,
  ephemeral a allowedMentions, NFC/casefold/nejednoznačná jména, Unicode/Markdown/mentions,
  stránky a Discord limity včetně dlouhých emoji jmen. Registry má podle flags 3/5/6 příkazů.
- Regrese `/accept` → Recruit, `/promote` → Member + Attack Alerts + jazykový přístup,
  `/remove` při zachování jazykové preference a omezení odebírání vedení. Jazyková tlačítka
  nemají změněnou implementaci; jejich reálné Discord chování patří do uživatelského pilotu.
- Defense: vlastní účet/nečlen, chybná aliance/owner, outpost/jiný svět/chybějící/nejednoznačný
  hrad, změna členství nebo souřadnic po SDI; defaulty vs explicitní prázdné S/kapacita 0,
  troop/tool/unknown klasifikace a vyloučení GUI/Gli/SCID.
- SDI timeout a karanténa, celkový HTTP deadline a pozdní úspěch bez publikace/cache,
  busy/cooldown/TTL/100-target bound, parser error a generace přes reconnect. Starý výsledek
  nemůže být vydán za obranu nového cíle. V případě překryvu přes reconnect se konzervativně
  zablokuje i následná generace. Monitor, callbacky, movement refresh a heartbeat pokračují.
- Python fake collector → skutečné HTTP → Node state/cache a mock Discord sink;
  Node `/obrana` handler → skutečné HTTP → Python fake game SDI → ephemeral mock Discord reply.
  Core po timeoutu neposílá automatický druhý požadavek.
- UYL/AUYL přítomnost/nekonzistence/explicitní nula, stejné vykreslení staršího DTO,
  odmítnutí chybného odvození a fetched_at/AS, zachování uvedených S počtů bez korekce.
- Reálné metody připnutého `EmpireClient.request_packet` i `request` nad falešnou connection:
  stejný session frame, command `sdi`, timeout a waiter `accepts=None`. Žádná druhá relace.
- Diagnostika vypnutá ve výchozím stavu, config závislost na defense flagu, whitelist bez
  tajemství/GUI/Gli/SCID, limity a cache hity, všechny tři stupně hodnot. Logging failure
  nemění výsledek ani karanténu; malformed S/error packet a výsledek přes reconnect se
  nepublikují. Attack refresh/heartbeat/ready pokračují i při enabled diagnostice.
- Ruční offline mezní rendering: sedm maximálních pozic, dlouhá escapovaná jména,
  maximální safe integer a nekonzistentní kapacita → description 1383 a embed součet
  5749 znaků, pod Discord limity 4096/6000 (pole nejvýše 604, pod 1024).

Simulované chyby záměrně produkují warning logy (`TimeoutError`, odmítnuté snapshoty,
parser/publisher failure). Finální Python běh s `-W error::ResourceWarning` prošel;
neuzavřené HTTP error response byly při review opravené bez změny retry klasifikace.

## Statické review a omezení prototypu

Připnuté zdroje byly prohlédnuté: alliance `get_local_members` a AMI model_fields_set,
aktuální announced movements, GDI owner/ID/AID a enumy hradů, SDI service/model,
`EmpireClient.send` a `Connection.request`. SDI request nemá target `accepts_reply`;
SCID je zdroj. Omezené post-timeout waiter okno knihovny nezaručuje korelaci všech pozdních
odpovědí. Proto implementace blokuje další SDI do nové přirozené relace a při překryvu generací
případně ještě déle. Nevyvolává reconnect a netlumí attack monitor.

Závěrečné review opravilo zachování complete sekcí přes změnu collector instance,
transactional bounded UTF-8 publication, nejisté pozdní SDI přes reconnect,
Unicode limity Discordu a uzavírání HTTP error responses. Po posledních runtime úpravách
prošly výše uvedené host testy, kontroly a oba odpovídající Docker obrazy. Nebyl nalezen další
blokující problém v offline ověřitelném rozsahu; nejde o živé ověření herního protokolu.

## Dosud neověřeno — musí ověřit uživatel ve hře/Discordu

- Reálná dostupnost a význam AMI/login_activity pro SICARIOS WORLD 2 a změny login/logout.
- Aktuální get_announced_attacks, cíl/ID/svět/dopad a přesnost velikostí vůči hernímu klientovi;
  průběh odebrání položek po úplném snapshotu a doručení do současného attack kanálu.
- Shoda SDI S/UWL s dialogem: na KrakenQ je doložený nesoulad, na druhém hlavním hradu
  srovnání chybí. Živé AS a diagnostická cesta před/po parseru dosud nezachycené;
  skutečná ID/metadatová klasifikace a kastelán vyžadují porovnání. AUYL není garantované volné místo.
- Skutečná identita/stabilita cíle při SDI. Profily před/po lookupu kontrolují stabilitu,
  neuzamykají mapu atomicky. Při nesouladu vypni defense; nepředstírej ověřený audit.
- Přirozený reconnect/karanténa na reálné relaci, latence 12/15 s a dlouhodobé RAM/CPU.
- Live slash registrace, ephemeral viditelnost, autocomplete, role removal, interní kanál,
  jazyková tlačítka a ruční Northflank build/probes bez překryvu relací.

Obrana zůstává **experimentální, se živě doloženým nesouladem a default false**. Přehledy mají samostatný
flag a lze je provozovat s defense vypnutou. Žádná skutečná game/Discord credentials nebyla
potřebná, Northflank ani live registrace se neměnily, nic nebylo mergováno. Historické výsledky
v0.1 jsou v `ATTACK_MONITOR_VALIDATION.md`; nejsou důkazem v0.2.

## Opakování offline kontrol

```sh
npm ci --cache /tmp/sicarios-npm-cache --no-audit --no-fund
npm run check
npm test
python3.12 -m venv .venv
.venv/bin/python -m pip install -r collector/requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python -m compileall -q collector scripts tests
git diff --check

docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -f deploy/Core.Dockerfile -t sicarios-core:v0.2 .
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -f collector/Dockerfile -t sicarios-collector:v0.2 .
```

V tomto managed prostředí Docker používá explicitně lokální socket a zapisovatelný
`BUILDX_CONFIG` pod `/tmp`; zachovává proxy a registry konfiguraci. Bez session proxy je CA
secret volitelný. Tests používají syntetické identity/tajemství a loopback HTTP; instalace
závislostí a pull základních obrazů mohou potřebovat síť, herní/Discord přístup ne.

## Další ruční pilot — diagnostika příčiny dosud neprovedena

Přesný postup včetně env, postupného nasazení Core proti starému collectoru, Pause → 0
instancí → build → Resume, ruční registrace 5/6 commands a rollbacku je v
[GAME_COMMANDS.md](GAME_COMMANDS.md#uživatelský-pilot-na-northflanku--ruční).
Po pilotu doplň ověřený commit, jednotlivé výsledky a zbývající rozdíly. Merge až na další
explicitní pokyn uživatele.

Pro navazující šetření /obrana použij
[přesný diagnostický pilot](GAME_COMMANDS.md#diagnostický-pilot-nesouladu-obrany): pouze
collector navíc `DEFENSE_DIAGNOSTICS_ENABLED=true`, dvě nová načtení s odstupem ≥31 s,
export jen prefixových JSON řádků a porovnání stejných pozic/dialogu včetně UTC času,
AS a commitu. Po sběru flag false; při přetrvávajícím nesouladu obranu vypni nebo ji
používej pouze jako explicitně neověřený experiment. Live údaje dodá uživatel.
