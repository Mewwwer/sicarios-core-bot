# SICARIOS attack monitor v0.1

První implementace dvou služeb: Python sběrač přes EmpireCore a přijímač v existujícím Node.js Core. Bez databáze, historie útoků a trvalé fronty. Připraveno 7. 10. 2026.

**Stav k 8. 10. 2026:** monitor je začleněný do Core se základem `251f9c46f86fb2dc8bfc81b5bb91aa0686194301` a ověřený offline testy. Uživatel potvrdil úspěšný živý pilot: útoky na různé členy, souběžné útoky na stejný cíl, deduplikaci při restartu pouze collectoru, Discord oznámení přes roli a hlášení výpadku/obnovení. Zdroj výsledků a zbývající limity uvádí [validační dokument](ATTACK_MONITOR_VALIDATION.md).

## Co funguje v této verzi

- Čtení aktuálních přesunů a callback nového útoku.
- Použití `get_announced_attacks()`, které zahrnuje útoky na další členy aliance. `get_incoming_attacks()` pro tento účel nestačí.
- Kontrola, že účet je ve správné alianci; podpora a návraty se neoznamují jako útoky.
- Souřadnice, napadený hráč, útočník, svět, dostupná velikost armády a ETA.
- Oddělení vojáků od nástrojů pouze při dostupných metadatech. Bez nich se ukáže odhad nebo neznámá hodnota; nástroje se nesčítají do údajně přesného počtu vojáků.
- Ověřený interní HTTP přenos, limit velikosti zprávy, kontrola serveru a stáří pozorování.
- Jedna CZ/EN Discord karta a ping pouze vybrané role Attack Alerts. První pilot standardně vypíná ping.
- Paměť už odeslaných ID v Core, omezené opakování v RAM sběrače a expirace starých upozornění.
- Heartbeat odlišený od posledního úspěšného snapshotu. Výpadek herních dat nebo odesílání se oznámí do status kanálu.

Zatím je použit ping celé role; individuální propojení herních a Discord účtů přidáme podle potřeby. Tlačítka koordinace, připomínky 15/5 minut, historie a automatická obrana nejsou součást této verze.

## Zapojení do Core

`src/bot.mjs` už spouští přijímač za vytvořením stávajícího Discord klienta a před přihlášením. Při SIGINT/SIGTERM nejprve zavře HTTP přijímač a potom Discord spojení. Onboarding, jazykové role i definice slash příkazů zůstávají zachované; kvůli monitoru není potřeba `npm run deploy`.

Cloud start zůstává `npm start` → `node src/bot.mjs`; lokální start `npm run start:local` načte `.env`. Monitor je standardně vypnutý. Zapne se až nastavením `ATTACK_MONITOR_ENABLED=true` a doplněním potřebných proměnných. Bez zapnutí běží Core jako dosud, bez nového HTTP portu a bez herního účtu. Modul používá vestavěné Node.js knihovny a existujícího Discord klienta; nepřidává npm závislosti.

`deploy/Core.Dockerfile` je volitelná build konfigurace s kontextem v kořeni repozitáře. Pokud současný Northflank build funguje, není potřeba ho měnit. Druhá služba používá `collector/Dockerfile` se stejným kořenovým build kontextem.

## Konfigurace

Vzory `deploy/core.env.example` a `deploy/collector.env.example` uvádějí potřebné proměnné. Nahrazují se skutečnými hodnotami v Northflank runtime environment/secrets; nejde o build argumenty.

| Služba | Údaje |
|---|---|
| Core | Stávající DISCORD_TOKEN a DISCORD_GUILD_ID; ATTACK_CHANNEL_ID, ATTACK_ROLE_ID, ATTACK_STATUS_CHANNEL_ID |
| Sběrač | GGE_USERNAME, GGE_PASSWORD, GGE_GAME_URL, GGE_GAME_ZONE, GGE_ALLIANCE_ID a privátní ATTACK_CORE_URL |
| Obě | Stejný GGE_SERVER_ID a ATTACK_SHARED_SECRET |

`GGE_SERVER_ID` je identifikátor konkrétního serveru, který zvolíme shodně v obou službách. Nenahrazuje skutečný herní endpoint a zone. Tyto dvě hodnoty i alliance ID je potřeba ověřit, nikoliv odhadnout z jazyka hry. `GGE_CLIENT_VERSION` je volitelná úprava přihlašovací konfigurace; správnou hodnotu vyžaduje případný protokolový pilot.

Sdílené tajemství vygenerovat jednou, například:

```sh
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
```

Stejnou hodnotu vložit do obou služeb. Herní heslo patří jen sběrači a Discord token jen Core. Pro test nastavíme `ATTACK_CHANNEL_ID` na testovací kanál a `ATTACK_DRY_RUN=true`. Tento režim stále vytváří zprávy v určeném kanálu, ale nepinguje členy; není to tichý režim.

## Northflank — dvě služby v jednom projektu

1. Po začlenění větve nasadit Core a doplnit runtime proměnné. Přidat port **8080** jako **Private**. Existujícímu Discord botovi podle permission modelu serveru přidělit připravenou roli **🤖 Attack Bot**, pokud ji ještě nemá. Ověřit ViewChannel, SendMessages a EmbedLinks; pro ping nementionable role také MentionEveryone v alert kanálu.
2. Druhou službu vytvořit ze stejného repozitáře a větve `main`. Build context je kořen repozitáře, Dockerfile cesta **collector/Dockerfile**. Jedna replika; žádný database addon nebo persistent volume.
3. V collectoru nastavit `ATTACK_CORE_URL=http://SKUTECNY_NAZEV_CORE_SLUZBY:8080`. Northflank pro služby ve stejném projektu dokumentuje privátní adresu `[service-name]:[port-number]`. Jméno služby zjistit z aktuální konfigurace. Porty po automatické detekci zkontrolovat a ponechat jako Private.
4. Collector vystavuje diagnostiku na **8081**. Tato služba nepotřebuje veřejnou doménu.
5. Liveness probe: HTTP `/healthz`, Core port 8080, collector port 8081. Doporučený počáteční interval 30 s, timeout 3 s, initial delay 30 s, 3 neúspěchy. Případnou routing readiness probe také nastavit na `/healthz`.
6. **Core `/readyz` nepoužívat pro řízení routování Northflanku.** Je to diagnostika celého monitoringu: čeká na čerstvý heartbeat a herní snapshot. Pokud by blokovala příjem provozu, sběrač by první heartbeat nemohl doručit. Výpadek hry nesmí odpojit interní příjem ani restartovat fungující onboarding.
7. Při aktualizaci collectoru zajistit, že stará herní relace skončí před novou. Jedna replika sama nezaručuje absenci krátkého překryvu při rolling deployi. Pro první pilot collector zastavit, nasadit novou verzi a spustit, nebo použít ověřenou strategii bez překryvu. Škálování na více replik není podporované.
8. Ověřit skutečný volný bezplatný slot a RAM/CPU týmu. Limity ani cena uživatelského účtu zde nejsou ověřené. Pokud se worker nevejde, nerozšiřovat automaticky placený tarif.

## Lokální ověření

Požadavky: Node.js alespoň 20.19 (testováno 24.19) a Python 3.12. Python dependency je připnutá na prohlédnutý commit EmpireCore 0.49.0, nikoliv pohyblivou větev. `collector/requirements.txt` připíná i běhové závislosti z ověřeného prostředí.

```sh
python -m venv .venv
```

V Linux/macOS:

```sh
.venv/bin/python -m pip install -r collector/requirements.txt
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
npm test
```

Ve Windows:

```powershell
.venv\Scripts\python.exe -m pip install -r collector/requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
npm test
```

Integrační test `test_integration.py` používá lokální testovací Node přijímač a skutečný normalizátor EmpireCore. Nečte herní přihlašovací údaje a nevytváří zprávy na Discordu. Node musí být dostupný v PATH. `npm test` zahrnuje také offline start a SIGTERM skutečného Core s mockovaným Discord loginem. Testy byly spuštěny na Linuxu; Windows běh zatím nebyl ověřen.

Pro simulaci události do skutečného, již integrovaného Core nastavit v prostředí jen ATTACK_CORE_URL, ATTACK_SHARED_SECRET a GGE_SERVER_ID a spustit:

```sh
python scripts/simulate.py --movement-id 900001
```

Pokud je Core na Northflanku a port privátní, simulaci spustit z jeho konzole nebo konzole služby ve stejném projektu; případně použít autorizovaný port forwarding. Skript nepotřebuje herní účet. Opakování stejného ID má vrátit `duplicate`; nové ID má vytvořit další kartu. `ATTACK_DRY_RUN=true` a testovací kanál předem ověřit.

Skutečný collector se spouští `python collector/main.py` s runtime proměnnými z konfigurace. Na Northflanku tento příkaz obstará Docker image.

## Chování při výpadku

Každý úspěšný snapshot může znovu předat aktivní útoky; Core běžné duplicity odstraní pomocí klíče server + svět + movement ID. Po restartu Core je seznam prázdný a aktivní útok přijde znovu. Nové ID na stejném hradě je vždy samostatný útok.

Při krátkém selhání HTTP nebo Discordu se přenos opakuje s prodlužovaným intervalem, nejvýše 30 s; respektuje také omezený Retry-After. Výsledky 400/401/403/404 apod. znamenají nesoulad konfigurace a collector skončí s chybou. Nejistý výsledek po timeoutu může způsobit duplicitu. Čerstvé úspěšné snapshoty aktualizují čekající payload; po dopadu nebo více než 120 s bez čerstvého pozorování se stará událost neodesílá jako nové včasné varování.

Pád procesu smaže frontu. Útok, který celý proběhne během výpadku, může být zmeškán. Na telefonu navíc doručení závisí na osobním nastavení Discord notifikací. Obnovení hry řeší běžný `keep_session` knihovny; definitivní chyby přihlášení vyžadují opravu konfigurace či ruční zásah. Sběrač neprovádí automatické vytváření účtů ani herní akce.

Metadata jednotek knihovna může cacheovat na dočasném disku. Jde o veřejná herní metadata, nikoliv evidenci útoků. Žádný soubor s útoky, uživatelskými hesly nebo Discord zprávami se nevytváří.

## Zbývající ověření po živém pilotu

- Ověřit ostatní herní světy; pilot podle uživatele běží s účtem MiskoJeTu v SICARIOS, AID 3540.
- Porovnat počet/odhad a ETA s herním klientem; zkontrolovat i útok aktivní už při startu.
- Ověřit nastavení telefonů; skutečné Discord doručení přes roli už uživatel potvrdil.
- Změřit RAM/CPU, zpoždění a chování při běžném odpojení, restartu a deployi.

## Primární podklady

- EmpireCore zdroj: https://github.com/eschnitzler/EmpireCore/tree/702f9d26cc65ea6c3b26a03e8ad801b2684f42f3
- Jeho dokumentace/implementace `docs/guides/movements.md`, `state/movements.py`, `movements/tracked.py`, `client/client.py`, `gamedata/troops.py` a `config.py` v tomto commitu.
- Northflank private networking: https://northflank.com/docs/v1/application/network/configure-ports
- Northflank health checks: https://northflank.com/docs/v1/application/observe/configure-health-checks
- Aktuální SICARIOS Core v1.3: https://github.com/Mewwwer/sicarios-core-bot/tree/251f9c46f86fb2dc8bfc81b5bb91aa0686194301

## Rozšíření v0.2

Čtecí příkazy mají samostatné snapshoty `/v2/state` a experimentální `/v2/defense`.
V1 alerty a heartbeat zůstávají kompatibilní; nové cache neovlivňují `seen` ani readiness feedu.
Viz [GAME_COMMANDS.md](GAME_COMMANDS.md) pro kontrakt, limity a postupný uživatelský pilot.
