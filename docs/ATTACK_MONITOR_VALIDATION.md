# Ověření v0.1 — 7. 10. 2026

Výsledek aktuálního ověření v Codex Cloud: **30 testů prošlo**, žádný selhaný ani přeskočený. Původní patch měl 25 testů; nové regresní a lifecycle testy jsou zahrnuté níže.

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

Neověřeno: živé přihlášení, chování konkrétního herního serveru, skutečný Discord ping/push, RAM/CPU při reálném provozu, Windows a build/start na samotném Northflanku.

Lokální Docker buildy používaly dočasné kopie Dockerfiles, které pouze přidaly BuildKit secret mount systémové CA a `NODE_EXTRA_CA_CERTS` / `PIP_CERT` při instalaci závislostí. To umožňuje HTTPS přes proxy tohoto cloudového prostředí bez vypnutí TLS ověřování. CA se neukládá do image ani repozitáře; produkční Dockerfiles zůstávají bez této prostředí specifické úpravy. Testy byly připojené read-only a kontejnery běžely s `--network none`. Mezijazykový HTTP integrační test prošel v hostitelském prostředí, kde jsou oba runtimy.

## Opravy nalezené při review patche

- Plná deduplikační cache už nezahazuje nejstarší ID při čtení nebo neúspěšném odeslání. Místo uvolňuje až při úspěšném přidání nového ID; limit platí i při souběžném dokončení odeslání.
- Snapshot nepřepíše novější callback ani nesmaže nový útok, který callback zařadil během zpracování snapshotu. Revize fronty jsou pouze v RAM.
- Limit 200 znaků názvu je shodně počítaný podle Unicode code points v Pythonu i Node. Neznámá aliance útočníka je na kartě výslovně označená.
- Core Dockerfile kopíruje package manifesty uživateli `node`, takže funguje i build z pracovního stromu se soubory s právy 0600.

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

V pracovním prostoru nejsou skutečná herní ani Discord přihlašovací data. Všechny identity a tajemství použité testy jsou syntetické.
