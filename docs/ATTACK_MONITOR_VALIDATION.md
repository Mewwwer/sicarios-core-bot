# Ověření v0.1 — stav k 8. 10. 2026

## Živý pilot — provedl a potvrdil uživatel 8. 10. 2026

Zdroj těchto výsledků je uživatelovo hlášení o vlastním živém pilotu. Codex pilot neprováděl ani nezávisle neověřoval jeho runtime konfiguraci či herní a Discord logy.

| Scénář / stav | Výsledek podle uživatele |
|---|---|
| Útoky na různé členy aliance | Zachyceny |
| Souběžné útoky na stejný cíl | Rozlišeny jako samostatné útoky |
| Restart pouze collectoru při běžícím Core | Deduplikace fungovala |
| Skutečné Discord oznámení přes roli | Doručeno |
| Výpadek a obnovení spojení | Hlášení fungovala |
| Collector | Účet `MiskoJeTu`, aliance SICARIOS, AID `3540` |
| Core a cílový attack channel | Core hlásí `healthy`; kanál je nastavený |

Pilot potvrzuje uvedené scénáře v použité konfiguraci. Neprokazuje funkčnost ostatních herních světů, přesnost počtů ani dlouhodobou spotřebu prostředků. Deduplikace při restartu pouze collectoru neznamená zachování paměti po restartu Core: jeho RAM cache se smaže a aktivní útoky mohou být oznámené znovu.

## Offline ověření — provedl Codex

**30 testů prošlo**, žádný selhaný ani přeskočený. Kontroly proběhly 7. 10. 2026; při závěrečném review 8. 10. 2026 byly znovu spuštěny syntax kontroly, všech 30 testů a `pip check`. Původní patch měl 25 testů; nové regresní a lifecycle testy jsou zahrnuté níže. Docker buildy a testy uvnitř obrazů jsou výsledky z 7. 10. 2026.

| Kontrola | Výsledek |
|---|---|
| Node přijímač a Discord adaptér | 13 testů prošlo |
| Node start/ukončení skutečného bot.mjs s mockovaným Discord loginem | 2 testy prošly |
| Python normalizátor, výběr aliančních cílů, fronta a refresh | 14 testů prošlo |
| Python → skutečné HTTP → Node přijímač | 1 integrační test prošel |
| Python dependency instalace podle requirements.txt | Závislosti rozřešeny a nainstalované |
| pip check | No broken requirements found |
| JavaScript syntax / Python kompilace | Prošlo |
| npm ci s aktuálním package-lock.json | Prošlo; beze změny závislostí |
| Zapojení do aktuálního main | Přijímač a shutdown začleněny; cloud start zachován |
| Docker build Core a collectoru | Oba obrazy sestaveny; detaily CA níže |
| Testy uvnitř obrazů bez sítě, pod jejich neprivilegovanými uživateli | Core: 15 testů; collector: 14 unit testů |

Integrační test ověřuje převod skutečného Movement modelu z připnutého EmpireCore na JSON, UTF-8 jména, simulované selhání prvního Discord odeslání, retry, potlačení opakovaného ID, odlišení světů, heartbeat a odmítnutí nesprávného tajemství. Discord sink je testovací; nevytváří externí zprávy.

Další scénáře zahrnují souběžné doručení stejného útoku, zastaralá data, payload nad limit, zákaz nepovolených pingů, stav při výpadku Discordu, přijaté opakování po restartu, oddělení nástrojů od vojáků, neznámá metadata a ukončení sběru při jiné alianci.

Prostředí: Linux, Node.js 24.19.0, Python 3.12.14, EmpireCore 0.49.0 ze zdroje `702f9d26cc65ea6c3b26a03e8ad801b2684f42f3`.

Offline testy používají simulovaná herní data a mockovaný Discord sink/login. Samy neprokazují živé přihlášení ani skutečné Discord doručení; tyto výsledky jsou samostatně uvedené v uživatelem provedeném pilotu výše.

Lokální Docker buildy používaly dočasné kopie Dockerfiles, které pouze přidaly BuildKit secret mount systémové CA a `NODE_EXTRA_CA_CERTS` / `PIP_CERT` při instalaci závislostí. To umožňuje HTTPS přes proxy tohoto cloudového prostředí bez vypnutí TLS ověřování. CA se neukládá do image ani repozitáře; produkční Dockerfiles zůstávají bez této prostředí specifické úpravy. Testy byly připojené read-only a kontejnery běžely s `--network none`. Mezijazykový HTTP integrační test prošel v hostitelském prostředí, kde jsou oba runtimy.

## Opravy nalezené při review patche

- Plná deduplikační cache už nezahazuje nejstarší ID při čtení nebo neúspěšném odeslání. Místo uvolňuje až při úspěšném přidání nového ID; limit platí i při souběžném dokončení odeslání.
- Snapshot nepřepíše novější callback ani nesmaže nový útok, který callback zařadil během zpracování snapshotu. Revize fronty jsou pouze v RAM.
- Limit 200 znaků názvu je shodně počítaný podle Unicode code points v Pythonu i Node. Neznámá aliance útočníka je na kartě výslovně označená.
- Core Dockerfile kopíruje package manifesty uživateli `node`, takže funguje i build z pracovního stromu se soubory s právy 0600.

## Závěrečné review a zbývající limity — 8. 10. 2026

Review implementace `118d42c` proti aktuálnímu `main` `251f9c4` nenašlo blokující problém ve schváleném rozsahu. Zkontrolovány byly normalizace aliančního feedu, ověřený HTTP příjem, Discord role/mentions, RAM deduplikace a retry, heartbeat a lifecycle. Definice slash příkazů, jazykové role, onboarding moduly a dependency lockfile nemají změny. Tato aktualizace mění pouze dokumentaci.

GitHub při review hlásil PR #1 jako bez konfliktů. Northflank build statusy Core i collectoru pro commit `118d42c` byly `success`; to je samostatná kontrola buildů, nikoliv důkaz herních scénářů. GitHub Actions workflow runs ani podaná reviews nebyly evidované.

- **Stále neověřeno:** ostatní herní světy, přesnost počtů vojáků/nástrojů a dlouhodobá spotřeba RAM/CPU. Pilot také nedokládá porovnání ETA s herním klientem, doručení na všechny telefony ani Windows běh.
- Bez databáze, historie, persistentní fronty nebo evidence na volume. Omezená fronta a deduplikace zůstávají pouze v RAM; útok proběhlý celý během výpadku může být zmeškán.
- Jeden collector a jedna herní relace. Při deployi zachovat strategii bez překryvu staré a nové relace; pravidelné restarty se nezavádějí.
- Core `/readyz` zůstává diagnostikou feedu a nesmí blokovat routing. Pro probes použít `/healthz`; výpadek hry nesmí restartovat fungující onboarding.
- Merge do `main` automaticky nasazuje produkci na Northflanku. PR je připravený k následnému merge se zachováním těchto limitů; merge v rámci této aktualizace neproběhl.

## Opakování kontrol

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check
npm test
python3.12 -m venv .venv
.venv/bin/python -m pip install -r collector/requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python -m compileall -q collector scripts tests
git diff --check
```

V tomto workspace bylo navíc nutné npm předat zapisovatelnou cache: `--cache /workspace/.cache/npm`. Síťové příkazy i testy s lokálním HTTP běžely s povoleným síťovým přístupem executor sandboxu.

Základ integrace: `Mewwwer/sicarios-core-bot`, main commit `251f9c46f86fb2dc8bfc81b5bb91aa0686194301`.

Offline testy používají syntetické identity a tajemství. Uživatelem uvedené jméno účtu a AID jsou dokumentované pouze jako kontext živého pilotu; herní heslo, Discord token ani sdílené tajemství se sem nezapisují.

## Navazující v0.2

Výše uvedené výsledky jsou historickým záznamem v0.1. Nové příkazy, aktuální offline počty,
volitelné Docker CA mounty a dosud neověřený obranný prototyp popisuje
[GAME_COMMANDS_VALIDATION.md](GAME_COMMANDS_VALIDATION.md). Živý pilot v0.2 se dosud neprovedl.
