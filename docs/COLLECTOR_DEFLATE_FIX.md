# Collector: oprava WebSocket HTTP 400 — 21. 10. 2026

Východisko: nasazený `176a3993fd6c18b2b8ec79b4f2c7ff70d3cd7f59`,
větev PR #2 `codex/alliance-read-commands-v0.2`. Collector podle uživatele zůstává alive,
ale opakovaný WebSocket handshake HTTP 400 drží session disconnected; Core správně vrací
503. `GGE_CLIENT_VERSION=1.170.22`. Živý incident nebyl reprodukovaný proti hernímu serveru
Codexem; následující výsledky jsou výhradně offline.

Oprava je na samostatné větvi `codex/collector-websocket-deflate-fix`, draft PR míří na
`codex/alliance-read-commands-v0.2`. Push této opravy neposouvá PR #2 ani větev stávajícího
nasazení. Main, Core runtime, Northflank a živé credentials se při práci nepoužily/neměnily.
Nesoulad čísel /obrana zůstává samostatný, nevyřešený problém.

## Prověřená integrace

Upstream [f96ab419e79416be1f18f81697bdb22120aea58d](https://github.com/eschnitzler/EmpireCore/commit/f96ab419e79416be1f18f81697bdb22120aea58d)
z 20. 10. doplňuje vyjednání permessage-deflate a inflater pro websocket-client.
Samotná hlavička by po úspěšném handshake nestačila: websocket-client nemá podporu
extensions a normálně odmítá RSV1 komprimovaných rámců.

Přímý upgrade celého SDK by vzal 150 commitů, 443 změněných souborů a breaking změny API
mezi 0.49.0 a 0.58.0. Proto zůstává přesný původní pin
`702f9d26cc65ea6c3b26a03e8ad801b2684f42f3`, stejně jako websocket-client **1.9.2**
(splňuje upstream `<1.10`). Přenášíme pouze nezměněný `network/deflate.py` a dvě úpravy
napojení v Connection z konkrétního upstream commitu. [Provenance, hashe a pravidlo odstranění](../collector/patches/README.md).

Docker build po pip instalaci ověří verze a celý SHA-256 původního Connection souboru,
zkopíruje ověřený inflater a upraví import/connect. Na jiném zdroji build selže.
Nejde o runtime monkeypatch; nepřepisuje se celá Connection, request waitery, session,
keepalive ani herní API. SDK metadata dál uvádějí 0.49.0: jde o **0.49.0 s explicitním
transportním backportem**, nikoliv čistou distribuci 0.58.0. `--check` ověří instalaci bez zápisu.

Při každém novém socketu se před přijímacími vlákny:

- nabídne `permessage-deflate; client_max_window_bits`;
- při přijetí extension zavede inflater s raw DEFLATE, RSV1 pouze na prvním rámci zprávy;
- drží kontext mezi zprávami, pokud server nevyžádá `server_no_context_takeover`;
- omezí rozbalenou komprimovanou zprávu na **64 MiB**, včetně fragmentů. Nejde o celkový
  RAM limit ani limit nekomprimovaných rámců;
- nechají odchozí rámce nekomprimované, což extension dovoluje. Pokud server extension
  nevyjedná, běžné nekomprimované zprávy dál fungují a RSV1 se odmítá.

### GGE_CLIENT_VERSION

**Kvůli této opravě není potřeba měnit `GGE_CLIENT_VERSION`; ponech `1.170.22`.**
Herní verze se používá při přihlášení/kontrole verze až po WebSocket upgrade. Neřeší
vyjednání komprese na HTTP handshake. Případné nové odmítnutí herní verze po úspěšném
handshake je jiný výsledek pilotu: zapiš fázi a kód chyby a ověř skutečnou aktuální verzi
klienta, nehádej ji. Aktuální přijetí `1.170.22` herním serverem nebylo offline ověřeno.
Žádný nový env flag není pro transport potřeba.

## Skutečné offline výsledky

| Kontrola | Výsledek |
|---|---|
| Python unittest discovery, `-W error::ResourceWarning` | **52 testů**, 0 fail/skip |
| `npm test` | **30 testů**, 0 fail/skip |
| Upstream síťové testy nad backportovaným 0.49.0 | **100 prošlo** |
| `npm run check`, Python compileall | Prošlo |
| `pip check` na hostu i při Docker buildu | `No broken requirements found.` |
| `git diff --check` | Prošlo |
| Collector Docker build, root context | Úspěšný, backport hash/version kontrola prošla |
| Collector image, UID 10001, `--network none` | **49 Python testů prošlo**, 0 fail/skip |
| Collector image, `--read-only --network none` | `patches/apply_deflate.py --check` prošlo |

82 repository testů na hostu zahrnuje 11 nových testů transportu/instalace; původní
41 Python a 30 Node regresí používají původní API/modely. Ověřují v1 útoky, retry/dedup,
heartbeat, snapshoty a jejich TTL/ordering, /online, /utoky, Core→collector HTTP, onboarding,
SDI quarantine, timeouty a překryvy generací. Core build není pro tuto změnu potřebný:
Core kód/lockfile se nemění, nasazuje se pouze collector.

Nové transportní testy používají skutečný nainstalovaný websocket-client a Connection,
syntetické DEFLATE rámce a lokální HTTP/WebSocket server:

- HTTP 400 bez compression offer, HTTP 101 a příjem komprimovaného herního paketu s backportem;
- text/binary, více fragmentů, ping/pong mezi fragmenty, plain/compressed střídání,
  context takeover včetně druhé zprávy vyžadující předchozí slovník, no-context-takeover,
  menší server window, přednačtené bytes a poll timeout uprostřed rámce;
- vadný DEFLATE, RSV2/3, RSV1 na control/continuation, neplatné pořadí fragmentů;
- přesná hranice / překročení nafouknuté zprávy i přes fragmenty, reset limitu po zprávě.
  Pro malé účelné payloady se testovací cap zmenšuje na 1024 B; zvlášť se kontroluje
  produkční konstanta 64 MiB;
- chyba příjmu ukončí connection, zruší SDI waiter, collector hlásí disconnected,
  vyčistí snapshot/cache a ponechá karanténu; nová relace má nový inflater a teprve
  potvrzený refresh ji uvolní. Žádný automatický herní zásah;
- opakovatelná instalace, read-only kontrola, odmítnutí změněného SDK/modulu.

100 doplňkových upstream testů tvoří `tests/network/test_deflate.py` z `f96ab419` a
Connection/room-id regrese z původního `702f9d26`. U testovacích fake socketů byly jen
povoleny nové connect keyword options a prázdné getheaders, stejně jako v upstream opravě.
Nebyl přebrán nový runtime ani upravená očekávání waiter/session chování. Tyto testy jsou
doplňkové; opakované image testy nejsou přičítány k 82 repository testům.

Při review se opravila obsluha close handshake v novém lokálním test serveru: původní
stub ho nečetl, takže cleanup starého socketu mohl vyčerpat deadline dalšího testovacího
handshake. Jeden souběžný běh při Docker buildu navíc překročil původní 5s timeout
Node harnessu; finální regrese se spouštějí bez souběžného buildu. Záměrně vadný DEFLATE
v testu generuje očekávaný socket-error log; nejde o spojení s herním serverem.

Host: Node 24.19.0 / Python 3.12.14. Image: Python 3.12.15 / UID 10001 /
EmpireCore 0.49.0 s backportem / websocket-client 1.9.2.
Lokální nepublikovaný image `sicarios-collector:deflate-fix`:
`sha256:fac28240b00db481eb1567b2ab5a985229de2aec524324d02086a8bb4f29c53c`.
Build používá volitelný CA secret mount, TLS ověřování zůstává zapnuté.

### Opakování kontrol

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r collector/requirements.txt
.venv/bin/python collector/patches/apply_deflate.py
.venv/bin/python collector/patches/apply_deflate.py --check
.venv/bin/python -m pip check
npm ci --no-audit --no-fund
npm run check
npm test
.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python -m compileall -q collector scripts tests
git diff --check
docker build -f collector/Dockerfile -t sicarios-collector:deflate-fix .
```

V managed prostředí se případně přidá
`--secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt` a použije lokální Docker
socket; bez session proxy mount není potřeba. Instalace může potřebovat package network,
testy používají pouze syntetické identity a loopback. Čistá pip instalace bez backport
kroku v této větvi nestačí. Připnuté zdroje ani jejich kontrolní hashe nerozvolňuj.

## Ruční nasazení pouze collectoru

1. Z draft PR zvol konkrétní finální commit větve `codex/collector-websocket-deflate-fix`;
   ověř ho proti předanému SHA. **Core nech běžet na stávajícím commitu**, bez změny větve,
   env, registrace příkazů nebo restartu. Jeho RAM dedup zůstane zachovaný.
2. **Pause collector → ověř 0 instancí.** I disconnected proces má retry/relogin a nesmí
   běžet vedle nového. V collector službě ručně zvol novou fix větev/commit; neměň větev
   Core ani PR #2. Dockerfile `/collector/Dockerfile`, build context `/`.
3. Explicitně sestav tento commit a počkej na `Successful`. V build logu ověř
   `EmpireCore 0.49.0 + permessage-deflate backport f96ab419: verified` a pip check.
   Samotné přepnutí větve bez úspěšného buildu není nasazení opravy.
4. Zachovej game endpoint/zone, účet, server/AID, shared secret, intervaly i feature flags.
   `GGE_CLIENT_VERSION=1.170.22` ponech. Pro tuto opravu nezapínej defense diagnostiku
   ani nevytvářej druhý collector. Resume/Deploy **jedné instance**, bez rolling překryvu.
5. V Collector Shellu můžeš bez credentials a bez změn ověřit:

   ```sh
   python patches/apply_deflate.py --check
   python -m pip check
   python -c 'import os,json,urllib.request; port=os.getenv("COLLECTOR_HEALTH_PORT","8081"); print(json.load(urllib.request.urlopen("http://127.0.0.1:"+port+"/healthz",timeout=3)))'
   ```

6. Sleduj nejprve handshake a login: nesmí pokračovat HTTP 400 ani přibýt chyby RSV1/
   dekomprese. Collector `/healthz` má po přihlášení a movement refreshi ukázat
   `session:connected`, `ready:true`; `/readyz` HTTP 200. Core musí po následujícím
   heartbeat/refreshi přejít z 503 disconnected na HTTP 200 healthy. Nezaměňuj alive za ready.
   První snapshoty mohou čekat jeden běžný poll interval; sleduj alespoň 2–3 cykly.
7. Ověř čerstvé snapshoty /online a /utoky při zapnutých flags a zachování běžných alertů.
   Existující movement se při běžícím Core nemá znovu pingnout jen kvůli restartu collectoru;
   dva odlišné movement ID se nesmějí sloučit. Použij existující/samostatně ručně vytvořenou
   kontrolovanou situaci, žádné automatické herní akce. Nesoulad /obrana tímto pilotem neřeš.
8. Při přirozeném výpadku sleduj disconnected/503, potom obnovu po řádném přihlášení.
   Nevyvolávej reconnect přes druhé přihlášení collector účtu v browseru. Pokud byla SDI
   karanténa aktivní, její uvolnění dál vyžaduje potvrzenou novou relaci; opakovaný refresh
   stejné relace ji nesmí obcházet.
9. Zapiš nasazený SHA/image, `--check`, ponechanou client version, časy a výsledky
   handshake/login/refresh/heartbeat/snapshotů/alertů. Při chybě předej pouze očištěnou fázi,
   HTTP/protokolový kód a čas, ne raw pakety, login payloady, celé headers nebo credentials.
   Dokud toto neproběhne, oprava je **offline ověřená, herní login neověřený**.

Rollback při nové regresi: Pause collector → 0 instancí → zvol již sestavený původní
`176a399` → Resume jediné instance. Core nech běžet. **Původní image nemá kompresi a při
trvajícím serverovém požadavku může znovu zůstat disconnected/HTTP 400**; rollback tedy
není příslib obnovení služby. Neskrývej 503 ani neměň readiness na úspěch. Nic nemerguj
a neposouvej main/PR #2 bez dalšího samostatného rozhodnutí.
