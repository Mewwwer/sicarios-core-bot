import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { GameState, readGameConfig } from '../src/game-state.mjs';
import { createAttackServer } from '../src/attack-monitor.mjs';
import { commandDefinitions } from '../src/commands.mjs';
import { Cooldowns, renderOverview, resolvePlayer, createGameHandler, validateDefense, renderDefense } from '../src/game-commands.mjs';

const config = { enabled: true, defense: true, serverId: 'test-cz1', allianceId: 444, guildId: '100000000000000001', channelId: '100000000000000002', secret: 'offline-secret-at-least-32-characters', collectorUrl: 'http://127.0.0.1' };
const section = (items = [], observed_at = 1000, extra = {}) => ({ items, observed_at, count: items.length, complete: true, truncated: false, ...extra });
const member = (id = 1, name = 'Člen', online_state = 'online') => ({ player_id: id, name, online_state });
const attack = (movement_id = 1, kingdom_id = 0, arrival_at = 1300, observed_at = 1000) => ({ schema_version: 1, event_type: 'incoming_attack', server_id: config.serverId, movement_id, kingdom_id, observed_at, arrival_at, attacker_id: 1, defender_id: 2, target_id: 3, target_x: 5, target_y: 6, attacker_name: 'Attack', attacker_alliance: 'Other', defender_name: 'Defender', target_name: 'Castle', troops: { value: null, accuracy: 'unknown' }, tools: { value: 0, accuracy: 'exact' } });
const envelope = (extra = {}) => ({ schema_version: 2, server_id: config.serverId, alliance_id: 444, collector_instance_id: randomUUID(), sequence: 1, generated_at: 1000, members: section([member()]), ...extra });

test('state replaces complete lists, preserves omissions and withholds partial lists without renewing observations', () => {
  let now = 1000; const state = new GameState(config, () => now); const e = envelope({ attacks: section([attack(1), attack(2)]) });
  state.accept(e); assert.equal(state.get('attacks').value.count, 2);
  state.accept({ ...e, sequence: 2, members: section([member(1, 'Updated')]) });
  assert.equal(state.get('members').value.items[0].name, 'Updated');
  state.accept({ ...e, sequence: 3, members: section([], 1010, { complete: false, truncated: true }), generated_at: 1010 });
  assert.equal(state.get('members').value.observed_at, 1000); assert.equal(state.get('members').partial, true);
  now = 1121; assert.equal(state.get('members').status, 'stale');
  state.accept({ ...e, sequence: 4, members: undefined, attacks: section([], 1121), generated_at: 1121 });
  assert.equal(state.get('attacks').value.count, 0); assert.equal(state.get('attacks').status, 'fresh');
  assert.equal(state.get('members').status, 'stale');
  assert.match(renderOverview(state, 'members').content, /Stale/);
  assert.match(JSON.stringify(renderOverview(state, 'attacks')), /No current attacks/);
  now = 1122;
  state.accept(envelope({ generated_at: now, members: undefined, attacks: section([attack(9, 0, 1300, now)], now, { complete: false, truncated: true }) }));
  assert.equal(state.get('members').value.observed_at, 1000);
  assert.equal(state.get('attacks').value.count, 0);
  assert.equal(state.get('attacks').value.observed_at, 1121);
  assert.equal(state.get('attacks').partial, true);
});

test('sequence and retired instance guards are atomic, bounded, and retain a time high water mark', () => {
  let now = 1000; const s = new GameState(config, () => now); const e = envelope(); s.accept(e);
  assert.throws(() => s.accept(e)); assert.throws(() => s.accept({ ...e, sequence: 0 }));
  assert.throws(() => s.accept({ ...e, sequence: 2, alliance_id: 99 }));
  assert.throws(() => s.accept({ ...e, sequence: 2, server_id: 'other' }));
  assert.equal(s.sequence, 1);
  for (let i = 0; i < 70; i++) { now++; s.accept(envelope({ generated_at: now, members: section([], now) })); }
  assert.equal(s.retired.size, 64);
  assert.throws(() => s.accept({ ...e, sequence: 99 }));
  assert.equal(s.get('members').value.count, 0);
  assert.throws(() => s.accept({ ...envelope(), generated_at: now + 40 }));
});

test('malformed sections, duplicate keys, oversized counts and timestamp regressions are rejected', () => {
  const s = new GameState(config, () => 1000); const e = envelope(); s.accept(e);
  for (const invalid of [section([member(), member()]), section([], 1000, { count: 1 }), section([], 1000, { complete: true, truncated: true }), section(Array.from({ length: 251 }, (_, i) => member(i + 1))), section([member(1, 'bad\nname')]), section([member(1, 'a', 'invalid')]), section([], 1100)]) assert.throws(() => s.accept({ ...e, sequence: 2, members: invalid }));
  s.accept({ ...e, sequence: 2, members: section([], 1001), generated_at: 1001 });
  assert.throws(() => s.accept({ ...e, sequence: 3, generated_at: 1002 }));
  assert.equal(s.sequence, 2);
});

test('overview distinguishes missing, unknown, partial, elapsed attacks, escaped names and pagination', () => {
  const s = new GameState(config, () => 1000); assert.match(renderOverview(s, 'attacks').content, /Waiting/);
  const items = Array.from({ length: 41 }, (_, i) => member(i + 1, i === 0 ? '@everyone **Člen**' : '😀'.repeat(200)));
  s.accept(envelope({ members: section([...items, member(50, 'Offline', 'offline'), member(51, 'Unknown', 'unknown')]), attacks: section([attack(2, 0, null), attack(1, 0, 999), attack(1, 2, 1100)]) }));
  const rendered = renderOverview(s, 'members');
  assert.ok(!JSON.stringify(rendered).includes('@everyone'));
  assert.ok(rendered.embeds[0].description.length < 4096);
  assert.match(rendered.embeds[0].description, /Unknown 1/);
  assert.match(renderOverview(s, 'members', 4).content, /Invalid page/);
  assert.ok(renderOverview(s, 'members', 2).embeds[0].description.length <= 4096);
  s.partial.members = true;
  assert.ok(renderOverview(s, 'members', 2).embeds[0].description.length <= 4096);
  s.partial.members = false;
  const attacks = renderOverview(s, 'attacks').embeds[0].fields;
  assert.match(attacks[0].value, /Past expected arrival/); assert.match(attacks[2].value, /Arrival unknown/);
  assert.equal(attacks.length, 3);
  const worst = Array.from({ length: 10 }, (_, i) => ({ ...attack(i, 0, 1300, 1001), attacker_name: '😀'.repeat(200), attacker_alliance: '\\'.repeat(200), defender_name: '*'.repeat(200), target_name: '😀'.repeat(200) }));
  s.accept(envelope({ generated_at: 1001, attacks: section(worst, 1001) }));
  const embed = renderOverview(s, 'attacks').embeds[0];
  const length = embed.title.length + embed.description.length + embed.fields.reduce((n, f) => n + f.name.length + f.value.length, 0);
  assert.ok(length < 6000); assert.ok(embed.fields.every((f) => f.value.length <= 1024));
});

test('Unicode casefold names require an unambiguous NFC match; partial and stale cannot select a target', () => {
  const s = new GameState(config, () => 1000);
  s.accept(envelope({ members: section([member(1, 'Straße'), member(2, 'Člen')]) }));
  assert.equal(resolvePlayer(s.get('members'), 'STRASSE').player_id, 1);
  assert.equal(resolvePlayer(s.get('members'), 'C\u030clen').player_id, 2);
  s.accept(envelope({ generated_at: 1001, members: section([member(1, 'Straße'), member(2, 'STRASSE')], 1001) }));
  assert.equal(resolvePlayer(s.get('members'), 'strasse'), null);
  assert.equal(resolvePlayer({ ...s.get('members'), partial: true }, '1'), null);
  assert.equal(resolvePlayer({ ...s.get('members'), status: 'stale' }, '1'), null);
});

test('registry retains onboarding and gates new commands separately; config fails closed', () => {
  const env = { GAME_COMMANDS_ENABLED: 'true', ATTACK_MONITOR_ENABLED: 'true', GGE_SERVER_ID: config.serverId, GGE_ALLIANCE_ID: '444', DISCORD_GUILD_ID: config.guildId, GAME_COMMAND_CHANNEL_ID: config.channelId, ATTACK_SHARED_SECRET: config.secret };
  assert.deepEqual(commandDefinitions({}).map((c) => c.name), ['accept', 'promote', 'remove']);
  assert.equal(commandDefinitions(env).length, 5);
  assert.equal(commandDefinitions({ ...env, DEFENSE_LOOKUP_ENABLED: 'true' }).length, 6);
  assert.throws(() => readGameConfig({ DEFENSE_LOOKUP_ENABLED: 'true' }));
  assert.throws(() => readGameConfig({ ...env, GGE_ALLIANCE_ID: '' }));
  assert.throws(() => readGameConfig({ ...env, DEFENSE_LOOKUP_ENABLED: 'true', ATTACK_COLLECTOR_URL: 'http://user:pass@host/' }));
});

const roles = Object.fromEntries(['member', 'leadership', 'leader', 'deputy', 'marshal', 'diplomat', 'treasurer'].map((k) => [k, { id: k }]));
function interaction({ auto = false, rank = 'member', command = 'online', guild = config.guildId, channel = config.channelId, dm = false, id = 'actor', owner = 'owner' } = {}) {
  const calls = []; return { calls, commandName: command, guildId: guild, channelId: channel, user: { id },
    inGuild: () => !dm, isAutocomplete: () => auto, guild: { ownerId: owner, members: { fetch: async (opts) => { assert.equal(opts.force, true); return { id, roles: { cache: new Set([rank]) } }; } } },
    options: { getInteger: () => 1, getFocused: () => '', getString: () => '1' },
    deferReply: async (v) => calls.push(['defer', v]), editReply: async (v) => calls.push(['reply', v]), respond: async (v) => calls.push(['choices', v]) };
}

test('Discord uses fresh roles and applies guild/channel/role/flag checks to commands and autocomplete', async () => {
  const s = new GameState(config, () => 1000); s.accept(envelope());
  const handler = createGameHandler(config, s, { rolesResolver: async () => roles, clock: () => 1000 });
  for (const options of [{ rank: 'recruit' }, { rank: 'english' }, { rank: 'attackAlerts' }, { guild: 'other' }, { channel: 'other' }, { dm: true }]) {
    const i = interaction(options); await handler(i); assert.match(i.calls.at(-1)[1].content, /unauthorized/);
    const a = interaction({ ...options, auto: true, command: 'obrana' }); await handler(a); assert.deepEqual(a.calls.at(-1), ['choices', []]);
  }
  for (const rank of Object.keys(roles)) { const i = interaction({ rank, id: rank }); await handler(i); assert.equal(i.calls[0][1].flags, 64); assert.deepEqual(i.calls[1][1].allowedMentions.parse, []); assert.ok(i.calls[1][1].embeds); }
  const owner = interaction({ rank: 'recruit', id: 'owner' }); await handler(owner); assert.ok(owner.calls[1][1].embeds);
  const a = interaction({ auto: true, command: 'obrana' }); await handler(a); assert.equal(a.calls[0][1][0].value, '1');
  const longNames = new GameState(config, () => 1000); longNames.accept(envelope({ members: section([member(1, '😀'.repeat(200))]) }));
  const long = interaction({ auto: true, command: 'obrana' });
  await createGameHandler(config, longNames, { rolesResolver: async () => roles, clock: () => 1000 })(long);
  assert.ok(long.calls[0][1][0].name.length <= 100);
  const disabled = createGameHandler({ ...config, defense: false }, s, { rolesResolver: async () => roles });
  const i = interaction({ command: 'obrana' }); await disabled(i); assert.match(i.calls[1][1].content, /disabled/);
  // Simulate revocation: a subsequent fetch returns Recruit, even with an
  // allowed role stored in the interaction's original member object.
  const revoked = interaction({ rank: 'recruit' }); revoked.member = { roles: { cache: new Set(['member']) } }; await handler(revoked); assert.match(revoked.calls[1][1].content, /unauthorized/);
});

test('cooldowns are bounded, expire and never evict an active cooldown to permit spam', () => {
  let now = 1000; const c = new Cooldowns(() => now, 2);
  assert.equal(c.take('a', 3), true); assert.equal(c.take('a', 3), false); assert.equal(c.take('b', 3), true); assert.equal(c.take('c', 3), false);
  now += 4; assert.equal(c.take('c', 3), true); assert.equal(c.items.size, 1);
});

test('authenticated v2 HTTP keeps v1 dedup, health and body limit behavior with no snapshot pings', async () => {
  const now = Math.floor(Date.now() / 1000); const s = new GameState(config); const messages = [];
  const server = await createAttackServer({ ...config, port: 0, host: '127.0.0.1', staleSeconds: 120, startupGraceSeconds: 180, maxConcurrent: 20, maxSeen: 1000 }, { isReady: () => true, sendAttack: async (a) => messages.push(a), sendStatus: async () => {} }, { gameState: s });
  const origin = `http://127.0.0.1:${server.address.port}`;
  const post = (path, data, secret = config.secret) => fetch(origin + path, { method: 'POST', headers: { Authorization: `Bearer ${secret}`, 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  try {
    const e = envelope({ generated_at: now, members: section([], now), attacks: section([attack(1, 0, now + 200, now)], now) });
    assert.equal((await post('/v2/state', e, 'bad')).status, 401);
    assert.equal((await post('/v2/state', e)).status, 200); assert.equal(messages.length, 0);
    assert.equal((await post('/v2/state', e)).status, 409);
    assert.equal((await post('/v1/attacks', e.attacks.items[0])).status, 200);
    assert.equal((await (await post('/v1/attacks', e.attacks.items[0])).json()).result, 'duplicate');
    renderOverview(s, 'attacks'); assert.equal(messages.length, 1);
    assert.equal((await post('/v1/heartbeat', { schema_version: 1, server_id: config.serverId, observed_at: now, last_snapshot_at: now, session: 'connected' })).status, 200);
    assert.equal((await fetch(origin + '/readyz')).status, 200);
    assert.equal((await post('/v1/heartbeat', { padding: 'x'.repeat(32_768) })).status, 413);
    assert.equal((await post('/v2/state', { ...e, sequence: 2, padding: 'x'.repeat(40_000) })).status, 200);
    assert.equal((await post('/v2/state', { ...e, sequence: 3, padding: 'x'.repeat(1_048_576) })).status, 413);
  } finally { await server.close(); }
});

test('defense DTO validates request identity, unknowns and bounds; HTTP failure is not retried', async () => {
  const request = { schema_version: 2, request_id: randomUUID(), server_id: config.serverId, alliance_id: 444, player_id: 1 };
  const dto = { ...request, target: { player_id: 1, name: '@everyone', castle_name: 'Castle', castle_id: 2, x: 4, y: 5, kingdom_id: 0 }, observed_at: 1000, quality: 'partial', capacities: { wall: null, yard: 0, alliance: null }, positions: null, castellan: null };
  assert.equal(validateDefense(dto, request, 1000), dto);
  assert.match(renderDefense(dto).embeds[0].description, /Unknown/);
  assert.ok(!JSON.stringify(renderDefense(dto)).includes('@everyone'));
  for (const change of [{ request_id: randomUUID() }, { target: { ...dto.target, player_id: 2 } }, { observed_at: 950 }, { capacities: {} }, { positions: Array(8).fill([]) }, { quality: 'complete' }]) assert.throws(() => validateDefense({ ...dto, ...change }, request, 1000));
  const s = new GameState(config, () => 1000); s.accept(envelope()); let requests = 0;
  const handler = createGameHandler(config, s, { rolesResolver: async () => roles, clock: () => 1000, fetcher: async () => { requests++; throw new Error('timeout'); } });
  const i = interaction({ command: 'obrana' }); await handler(i); assert.equal(requests, 1); assert.match(i.calls[1][1].content, /unavailable/);
});

test('defense shows separated consistent courtyard capacities and receipt/source age, without correcting SDI counts', () => {
  const request = { schema_version: 2, request_id: randomUUID(), server_id: config.serverId, alliance_id: 444, player_id: 1 };
  const dto = { ...request, target: { player_id: 1, name: 'KrakenQ', castle_name: 'Hrad KrakenQ', castle_id: 2, x: 574, y: 528, kingdom_id: 0 },
    observed_at: 1000, fetched_at: 1000, source_age_seconds: 50, quality: 'partial',
    capacities: { wall: 8998, yard: 1029100, alliance: 286100, courtyard: 743000 }, castellan: null,
    positions: [[{ id: 489, count: 744, kind: 'troop' }], [{ id: 238, count: 3565, kind: 'troop' }], [{ id: 2, count: 99, kind: 'tool' }]] };
  validateDefense(dto, request, 1029); // A cached response keeps its original receipt/AS.
  const embed = renderDefense(dto).embeds[0];
  assert.match(embed.description, /Courtyard excluding alliance: 743000/);
  assert.match(embed.description, /Alliance support capacity: 286100/);
  assert.match(embed.description, /Total courtyard capacity including alliance: 1029100/);
  assert.match(embed.description, /Response fetched: <t:1000:F>/);
  assert.match(embed.description, /Source age at fetch: 50 s/);
  assert.match(embed.description, /Game measurement time unconfirmed/);
  assert.match(embed.description, /game dialog agreement unverified/);
  assert.match(embed.description, /Wall: 8998/);
  assert.equal(embed.fields[1].value, 'ID 238: 3565 (troop)');
  assert.equal(embed.fields[2].value, 'ID 2: 99 (tool)');
  for (const change of [{ fetched_at: 1029 }, { source_age_seconds: -1 }, { source_age_seconds: '50' }, { source_age_seconds: true },
    { capacities: { ...dto.capacities, courtyard: 743001 } }]) assert.throws(() => validateDefense({ ...dto, ...change }, request, 1000));
});

test('defense legacy, missing and inconsistent capacity/AS fields stay unknown; legitimate zero remains zero', () => {
  const request = { schema_version: 2, request_id: randomUUID(), server_id: config.serverId, alliance_id: 444, player_id: 1 };
  const dto = { ...request, target: { player_id: 1, name: 'Member', castle_name: 'Castle', castle_id: 2, x: 4, y: 5, kingdom_id: 0 },
    observed_at: 1000, quality: 'partial', capacities: { wall: null, yard: 10, alliance: null }, positions: null, castellan: null };
  for (const capacities of [dto.capacities, { wall: null, yard: null, alliance: 10 }, { wall: null, yard: 9, alliance: 10 }]) {
    validateDefense({ ...dto, capacities }, request, 1000);
    const description = renderDefense({ ...dto, capacities }).embeds[0].description;
    assert.match(description, /Courtyard excluding alliance: Neznámá/);
    assert.match(description, /Source age at fetch: Neznámé/);
  }
  const description = renderDefense({ ...dto, source_age_seconds: 0, capacities: { wall: 0, yard: 0, alliance: 0 } }).embeds[0].description;
  assert.match(description, /Courtyard excluding alliance: 0/);
  assert.match(description, /Source age at fetch: 0 s/);
});
