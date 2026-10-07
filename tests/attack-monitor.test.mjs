import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAttackServer, buildAttackMessage, validateAttack, readMonitorConfig, createDiscordSink } from '../src/attack-monitor.mjs';

const secret = 'test-only-shared-secret-32-characters-long';
const config = {
  secret, serverId: 'test-cz1', guildId: '100000000000000001', alertChannelId: '100000000000000002',
  alertRoleId: '100000000000000003', statusChannelId: '100000000000000004',
  host: '127.0.0.1', port: 0, staleSeconds: 120, startupGraceSeconds: 180,
  maxSeen: 1000, maxConcurrent: 20, dryRun: true,
};
const quiet = { warn() {}, info() {} };
const now = () => Math.floor(Date.now() / 1000);
export const fixture = (id = 1, kingdom = 0) => ({
  schema_version: 1, event_type: 'incoming_attack', server_id: config.serverId,
  movement_id: id, kingdom_id: kingdom, attacker_id: 101, attacker_name: 'Útočník', attacker_alliance: 'Other',
  defender_id: 202, defender_name: 'Obránce', target_id: 303, target_name: 'Hrad', target_x: 790, target_y: 804,
  troops: { value: 416843, accuracy: 'estimated' }, tools: { value: null, accuracy: 'unknown' },
  observed_at: now(), arrival_at: now() + 300,
});
const newSink = () => ({
  attacks: [], statuses: [], ready: true,
  isReady() { return this.ready; },
  async sendAttack(attack) { this.attacks.push(attack); },
  async sendStatus(text) { this.statuses.push(text); },
});

async function setup(sink = newSink(), opts = {}, overrides = {}) {
  const server = await createAttackServer({ ...config, ...overrides }, sink, { log: quiet, checkIntervalMs: 60_000, ...opts });
  const origin = `http://127.0.0.1:${server.address.port}`;
  const post = (path, body, token = secret) => fetch(origin + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(body),
  });
  return { server, origin, post, sink };
}

test('monitor is opt-in and dry-run is the default', () => {
  assert.equal(readMonitorConfig({}), null);
  const cfg = readMonitorConfig({ ATTACK_MONITOR_ENABLED: 'true', ATTACK_SHARED_SECRET: secret,
    GGE_SERVER_ID: config.serverId, DISCORD_GUILD_ID: config.guildId,
    ATTACK_CHANNEL_ID: config.alertChannelId, ATTACK_ROLE_ID: config.alertRoleId });
  assert.equal(cfg.dryRun, true);
  assert.throws(() => readMonitorConfig({ ATTACK_MONITOR_ENABLED: 'true' }));
});

test('only the chosen role can be pinged; game names cannot add mentions', () => {
  const attack = fixture(); attack.attacker_name = '@everyone <@&999999999999999999> **name**';
  const live = buildAttackMessage(attack, config.alertRoleId);
  assert.deepEqual(live.allowedMentions, { parse: [], roles: [config.alertRoleId], users: [], repliedUser: false });
  assert.match(live.embeds[0].fields[4].value, /odhad/);
  assert.equal(live.embeds[0].fields[5].value, 'Neznámé / Unknown');
  assert.ok(!live.embeds[0].fields[0].value.includes('@everyone'));
  const dry = buildAttackMessage(attack, config.alertRoleId, { dryRun: true });
  assert.deepEqual(dry.allowedMentions.roles, []);
  assert.ok(!dry.content.includes('<@&'));
});

test('wrong server, stale observations and invented precision are rejected', () => {
  assert.throws(() => validateAttack({ ...fixture(), server_id: 'wrong' }, config.serverId));
  assert.throws(() => validateAttack({ ...fixture(), observed_at: now() - 121 }, config.serverId));
  assert.throws(() => validateAttack({ ...fixture(), observed_at: now() + 60 }, config.serverId));
  assert.throws(() => validateAttack({ ...fixture(), troops: { value: 1, accuracy: 'unknown' } }, config.serverId));
  assert.throws(() => validateAttack({ ...fixture(), target_x: -1 }, config.serverId));
  assert.throws(() => validateAttack({ ...fixture(), movement_id: Number.MAX_SAFE_INTEGER + 1 }, config.serverId));
});

test('Unicode game labels match Python length limits and missing alliance is explicit', () => {
  const attack = validateAttack({ ...fixture(), attacker_name: '🏰'.repeat(200), attacker_alliance: null }, config.serverId);
  const field = buildAttackMessage(attack, config.alertRoleId).embeds[0].fields[0];
  assert.match(field.value, /Neznámé \/ Unknown/);
  assert.ok(field.value.length <= 1024);
  assert.throws(() => validateAttack({ ...fixture(), attacker_name: '🏰'.repeat(201) }, config.serverId));
});

test('full deduplication cache survives repeated snapshots and a failed new send', async () => {
  const sink = newSink();
  let fail = false;
  sink.sendAttack = async (attack) => {
    if (fail) throw new Error('simulated');
    sink.attacks.push(attack);
  };
  const { server, post } = await setup(sink, {}, { maxSeen: 2 });
  try {
    await post('/v1/attacks', fixture(1));
    await post('/v1/attacks', fixture(2));
    for (const id of [1, 2, 1, 2]) {
      assert.equal((await (await post('/v1/attacks', fixture(id))).json()).result, 'duplicate');
    }
    fail = true;
    assert.equal((await post('/v1/attacks', fixture(3))).status, 503);
    assert.equal((await (await post('/v1/attacks', fixture(1))).json()).result, 'duplicate');
    fail = false;
    await post('/v1/attacks', fixture(3));
    assert.equal((await (await post('/v1/attacks', fixture(2))).json()).result, 'duplicate');
    assert.equal(sink.attacks.length, 3);
  } finally { await server.close(); }
});

test('HTTP authentication and size limits prevent Discord sends', async () => {
  const { server, origin, post, sink } = await setup();
  try {
    assert.equal((await post('/v1/attacks', fixture(), 'wrong')).status, 401);
    assert.equal((await post('/v1/attacks', { ...fixture(), huge: 'x'.repeat(33_000) })).status, 413);
    const result = await fetch(origin + '/v1/attacks', { method: 'POST', headers: { Authorization: `Bearer ${secret}` }, body: '{}' });
    assert.equal(result.status, 415);
    assert.equal(sink.attacks.length, 0);
  } finally { await server.close(); }
});

test('repeated snapshots deduplicate, distinct movements and worlds do not', async () => {
  const { server, post, sink } = await setup();
  try {
    assert.equal((await (await post('/v1/attacks', fixture(1))).json()).result, 'sent_dry_run');
    assert.equal((await (await post('/v1/attacks', fixture(1))).json()).result, 'duplicate');
    await post('/v1/attacks', fixture(2));
    await post('/v1/attacks', fixture(1, 2));
    assert.equal(sink.attacks.length, 3);
  } finally { await server.close(); }
});

test('concurrent requests for one movement share one Discord send', async () => {
  const sink = newSink(); let release;
  const gate = new Promise((resolve) => { release = resolve; });
  sink.sendAttack = async (attack) => { sink.attacks.push(attack); await gate; };
  const { server, post } = await setup(sink);
  try {
    const first = post('/v1/attacks', fixture(10));
    const second = post('/v1/attacks', fixture(10));
    // Yield until the first operation has actually reached the sink.
    while (sink.attacks.length === 0) await new Promise((resolve) => setImmediate(resolve));
    release();
    assert.equal((await first).status, 200);
    assert.equal((await second).status, 200);
    assert.equal(sink.attacks.length, 1);
  } finally { release(); await server.close(); }
});

test('failed Discord send is not remembered as delivered; retry works', async () => {
  const sink = newSink(); let fail = true;
  sink.sendAttack = async (attack) => { if (fail) throw new Error('simulated'); sink.attacks.push(attack); };
  const { server, post } = await setup(sink);
  try {
    assert.equal((await post('/v1/attacks', fixture())).status, 503);
    assert.equal(server.health().state, 'delivery_failed');
    fail = false;
    assert.equal((await post('/v1/attacks', fixture())).status, 200);
    assert.equal(sink.attacks.length, 1);
    assert.notEqual(server.health().state, 'delivery_failed');
  } finally { await server.close(); }
});

test('expired attacks and a disconnected Discord do not generate false warnings', async () => {
  const { server, post, sink } = await setup();
  try {
    assert.equal((await (await post('/v1/attacks', { ...fixture(), arrival_at: now() - 1 })).json()).result, 'expired');
    sink.ready = false;
    assert.equal((await post('/v1/attacks', fixture())).status, 503);
    assert.equal(sink.attacks.length, 0);
  } finally { await server.close(); }
});

test('heartbeat measures fresh data independently of process liveness', async () => {
  let tick = now();
  const { server, post, origin, sink } = await setup(newSink(), { clock: () => tick });
  const heartbeat = () => ({ schema_version: 1, server_id: config.serverId, observed_at: tick,
    last_snapshot_at: tick, session: 'connected' });
  try {
    assert.equal((await fetch(origin + '/readyz')).status, 503);
    await post('/v1/heartbeat', heartbeat());
    assert.equal(server.health().state, 'healthy');
    await server.checkStatus();
    const firstCount = sink.statuses.length;
    await post('/v1/heartbeat', heartbeat()); await server.checkStatus();
    assert.equal(sink.statuses.length, firstCount);
    tick += 121;
    assert.equal(server.health().state, 'collector_missing');
    await post('/v1/heartbeat', { ...heartbeat(), last_snapshot_at: tick - 121 });
    assert.equal(server.health().state, 'game_data_stale');
    assert.equal((await fetch(origin + '/healthz')).status, 200);
    assert.equal((await fetch(origin + '/readyz')).status, 503);
    await post('/v1/heartbeat', { ...heartbeat(), session: 'disconnected' });
    assert.equal(server.health().state, 'disconnected');
  } finally { await server.close(); }
});

test('a new Core instance intentionally reannounces active movements', async () => {
  const sink = newSink();
  let setupResult = await setup(sink);
  await setupResult.post('/v1/attacks', fixture());
  await setupResult.server.close();
  setupResult = await setup(sink);
  try { await setupResult.post('/v1/attacks', fixture()); assert.equal(sink.attacks.length, 2); }
  finally { await setupResult.server.close(); }
});

test('Discord adapter refuses another guild and respects role ping permissions', async () => {
  const sent = [];
  let permission = true;
  const channel = { guildId: config.guildId, isTextBased: () => true,
    permissionsFor: () => ({ has: () => permission }), send: async (value) => sent.push(value) };
  const guild = { id: config.guildId, channels: { fetch: async () => channel },
    members: { fetchMe: async () => ({}) }, roles: { fetch: async () => ({ id: config.alertRoleId, mentionable: false }) } };
  const client = { isReady: () => true, guilds: { fetch: async () => guild } };
  const adapter = createDiscordSink(client, { ...config, dryRun: false });
  await adapter.sendAttack(fixture());
  assert.equal(sent.length, 1);
  permission = false;
  await assert.rejects(() => adapter.sendAttack(fixture()));
  channel.guildId = 'wrong';
  await assert.rejects(() => adapter.sendAttack(fixture()));
});
