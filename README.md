# SICARIOS Core Bot v1.3

First live automation layer for the SICARIOS Discord server.

## v1 functions

- Persistent Czech / English preference buttons in `#role-roles`.
- Automatic synchronization between language preferences and technical Recruit/Member access roles.
- `/accept @user` — accept a visitor as `🌱 Recruit`.
- `/promote @user` — promote Recruit to `👤 Member`, switch language access to Member access, add `🚨 Attack Alerts`.
- `/remove @user` — remove alliance status/access/leadership/rank roles while preserving Czech/English preferences.
- Onboarding commands are accepted from **Leader, Deputy or Marshal** (the server owner is also allowed). `/remove` adds extra protection: Marshal cannot remove Leadership, and Deputy cannot remove a Leader.
- Administrative actions are written to `#log-bota-bot-log` when the bot can access it.

Optional alliance attack monitoring is implemented but disabled by default. It reuses this Discord client and a separate read-only Python collector, with no database. See [attack monitor configuration and Northflank deployment](docs/ATTACK_MONITOR.md).

## Important: this is a running bot

Unlike the one-shot Setup Tool, this process must be online for buttons and slash commands to work. The production bot runs on Northflank from `main`; pushes to `main` automatically build and redeploy. Keep proposed changes on a separate branch until reviewed.

## Reuse the existing Discord application

The easiest path is to reuse the existing `SICARIOS SETUP` Discord application/token. That means the bot can edit the static role information message it previously posted instead of creating a duplicate.

You can rename the bot/application later after testing.

## Install

Copy `.env.example` to `.env` and use the same token and server ID you already used for the Setup Tool:

```env
DISCORD_TOKEN=YOUR_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_SICARIOS_SERVER_ID
```

Then:

```powershell
npm install
npm run check
```

## Register slash commands

Run once initially and again only when command definitions change:

```powershell
npm run deploy
```

Expected output:

```text
[DEPLOY] Registered 3 guild commands in SICARIOS:
  /accept
  /promote
  /remove
```

Guild commands normally appear quickly because they are registered directly to SICARIOS.

## Start the bot

Locally, with `.env`:

```powershell
npm run start:local
```

In Northflank, where runtime environment variables are already supplied, use `npm start`.

Expected startup output:

```text
[READY] Logged in as SICARIOS SETUP#....
[READY] Connected to SICARIOS (...)
[READY] Role panel updated in #role-roles.
```

Leave this terminal running while testing.

## Role buttons

The bot replaces/upgrades the existing static `#role-roles` message with two buttons:

- `🇨🇿 Čeština / Czech`
- `🇬🇧 Angličtina / English`

Each button is a toggle.

### Visitor

A visitor can select Czech, English or both. Only preference roles are added; no internal channels unlock.

### Recruit

Preferences are synchronized automatically to:

- Czech -> `🔐 CZ Recruit Access`
- English -> `🔐 EN Recruit Access`

An accepted Recruit/Member cannot remove their final remaining language preference, preventing them from accidentally losing every language section.

### Member

Preferences are synchronized automatically to:

- Czech -> `🔐 CZ Member Access`
- English -> `🔐 EN Member Access`

### Leadership

Leadership sees both language sections directly through `🛡️ Vedení / Leadership`. Buttons only store their preferences; the bot removes redundant technical language-access roles from Leadership.

## `/accept`

Usage:

```text
/accept user:@Player
```

Requirements:

- caller is Leader, Deputy, Marshal, or server owner;
- target is already on the Discord server;
- target has selected at least one Czech/English preference;
- target is not already Member/Leadership.

Result:

- adds `🌱 Nováček / Recruit`;
- synchronizes CZ/EN Recruit Access from preferences;
- does not add Attack Alerts.

## `/promote`

```text
/promote user:@Player
```

Result:

- removes Recruit;
- removes Recruit language access through synchronization;
- adds `👤 Člen / Member`;
- adds `🚨 Útoky / Attack Alerts`;
- adds the corresponding Member language access role(s).

## `/remove`

```text
/remove user:@Player
```

Authorization safety:

- regular Recruit/Member: Leader, Deputy or Marshal may remove access;
- Leadership/rank holder: only Leader, Deputy or server owner;
- Leader: only another Leader or server owner.

Removes:

- Recruit / Member;
- all four technical language-access roles;
- Attack Alerts;
- Leadership;
- Leader / Deputy / Marshal / Diplomat / Treasurer rank roles.

It deliberately keeps:

- `🇨🇿 Čeština / Czech`;
- `🇬🇧 Angličtina / English`.

These are harmless preferences and do not unlock internal channels.

It does **not** remove the technical Administrator role.

## Required bot role hierarchy

The bot's managed Discord role (currently likely named `SICARIOS SETUP`) must remain **above every role the bot needs to add/remove**.

For now, while testing, leave the bot's temporary Administrator permission enabled.

Do **not** leave Administrator enabled permanently. Once v1 is validated, the next hardening step will replace it with minimal permissions and explicit channel access.

## Privileged intents

v1 only starts the client with the `Guilds` gateway intent. Presence Intent, Server Members Intent and Message Content Intent can remain disabled in Developer Portal.

## Recommended functional test

1. Start the bot.
2. Confirm `#role-roles` now has clickable buttons.
3. Use a visitor/test user to toggle Czech and English.
4. Run `/accept` on the test user.
5. Verify Recruit access is synchronized.
6. Toggle languages while Recruit and verify access changes.
7. Run `/promote`.
8. Verify Member + Attack Alerts + Member Access.
9. Run `/remove`.
10. Verify internal access disappears while language preference remains.

## Security note

Never paste your Discord bot token into chat, screenshots, Git, or shared files. If it is ever exposed, reset it immediately in Discord Developer Portal.


## v1.3 fix

Fixes stale Discord member-role cache during `/accept`, `/promote`, and language-button synchronization.
All member refreshes after role mutations now bypass the cache with `force: true`.
Slash command definitions did not change, so `npm run deploy` is not required when upgrading from v1.0.


## v1.3 fix

Fixed sequential role mutation races in `/accept` and `/promote`. The bot now refreshes the GuildMember from Discord between role changes, preventing a stale role snapshot from re-adding `Recruit` during promotion. Member status also takes precedence during language-access synchronization as a defensive fallback.


## v1.3 fix
Synchronizace jazykových access rolí nyní po odebrání starých Recruit/Member Access rolí vždy znovu načte člena z Discord API před přidáním nových rolí. Tím se odstraní race condition, která mohla po `/promote` ponechat starý Recruit Access.


## Attack monitor v0.1

- Disabled unless `ATTACK_MONITOR_ENABLED=true`; alert pings remain off unless `ATTACK_DRY_RUN=false`.
- Python collector reads alliance attack announcements through pinned EmpireCore; it performs no defense or game actions.
- Authenticated HTTP delivers bilingual cards through the existing Discord client.
- Duplicate IDs and pending retries exist only in RAM; process restarts may repeat active alerts.
- Deployment uses this Core and one collector service in the same Northflank project.

Configuration: [docs/ATTACK_MONITOR.md](docs/ATTACK_MONITOR.md). Validation and remaining live checks: [docs/ATTACK_MONITOR_VALIDATION.md](docs/ATTACK_MONITOR_VALIDATION.md). Run `npm run check` and `npm test` for Node checks; Python and cross-language test commands are in the monitor guide.

## Alianční čtecí příkazy v0.2 (opt-in)

`/online [stranka]` čte čerstvý přehled členů ve hře a rozlišuje online, offline a unknown.
`/utoky [stranka]` čte aktuální alianční útoky, nezávisle na RAM deduplikaci alertů.
Experimentální `/obrana hrac` čte SDI pouze pro hlavní hrad jiného současného člena ve Velké říši;
neprovádí žádné herní akce. Chybějící údaje jsou unknown, nikoli naměřené nuly.
Načtení SDI není potvrzený čas měření ve hře. Kapacity oddělují nádvoří bez aliance,
alianční podporu a celkovou kapacitu; nesoulad hradeb/jednotek v pilotu je dosud nevyřešený.
Dočasná collector diagnostika `DEFENSE_DIAGNOSTICS_ENABLED=false` je samostatně vypnutá;
[přesný diagnostický pilot](docs/GAME_COMMANDS.md#diagnostický-pilot-nesouladu-obrany) zachytí
nejvýše pět whitelist záznamů, bez celých paketů a tajemství.

Nové funkce jsou výchozím nastavením vypnuté. Přehledy zapíná `GAME_COMMANDS_ENABLED`,
obranu navíc `DEFENSE_LOOKUP_ENABLED`, vždy na Core i collectoru. Registrace zůstává ruční:
`node src/deploy.mjs` v Core Shellu s již nastaveným prostředím. Restart příkazy neregistruje.

[Architektura, konfigurace a přesný pilot v0.2](docs/GAME_COMMANDS.md) ·
[Skutečné offline výsledky a neověřené body](docs/GAME_COMMANDS_VALIDATION.md).
Dosavadní živé výsledky v0.1 se nepovažují za ověření těchto nových funkcí.
