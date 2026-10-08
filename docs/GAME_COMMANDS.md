# SICARIOS v0.2 — čtení aktuálních dat

Implementace navazuje na `origin/main` `4f82d9efacd9e236bb607fbc69e329c7d41197ef`.
Dvě služby, jeden Discord klient a jedna stávající EmpireClient relace. Vše pouze v RAM;
bez databáze, historie, automatických herních akcí, browseru nebo dalších veřejných portů.
EmpireCore zůstává 0.49.0, commit `702f9d26cc65ea6c3b26a03e8ad801b2684f42f3`.

## Konfigurace

| Proměnná | Služba | Výchozí hodnota / pravidlo |
|---|---|---|
| `GAME_COMMANDS_ENABLED` | Core + collector | `false`; přesné `true` zapne přehledy/snapshoty |
| `DEFENSE_LOOKUP_ENABLED` | Core + collector | `false`; vyžaduje zapnuté přehledy |
| `DEFENSE_DIAGNOSTICS_ENABLED` | Pouze collector | `false`; vyžaduje defense flag; nejvýše 5 nových SDI pokusů za proces, bez resetu při reconnectu |
| `GGE_ALLIANCE_ID` | Core + collector | Na Core nově povinné při zapnutých funkcích; bez implicitního ID; SICARIOS pilot `3540` |
| `GAME_COMMAND_CHANNEL_ID` | Core | Povinné při zapnutých přehledech; ID existujícího interního kanálu |
| `ATTACK_COLLECTOR_URL` | Core | `http://sicarios-attack-collector:8081`; pouze při obraně; origin bez credentials, path/query/fragment |
| `GGE_MEMBERS_POLL_SECONDS` | Collector | `60`, platný rozsah `30–300` s |

Na Core přehledy vyžadují `ATTACK_MONITOR_ENABLED=true`. Obě služby mají stejný existující
`GGE_SERVER_ID` a `ATTACK_SHARED_SECRET`. V pilotu je server `world2`; herní endpoint, zone,
username/password, Discord token, attack/status channels a ping režim zachovej z ověřené v0.1.
Examples nezapisují žádné skutečné tajemství. Nové flags samy nemění dry-run ani ping role.
Chybně zadaná povinná konfigurace selže při startu; provozní chyby lookupů nezastavují monitor.

## Discord

Guild commands registrované přes `guild.commands.set`: původní `/accept`, `/promote`, `/remove`,
plus `/online` a `/utoky` při zapnutých přehledech; `/obrana` jen při samostatném defense flagu.
Runtime flags se kontrolují i po staré registraci. DM, jiné guildy a jiné kanály jsou odmítnuté.
Role se čtou znovu s `force:true`; přístup má owner, Member, Leadership a hodnosti
`ALLIANCE_RANK_KEYS`. Recruit, jazyková preference a Attack Alerts samy nestačí.
Stejná autorizace platí pro autocomplete. Odpovědi jsou ephemeral, CZ/EN a bez mentions.

- `/online [stranka]`: 20 online jmen na stránku, stabilní jméno/ID řazení; počty online,
  známých offline, unknown a celkem. AMI může chybět; `model_fields_set` musí obsahovat
  `login_activity`. Jen explicitní 0 je online, 1–4 offline, ostatní unknown.
- `/utoky [stranka]`: nejvýše 10 položek; známé dopady vzestupně, unknown nakonec, movement ID
  a kingdom jako další klíče. Útočník/aliance, obránce, hrad, souřadnice, svět, dopad,
  exact/estimated/unknown vojáci i nástroje. Texty jsou kvůli Discord limitům zkrácené a escapované.
  Položka po očekávaném dopadu má výslovné označení; není to důkaz výsledku bitvy.
- `/obrana hrac`: autocomplete vrací player ID z čerstvého kompletního rosteru. Ruční jméno
  přijmeme jen při jedné NFC+Unicode casefold shodě. ID bez současného členství nestačí.

Cooldown přehledů je 3 s na uživatele, společný pro oba přehledy. Obrana má 15 s na uživatele.
Mapy cooldownů mají limit 2000 aktivních položek a čistí expirované záznamy při přístupu;
při plné mapě odmítnou další požadavek. Stránkování nevytváří interaktivní sessions.

## Snapshoty: collector → Core

POST `/v2/state` na stávajícím interním 8080 s `Authorization: Bearer ATTACK_SHARED_SECRET`.
Secret se porovnává přes hash a timing-safe comparison. Body limit této cesty je 1 MiB;
`/v1/attacks` a `/v1/heartbeat` zůstávají na 32 KiB.

```json
{
  "schema_version": 2,
  "server_id": "world2",
  "alliance_id": 3540,
  "collector_instance_id": "7d287720-1ffd-45eb-a79c-7f351984188a",
  "sequence": 1,
  "generated_at": 1791470000,
  "members": {
    "observed_at": 1791470000,
    "complete": true,
    "truncated": false,
    "count": 1,
    "items": [{"player_id": 123, "name": "Člen", "online_state": "unknown"}]
  },
  "attacks": {
    "observed_at": 1791470000,
    "complete": true,
    "truncated": false,
    "count": 0,
    "items": []
  }
}
```

`attacks.items` používá původní normalizované v1 attack DTO. Trojitý klíč je stále
`(server_id, kingdom_id, movement_id)`. Snapshot vzniká po úspěšném movement refreshi (standardně
30 s) a po samostatném member refreshi (60 s). Sekce si uchovávají vlastní čas úspěšného čtení;
selhání neomladí stará data a nevytvoří prázdný seznam. TTL obou sekcí je nezávisle 120 s.

Collector posílá snapshoty samostatným workerem: jeden odesílaný a jeden nejnovější čekající
snapshot. Retry nedělá další alerty a nepřepisuje novější čekající stav. V2 odmítnutí není fatal
pro v1 monitor. UUID je nové při startu procesu, sequence roste; po disconnectu se lokální
sekce zahodí. Core odmítá opakované/starší sequence, chybný server/AID, nesmyslné časy,
duplicitní klíče a nesoulad count/items. Celý envelope se validuje před změnou cache.

Sekce mají limit 250 členů / 500 útoků. Collector označí překročení jako `complete:false`,
`truncated:true`; navíc omezuje kombinovaný UTF-8 payload na 1 MiB. Count znamená počet skutečně
poslaných položek. Core neúplný seznam **nezveřejní jako nový přehled**: ponechá poslední kompletní
seznam s původním časem a varováním. Pokud kompletní data nikdy neměl, řekne, že na ně čeká.
Neúplná sekce tedy nic nemaže, ale ani nezpřístupní nové dílčí položky. Defense/autocomplete
při tomto stavu nejsou dostupné. Úspěšná kompletní prázdná sekce přehled vyprázdní.

Při nové collector instanci Core zachová poslední kompletní sekce s původním časem a TTL;
chybějící nebo neúplná sekce je ani při restartu collectoru nemaže. Uchovává 64 vyřazených UUID a časový high-water
mark: nová instance musí mít `generated_at` novější než poslední přijatý envelope. Pozdní
zprávy jsou chráněné i po evikci UUID. Restart collectoru ve stejné sekundě může dočasně dostat
409; další čerstvý snapshot z nového procesu je přijmutelný. Synchronizované systémové časy jsou
nutné, stejně jako pro v1. Restart Core záměrně smaže snapshoty, pořadí i dedup RAM.

Core `/healthz` při zapnuté funkci přidává `game_commands.members/attacks` se statusy
`missing|fresh|stale`, `partial` a časy. Bez jmen/rosteru. `/readyz` zůstává jen původní diagnostikou
attack feedu. Starý collector přes v1 stále funguje; nové příkazy čekají na v0.2 data.

## Obrana: Core → collector

POST `/v2/defense` na stávajícím **privátním** 8081, stejný Bearer secret. Vstup má přesně
`schema_version:2`, UUID `request_id`, `server_id`, `alliance_id`, kladné `player_id`.
Core neposílá souřadnice ani herní příkazy. Body limit je 8 KiB. Odpovědi: 401 secret,
400 vstup, 422 nepovolený cíl, 429 busy/cooldown, 503 disabled/nedostupná relace/karanténa,
504 timeout; úspěch 200 s normalizovaným DTO. Bounded HTTP server má nejvýše 16 handlerů;
při vyčerpání uzavírá nová spojení.

Worker má jednu aktivní operaci a žádnou čekající frontu. Souběžný požadavek včetně stejného
cíle dostane 429; sdílení in-flight výsledku není v tomto prototypu implementované. Cache má
TTL 30 s / nejvýše 100 cílů; čistíme při dalším lookupu a při změně relace. Chyby se necacheují.
Globální interval mezi začátky game lookupů je 5 s. Jednotlivé game requesty mají timeout
nejvýše 5 s a sdílený deadline 11,5 s; HTTP čekání collectoru je maximálně 12 s. Core timeout
15 s nikdy automaticky neopakuje SDI. Core ověřuje request/target identity, čas a meze DTO,
s response body limitem 64 KiB.

Lookup z čerstvého kompletního member seznamu:

1. Odmítne vlastní účet a nečlena. Přes `get_player_info(player_id)` ověří explicitní owner ID
   a aktuální AID, právě jeden `MapItemType.CASTLE` v `Kingdom.GREEN`, owner, ID a souřadnice.
   Outpost, jiný svět, chybějící/nejednoznačný či obsazený hrad jsou odmítnuté.
2. Stejně ověří vlastní hlavní hrad ve Velké říši. Zdroj nejsou první libovolné souřadnice ze seznamu.
3. Na stejné relaci volá veřejné `EmpireClient.request_packet(GetSupportDefenseRequest,
   'sdi', timeout)`; používá stejný frame, `Connection.request`, command lock a waiter check
   jako `get_support_defense_info`. Není to další klient ani globální packet observer.
   Potřebná pole parsuje připnutým `GetSupportDefenseResponse`, gui/gli ani SCID neparsuje.
4. Znovu čte profil cíle, ověří stejný hrad/souřadnice/AID a členství. Změna mezi čteními lookup
   odmítne. SDI nenese jednoznačný target ID: prototype může ověřit stabilitu souřadnic při čtení,
   nemůže atomicky zamknout herní mapu; při jakékoliv pochybnosti v pilotu obranu vypni.

DTO uvádí target, `fetched_at` a kompatibilní `observed_at` se stejným **časem přijetí
odpovědi**, `source_age_seconds` z AS nebo null, quality `complete|partial|unavailable`,
capacities `wall/yard/alliance/courtyard`, pozice `S` s `id/count/kind`
(`troop|tool|unknown`) a omezené `B` id/name. `yard` je UYL, tedy celková kapacita nádvoří
**včetně** alianční části. `alliance` je AUYL. `courtyard = UYL − AUYL` pouze při obou
explicitních nezáporných celých číslech a UYL ≥ AUYL; jinak null, nikdy domyšlená nula.
Core ověřuje odvození. Discord ukazuje všechny tři kapacity zvlášť a varuje při nesouladu.
Zero UYL/AUYL je přípustné pouze explicitně. Číselné stringy, bool a záporné kapacity jsou unknown.
Nový Core přijme i starší v0.2 DTO bez nových polí; jeho observed_at označí pouze jako načtení
a AS jako unknown. Nové partial/complete znamená pokrytí polí, nikoliv shodu s herním dialogem.

Připnutý SDI model AS nedefinuje jako běžné pole, ale `extra='allow'` ho uchovává.
Dokumentace knihovny ho popisuje jako stáří špionážních údajů v sekundách. Přijímáme pouze
explicitní nezáporné celé číslo, včetně 0; bez něj je stáří unknown. Discord uvádí AS
**při načtení**: cache neomladí fetched_at ani AS. TTL 30 s omezuje věk lokální cache,
nezaručuje čerstvost zdrojových herních údajů. Z fetched_at − AS neodvozujeme potvrzený čas
měření. Kapacity/jednotky jsou viditelně označené jako SDI se zatím neověřenou shodou s dialogem.

Modelové defaulty se bez `model_fields_set` nevydávají za měření. Explicitní `S:[]` je jiné než
chybějící S. Knihovna převádí páry přes `js_int` a vynechává nekladné počty; collector nyní
odmítá malformed S místo jeho tichého převodu na prázdnou pozici (stejná konzervativní
karanténa). Explicitní nulové řádky může parser vynechat; kladná celá čísla se nepřepočítávají.
Neznámá ID nejsou přesným součtem vojáků; GUI/Gli, source SCID, tower castellan,
vlastní roster, raw paket ani efekty se neexportují. `AUYL` je serverem uvedená kapacita pro
alianční podporu, nikoliv zaručený volný počet dalších vojáků. Kastelán je bez vybavení;
quality complete neznamená audit hradu, tier score, PVP procenta nebo predikci vítězství.

### Diagnostický pilot nesouladu obrany

Uživatel doložil KrakenQ / Hrad KrakenQ / GREEN hlavní hrad / 574:528: wall 8998 vs dialog
9180 a vojáci 12128 vs 12374. Stav potvrdil jako stejný; opakovaný nový lookup dal stejné
hodnoty. Oprava popisku UYL je potvrzená ze zdrojů knihovny. Příčina rozdílu UWL/S potvrzená
není; žádný koeficient nebo screenshotové číslo není v runtime použité. Dodaný screenshot
je „Špionáž brány (50 sekundy zpátky)“; neznáme AS odpovědi SDI z původního porovnání.
Stáří ani rozdílný kontext dialogů zatím nejsou doloženým vysvětlením.

Diagnostika zapisuje pouze do existujícího collector logu řádky
`SICARIOS_DEFENSE_DIAGNOSTIC {JSON}`. Žádný soubor, historie v RAM ani nový HTTP endpoint.
Maximálně 5 nových SDI pokusů za proces; cache hity nezapisuje, reconnect limit neobnoví.
Jeden JSON má limit 16 KiB, první tři pozice S, nejvýše 20 řádků na pozici s příznakem
truncation. Zachytí pouze validní dvojice ID/počet, ne libovolné texty či hodnoty.

Záznam obsahuje UUID requestu, server/AID, generaci, časy požadavku/přijetí, ověřená numerická
ID cíle a souřadnice TX/TY/SX/SY. `wire` má whitelist S/AS/UWL/UYL/AUYL před parsováním,
`model` stejné vybrané hodnoty po parsování, `normalized` kapacity/AS a ID/count/kind tří
hradeb, `outcome` accepted nebo kód odmítnutí. Chybějící/invalid skaláry jsou označené,
nikoliv nuly. Žádný raw paket, gui/gli, SCID, kastelán/vybavení, jména, credentials nebo
shared secret. Kontext requestu není důkaz target identity odpovědi SDI. Záznam po timeoutu
nebo změně generace nesmí být vydáván za úspěšný výsledek; dál platí karanténa.

1. Ručně sestav/nasaď aktuální commit PR #2 do **obou služeb**. Core má již zapnuté
   `GAME_COMMANDS_ENABLED=true`, `DEFENSE_LOOKUP_ENABLED=true` a existující privátní collector
   URL/kanál/AID; zachovej stávající attack env/secrets. Core žádný nový env nepotřebuje.
   Na collectoru Pause → ověř 0 instancí → build aktuálního commitu → nastav navíc
   `DEFENSE_DIAGNOSTICS_ENABLED=true` → Resume právě jedné instance, bez rolling překryvu.
   Nový proces začíná bez defense cache. Není nutná nová registrace Discord příkazů,
   pokud /obrana již existuje. Nepřihlašuj collector účet v browseru.
2. Ověř `/healthz` → `game_commands.defense_diagnostics_enabled:true`,
   `sdi_quarantined:false`; attack `/readyz`, heartbeat a běžné alerty musí fungovat dál.
   V interním Discord kanálu proveď `/obrana hrac:KrakenQ` (autocomplete ověřeného člena).
   Zkontroluj target 574:528; ulož ephemeral odpověď včetně času načtení a AS.
3. Ve svém běžném herním klientovi zachyť stejný hrad: celý název/souřadnice, druh dialogu
   (podpora vs útok/špionáž), čas porovnání, zobrazené stáří zprávy, všechny tři pozice hradeb,
   ID jednotek, pokud je klient dostupně ukazuje, kapacity a potvrzení nezměněného stavu.
   Pokud lze, zachyť i dialog podpory odpovídající požadavku SDI, vedle původní špionáže brány.
   Screenshotové AS=50 nelze automaticky přiřadit k odpovědi SDI.
4. Pro druhý **nový** lookup počkej alespoň 31 s od prvního času načtení (TTL 30 s,
   user cooldown 15 s). Opakuj /obrana a herní porovnání. V logu musí přibýt nový JSON
   s novým request_id/fetched_at; pokud nepřibyl, neoznačuj odpověď jako nově načtenou.
   Nejvýše pět pokusů; při timeoutu nepokoušej další SDI ani restart jako obcházení karantény.
5. Z collector logu vyexportuj **jen jednotlivé řádky** s prefixem
   `SICARIOS_DEFENSE_DIAGNOSTIC` pro tyto lookupy, nikoliv celé deployment/startup logy.
   Pokud používáš Shell a máš pouze exportovaný log, filtr je
   `rg 'SICARIOS_DEFENSE_DIAGNOSTIC ' collector-pilot.log`.
   Předej je spolu s oběma Discord odpověďmi, screenshoty, UTC časy, commitem a potvrzením
   stejného stavu. Chybějící řádky vysvětli (limit, cache, odmítnutí před SDI); nesbírej raw paket.
6. Po krátkém pilotu nastav na collectoru `DEFENSE_DIAGNOSTICS_ENABLED=false` přes stejný
   Pause / 0 instancí / Resume postup. Při přetrvávajícím nesouladu používej /obrana pouze
   jako neověřený prototyp nebo vypni `DEFENSE_LOOKUP_ENABLED` v obou službách; při jeho
   vypnutí musí být diagnostic flag false. Přehledy a attack monitor mohou zůstat zapnuté.

Vyhodnocení: rozdíl wire → model doloží parser; model → normalized doloží normalizaci;
normalized → Discord doloží validaci/rendering. Pokud se vybraná S/UWL shodují na všech
stupních, chyba jejich přenosu těmito stupni není příčinou. Zbývá herní význam/zdroj stáří
nebo klientské výpočty, které whitelist bez dalších doložených podkladů nepotvrdí. Rostoucí
AS při opakovaném novém fetched_at je důkaz staršího zdroje podle serveru, ne potvrzení, že
právě to způsobilo rozdíl. Truncated/invalid diagnostika není úplné porovnání pozic.

### Pozdní SDI a relace

Statická kontrola připnutého `GetSupportDefenseRequest` nepotvrdila `accepts_reply` pro SDI.
`SCID` je zdroj, nikoli identita cíle. `Connection.request` drží po timeoutu waiter jen po
omezené další okno; jeho dokumentace výslovně připouští pozdní odpověď v dalším požadavku.
Proto tento prototyp používá přísnější karanténu místo spoléhání na serializaci.

Po SDI timeoutu nebo nejisté send/network/parser chybě se zablokuje další SDI na celé aktuální
relaci. Žádný reconnect se kvůli tomu nevynucuje. Monitor, alert worker, member worker a
heartbeat pokračují. Collector `/healthz` přidává `game_commands.sdi_quarantined`.
Disconnect zvyšuje generation a zahodí defense cache. Úspěšný movement refresh potvrzuje
novou přihlášenou relaci. Stará generace nemůže publikovat výsledek. Pokud starý worker doběhne
až přes reconnect, konzervativně zablokuje i tuto novou generaci: uvolnění pak vyžaduje další
přirozenou novou relaci. Opakovaný refresh stejné relace karanténu nikdy neuvolní.

## Uživatelský pilot na Northflanku — ruční

Codex tímto postupem nenastavuje Northflank, neregistruje live Discord commands a nevstupuje
do hry. Tento úvodní postup je pro první zapnutí v0.2; pokud již pilot běží,
navazující šetření nesouladu je v diagnostickém pilotu výše. Nasazení řídí uživatel.

1. Použij commit draft PR z `codex/alliance-read-commands-v0.2`. Nejprve ručně sestav/nasaď
   nový **Core** s `GAME_COMMANDS_ENABLED=false`, `DEFENSE_LOOKUP_ENABLED=false`.
   Starý collector nech přes v1; ověř `/healthz`, `/readyz` a dosavadní alerty/onboarding.
   Restart Core smaže RAM dedup a může znovu oznámit aktivní útoky.
2. **Pause collector → ověř 0 instancí**. Zvol PR větev a explicitně sestav aktuální commit,
   pokud jeho build není dostupný: Dockerfile `/collector/Dockerfile`, context `/`.
   Po `Successful` nastav `GAME_COMMANDS_ENABLED=true`, `DEFENSE_LOOKUP_ENABLED=false`,
   `GGE_MEMBERS_POLL_SECONDS=60`; ponech `GGE_SERVER_ID=world2`, `GGE_ALLIANCE_ID=3540`
   a již ověřené ostatní env/secrets. Resume/Deploy s jedinou instancí, bez rolling překryvu.
3. Na Core nastav `GAME_COMMANDS_ENABLED=true`, `DEFENSE_LOOKUP_ENABLED=false`,
   `GGE_ALLIANCE_ID=3540`, `GAME_COMMAND_CHANNEL_ID=<ID existujícího interního kanálu>`.
   Zachovej `ATTACK_MONITOR_ENABLED=true`, `GGE_SERVER_ID=world2` a dosavadní secret/channels.
   Po restartu zkontroluj Core `/healthz` → obě sekce `fresh`, `partial:false` a jejich časy;
   `/readyz` má být stále `healthy`. Pokud Core bylo při příjmu snapshotů vypnuté, další nový
   snapshot musí dorazit do jednoho member/movement intervalu.
4. V **Core Shellu** spusť `node src/deploy.mjs` (bez lokální `.env`). Ověř přesně 5 guild
   příkazů: accept/promote/remove/online/utoky. `npm run deploy` vyžaduje lokální `.env`;
   samotný restart služby registraci neprovede.
5. `/online`: porovnej alespoň dva známé členy s hrou, unknown a čas dat. Login/logout jiného
   účtu než MiskoJeTu se má projevit do dvou member intervalů (120 s). Collector účet současně
   nepřihlašuj v browseru. Zkontroluj CZ/EN, stranka a nepovolený kanál/Recruit.
6. `/utoky`: legitimně prázdný přehled; pak vhodný ručně provedený kontrolovaný útok nebo
   skutečná hrozba. Porovnej obránce, hrad/souřadnice, svět, dopad a exact/estimated/unknown.
   Dvě různá movement ID se nesloučí; opakovaný příkaz nevytvoří další attack kartu/ping.
   Označení „po dopadu“ neznamená výhru. Žádné herní akce nespouští bot.
7. Krátce Pause collector, **Core nech běžet**. Po více než 120 s mají přehledy uvést stale,
   nikoliv „nikdo / bez útoků“. Resume obnoví čerstvost; není nutné opakovat celý pilot v0.1.
8. Až přehledy projdou: Pause collector → 0 instancí → nastav v **obou službách**
   `DEFENSE_LOOKUP_ENABLED=true` (GAME flag zůstává true). Core potřebuje privátní
   `ATTACK_COLLECTOR_URL=http://sicarios-attack-collector:8081`; pokud skutečný privátní hostname
   služby v projektu není tento, uprav pouze hostname na ověřený. Žádný public port.
   Sestav/nasaď aktuální commit, Resume jediného collectoru; Core restart. V Core Shellu znovu
   `node src/deploy.mjs`, ověř 6 příkazů.
9. `/obrana hrac`: autocomplete a jeden známý hlavní hrad **jiného současného člena SICARIOS**.
   Porovnej S jednotky po pozicích, kapacity, kastelána a čas s herním dialogem/screenshotem.
   Po uživatelském cooldownu 15 s ověř druhý cíl. Neznámá ID/pole nesmějí vypadat jako nuly.
   Vlastní účet, ruční nejednoznačné jméno, nečlen a nepovolený Discord uživatel se odmítnou.
10. Pokud lookup timeoutuje, ověř `sdi_quarantined:true` a že další lookup zůstává nedostupný,
    zatímco `/readyz`, heartbeat a alerty dál fungují. Nevyvolávej restart/reconnect jako
    rutinní řešení karantény. Při nejasném přiřazení, jednotkách nebo kapacitách vypni pouze
    defense v obou službách; přehledy mohou zůstat zapnuté.
11. Zapiš výsledky pilotu do `GAME_COMMANDS_VALIDATION.md`, včetně screenshotového porovnání,
    ověřeného commitu a zbylých rozdílů. PR zůstává draft/neověřený prototyp do těchto výsledků.
    Merge pouze na tvůj další explicitní pokyn. Při pozdějším nasazení na main znovu zachovej
    Pause / 0 instancí / explicitní úspěšný build / Resume collectoru.

Rollback: nejprve vypni `DEFENSE_LOOKUP_ENABLED` v obou službách, případně také GAME flag.
Core stále kontroluje runtime flags. Spusť ručně `node src/deploy.mjs`, aby registrace měla 5
nebo 3 příkazy. Při rollbacku kódu vyber odpovídající již sestavený commit, nejen název větve;
collector opět Pause / 0 instancí / build / Resume. Samostatný restart collectoru nesmaže
Core dedup; restart Core ano. Pro liveness probes nadále používej `/healthz`, ne `/readyz`.
