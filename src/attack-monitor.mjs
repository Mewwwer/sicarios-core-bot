import http from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';

const MAX_BODY = 32_768;
const MAX_AGE = 120;
const MAX_FUTURE = 30;
const MAX_TRAVEL = 7 * 86_400;
const ACCURACY = new Set(['exact', 'estimated', 'unknown']);
const PERMISSIONS = { ViewChannel: 1024n, SendMessages: 2048n, EmbedLinks: 16384n, MentionEveryone: 131072n };

function integer(value, name, min, max) {
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`Invalid ${name}`);
  return value;
}

function label(value, name) {
  if (value === null) return null;
  // Python truncates game names by Unicode code point, not UTF-16 code unit.
  if (typeof value !== 'string' || [...value].length > 200 || /[\x00-\x1f\x7f]/u.test(value)) throw new Error(`Invalid ${name}`);
  return value.trim() || null;
}

function count(value, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !ACCURACY.has(value.accuracy)) throw new Error(`Invalid ${name}`);
  if (value.accuracy === 'unknown') {
    if (value.value !== null) throw new Error(`Unknown ${name} must be null`);
  } else integer(value.value, name, 0, 1_000_000_000);
  return { value: value.value, accuracy: value.accuracy };
}

function optionalInt(value, name, min = 0, max = Number.MAX_SAFE_INTEGER) {
  return value === null ? null : integer(value, name, min, max);
}

export function validateAttack(value, serverId, now = Date.now() / 1000) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.schema_version !== 1 || value.event_type !== 'incoming_attack') throw new Error('Invalid attack schema');
  if (value.server_id !== serverId) throw new Error('Unexpected game server');
  const observed = integer(value.observed_at, 'observed_at', 1, Number.MAX_SAFE_INTEGER);
  if (observed < now - MAX_AGE || observed > now + MAX_FUTURE) throw new Error('Stale or future observation');
  const arrival = optionalInt(value.arrival_at, 'arrival_at', 1);
  if (arrival !== null && arrival > now + MAX_TRAVEL) throw new Error('Invalid arrival time');
  return {
    schema_version: 1, event_type: 'incoming_attack', server_id: serverId,
    movement_id: integer(value.movement_id, 'movement_id', 0, Number.MAX_SAFE_INTEGER),
    kingdom_id: integer(value.kingdom_id, 'kingdom_id', 0, 1000),
    attacker_id: optionalInt(value.attacker_id, 'attacker_id', -1_000_000_000),
    attacker_name: label(value.attacker_name, 'attacker_name'),
    attacker_alliance: label(value.attacker_alliance, 'attacker_alliance'),
    defender_id: optionalInt(value.defender_id, 'defender_id'),
    defender_name: label(value.defender_name, 'defender_name'),
    target_id: optionalInt(value.target_id, 'target_id'),
    target_name: label(value.target_name, 'target_name'),
    target_x: optionalInt(value.target_x, 'target_x', 0, 1_000_000),
    target_y: optionalInt(value.target_y, 'target_y', 0, 1_000_000),
    troops: count(value.troops, 'troops'), tools: count(value.tools, 'tools'),
    observed_at: observed, arrival_at: arrival,
  };
}

function safeText(value) {
  return (value || 'Neznámé / Unknown').replace(/[\\`*_{}\[\]()~>|]/gu, '\\$&').replace(/@/gu, '@\u200b');
}

function countText(counted) {
  if (counted.accuracy === 'unknown') return 'Neznámé / Unknown';
  const prefix = counted.accuracy === 'estimated' ? '≈ ' : '';
  const suffix = counted.accuracy === 'estimated' ? ' (odhad / estimate)' : ' (přesné / exact)';
  return `${prefix}${new Intl.NumberFormat('cs-CZ').format(counted.value)}${suffix}`;
}

export function buildAttackMessage(attack, roleId, { dryRun = false } = {}) {
  const world = { 0: 'Velká říše / Great Empire', 2: 'Ledovec / Ice', 1: 'Písky / Sands', 3: 'Vrchy / Fire' }[attack.kingdom_id] || `Svět / Kingdom ${attack.kingdom_id}`;
  const coords = attack.target_x !== null && attack.target_y !== null ? `${attack.target_x}:${attack.target_y}` : 'Neznámé / Unknown';
  const attacker = `${safeText(attack.attacker_name)} (${safeText(attack.attacker_alliance)})`;
  return {
    content: dryRun ? '🧪 Test upozornění / Alert test — ping vypnutý / ping disabled' : `<@&${roleId}> 🚨 Útok na alianci / Alliance under attack`,
    allowedMentions: { parse: [], roles: dryRun ? [] : [roleId], users: [], repliedUser: false },
    embeds: [{
      title: dryRun ? 'TEST — Příchozí útok / Incoming attack' : 'Příchozí útok / Incoming attack',
      color: dryRun ? 0x3498db : 0xe74c3c,
      fields: [
        { name: 'Útočník / Attacker', value: attacker },
        { name: 'Napadený hráč / Defender', value: safeText(attack.defender_name) },
        { name: 'Cíl / Target', value: `${safeText(attack.target_name)} · ${coords}` },
        { name: 'Svět / Kingdom', value: world, inline: true },
        { name: 'Vojáci / Troops', value: countText(attack.troops), inline: true },
        { name: 'Nástroje / Tools', value: countText(attack.tools), inline: true },
        { name: 'Dopad / Arrival', value: attack.arrival_at === null ? 'Neznámý / Unknown' : `<t:${attack.arrival_at}:F>\n<t:${attack.arrival_at}:R>` },
      ],
      footer: { text: `${attack.server_id} · movement ${attack.movement_id} · kingdom ${attack.kingdom_id}` },
      timestamp: new Date(attack.observed_at * 1000).toISOString(),
    }],
  };
}

export function readMonitorConfig(env = process.env) {
  if (env.ATTACK_MONITOR_ENABLED !== 'true') return null;
  const snowflake = (name, optional = false) => {
    const value = env[name];
    if (optional && !value) return null;
    if (!value || !/^\d{17,20}$/u.test(value)) throw new Error(`Missing or invalid ${name}`);
    return value;
  };
  if (!env.ATTACK_SHARED_SECRET || env.ATTACK_SHARED_SECRET.length < 32) throw new Error('ATTACK_SHARED_SECRET must have at least 32 characters');
  if (!env.GGE_SERVER_ID || !/^[a-zA-Z0-9_.-]{1,80}$/u.test(env.GGE_SERVER_ID)) throw new Error('Missing or invalid GGE_SERVER_ID');
  const numeric = (name, fallback, min, max) => {
    const value = Number(env[name] ?? fallback);
    return integer(value, name, min, max);
  };
  return {
    secret: env.ATTACK_SHARED_SECRET, serverId: env.GGE_SERVER_ID,
    guildId: snowflake('DISCORD_GUILD_ID'), alertChannelId: snowflake('ATTACK_CHANNEL_ID'),
    statusChannelId: snowflake('ATTACK_STATUS_CHANNEL_ID', true), alertRoleId: snowflake('ATTACK_ROLE_ID'),
    port: numeric('ATTACK_HTTP_PORT', 8080, 1, 65535), host: env.ATTACK_HTTP_HOST || '0.0.0.0',
    staleSeconds: numeric('ATTACK_STALE_SECONDS', 120, 30, 3600),
    startupGraceSeconds: numeric('ATTACK_STARTUP_GRACE_SECONDS', 180, 0, 3600),
    maxSeen: numeric('ATTACK_MAX_SEEN', 10_000, 100, 100_000),
    maxConcurrent: 20, dryRun: env.ATTACK_DRY_RUN !== 'false',
  };
}

export function createDiscordSink(client, config) {
  const getChannel = async (id, needsPing = false) => {
    const guild = await client.guilds.fetch(config.guildId);
    const channel = await guild.channels.fetch(id);
    if (!channel || channel.guildId !== config.guildId || !channel.isTextBased() || typeof channel.send !== 'function') throw new Error('Invalid Discord alert channel');
    const me = await guild.members.fetchMe();
    const permissions = channel.permissionsFor(me);
    for (const name of ['ViewChannel', 'SendMessages', 'EmbedLinks']) {
      if (!permissions?.has(PERMISSIONS[name])) throw new Error(`Discord channel requires ${name}`);
    }
    if (needsPing) {
      const role = await guild.roles.fetch(config.alertRoleId);
      if (!role || role.id === guild.id) throw new Error('Invalid attack alert role');
      if (!role.mentionable && !permissions.has(PERMISSIONS.MentionEveryone)) throw new Error('Discord alert role cannot be mentioned');
    }
    return channel;
  };
  return {
    isReady: () => client.isReady(),
    sendAttack: async (attack) => (await getChannel(config.alertChannelId, !config.dryRun)).send(buildAttackMessage(attack, config.alertRoleId, config)),
    sendStatus: async (text) => {
      if (!config.statusChannelId) return;
      await (await getChannel(config.statusChannelId)).send({ content: text, allowedMentions: { parse: [], users: [], roles: [] } });
    },
  };
}

function readJson(request, limit = MAX_BODY) {
  return new Promise((resolve, reject) => {
    if (!/^application\/json(?:\s*;|$)/iu.test(request.headers['content-type'] || '')) {
      request.resume(); reject(Object.assign(new Error('JSON required'), { status: 415 })); return;
    }
    let size = 0;
    const chunks = [];
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) { reject(Object.assign(new Error('Payload too large'), { status: 413 })); return; }
      if (size <= limit) chunks.push(chunk);
    });
    request.on('end', () => {
      if (size > limit) return;
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch { reject(Object.assign(new Error('Invalid JSON'), { status: 400 })); }
    });
    request.on('error', reject);
    request.on('aborted', () => reject(Object.assign(new Error('Aborted'), { status: 400 })));
  });
}

function validateHeartbeat(value, config, now) {
  if (!value || value.schema_version !== 1 || value.server_id !== config.serverId) throw new Error('Invalid heartbeat');
  if (!['starting', 'connected', 'disconnected', 'login_failed', 'stopped'].includes(value.session)) throw new Error('Invalid session');
  const observed = integer(value.observed_at, 'observed_at', 1, Number.MAX_SAFE_INTEGER);
  const snapshot = optionalInt(value.last_snapshot_at, 'last_snapshot_at', 1);
  if (Math.abs(observed - now) > 30 || (snapshot !== null && snapshot > observed + 5)) throw new Error('Invalid heartbeat time');
  return { session: value.session, lastSnapshot: snapshot, receivedAt: now };
}

export async function createAttackServer(config, sink, { clock = () => Date.now() / 1000, log = console, checkIntervalMs = 10_000, gameState = null } = {}) {
  const started = clock();
  const secretHash = createHash('sha256').update(`Bearer ${config.secret}`).digest();
  const seen = new Map();
  const inFlight = new Map();
  let heartbeat = null;
  let announcedStatus = null;
  let statusInFlight = false;
  let closing = false;
  let deliveryFailed = false;
  const health = () => {
    const now = clock();
    if (!sink.isReady()) return { ready: false, state: 'discord_unavailable' };
    if (deliveryFailed) return { ready: false, state: 'delivery_failed' };
    if (!heartbeat) return { ready: false, state: now - started <= config.startupGraceSeconds ? 'starting' : 'collector_missing' };
    if (now - heartbeat.receivedAt > config.staleSeconds) return { ready: false, state: 'collector_missing' };
    if (heartbeat.session !== 'connected') return { ready: false, state: heartbeat.session };
    if (heartbeat.lastSnapshot === null || now - heartbeat.lastSnapshot > config.staleSeconds) return { ready: false, state: 'game_data_stale' };
    return { ready: true, state: 'healthy' };
  };
  const checkStatus = async () => {
    if (closing || statusInFlight || !sink.isReady()) return;
    const state = health().state;
    if (state === 'starting' || state === announcedStatus) return;
    statusInFlight = true;
    try {
      const messages = {
        healthy: '✅ Hlídač útoků má čerstvá herní data / Attack monitor has fresh game data.',
        collector_missing: '⚠️ Sběrač se nehlásí. Sledování útoků není ověřené / Collector heartbeat missing. Attack monitoring is unverified.',
        game_data_stale: '⚠️ Sběrač běží, ale herní data jsou zastaralá / Collector is running, but game data is stale.',
        disconnected: '⚠️ Sběrač ztratil spojení se hrou / Collector lost its game connection.',
        login_failed: '❌ Přihlášení do hry vyžaduje zásah / Game login needs attention.',
        stopped: '⚠️ Sběrač byl zastaven / Collector stopped.',
        delivery_failed: '❌ Upozornění se nepodařilo doručit do Discordu. Sběrač zkouší opakování / Discord alert delivery failed. Collector is retrying.',
      };
      await sink.sendStatus(messages[state] || '⚠️ Hlídač čeká na herní data / Monitor is waiting for game data.');
      announcedStatus = state;
    } catch { log.warn('[ATTACK] Status delivery failed; will retry.'); }
    finally { statusInFlight = false; }
  };
  const prune = () => {
    const now = clock();
    for (const [key, expiry] of seen) if (expiry <= now) seen.delete(key);
  };
  const deliver = async (attack) => {
    const key = JSON.stringify([attack.server_id, attack.kingdom_id, attack.movement_id]);
    prune();
    if (seen.has(key)) return { status: 200, result: 'duplicate' };
    if (attack.arrival_at !== null && attack.arrival_at <= clock()) return { status: 200, result: 'expired' };
    if (!sink.isReady() || closing) return { status: 503, result: 'discord_unavailable' };
    if (inFlight.has(key)) return inFlight.get(key);
    if (inFlight.size >= config.maxConcurrent) return { status: 429, result: 'busy' };
    const promise = (async () => {
      try {
        await sink.sendAttack(attack);
        deliveryFailed = false;
        void checkStatus();
        // No durable records. A restart intentionally clears these keys.
        // Reserve space only on successful insertion, never on duplicate reads
        // or failed sends. Concurrent completions must obey the bound as well.
        prune();
        while (seen.size >= config.maxSeen) seen.delete(seen.keys().next().value);
        seen.set(key, attack.arrival_at === null ? clock() + 86_400 : attack.arrival_at + 3600);
        return { status: 200, result: config.dryRun ? 'sent_dry_run' : 'sent' };
      } catch {
        deliveryFailed = true;
        void checkStatus();
        log.warn('[ATTACK] Discord alert delivery failed; collector may retry.');
        return { status: 503, result: 'delivery_failed' };
      }
    })();
    inFlight.set(key, promise);
    try { return await promise; }
    finally { inFlight.delete(key); }
  };
  const json = (response, status, data) => {
    if (response.destroyed || response.writableEnded) return;
    response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...(status === 429 || status === 503 ? { 'Retry-After': '5' } : {}) });
    response.end(JSON.stringify(data));
  };
  const server = http.createServer(async (request, response) => {
    try {
      if (request.method === 'GET' && request.url === '/healthz') return json(response, 200, { alive: !closing, ...(gameState ? { game_commands: gameState.diagnostics() } : {}) });
      if (request.method === 'GET' && request.url === '/readyz') {
        // Operational diagnosis only. Do NOT use this to gate Northflank
        // routing: the receiver must accept heartbeats while the feed is stale.
        const state = health(); return json(response, state.ready ? 200 : 503, state);
      }
      if (request.method !== 'POST' || !['/v1/attacks', '/v1/heartbeat', '/v2/state'].includes(request.url)) return json(response, 404, { error: 'not_found' });
      const incomingHash = createHash('sha256').update(request.headers.authorization || '').digest();
      if (!timingSafeEqual(incomingHash, secretHash)) { request.resume(); return json(response, 401, { error: 'unauthorized' }); }
      const value = await readJson(request, request.url === '/v2/state' ? 1_048_576 : MAX_BODY);
      if (request.url === '/v2/state') {
        if (!gameState) return json(response, 503, { error: 'feature_disabled' });
        gameState.accept(value); return json(response, 200, { result: 'accepted' });
      }
      if (request.url === '/v1/heartbeat') {
        heartbeat = validateHeartbeat(value, config, clock());
        json(response, 200, { result: 'accepted' });
        void checkStatus(); return;
      }
      const attack = validateAttack(value, config.serverId, clock());
      // The Discord operation can outlive an HTTP timeout. Keep its in-flight
      // key until it actually completes so a transport retry does not send twice.
      const deadline = setTimeout(() => {
        deliveryFailed = true; void checkStatus();
        json(response, 503, { result: 'delivery_pending' });
      }, 10_000);
      try { const result = await deliver(attack); json(response, result.status, { result: result.result }); }
      finally { clearTimeout(deadline); }
    } catch (error) { json(response, error.status || 400, { error: error.status ? 'invalid_request' : 'invalid_payload' }); }
  });
  server.requestTimeout = 15_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 5_000;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(config.port, config.host, () => { server.removeListener('error', reject); resolve(); });
  });
  const timer = setInterval(() => { prune(); void checkStatus(); }, checkIntervalMs);
  timer.unref();
  return {
    address: server.address(), health, checkStatus,
    close: async () => {
      if (closing) return;
      closing = true; clearInterval(timer);
      await new Promise((resolve) => { server.close(resolve); server.closeAllConnections(); });
    },
  };
}

export async function startAttackMonitor(client, { env = process.env, log = console, gameState = null } = {}) {
  const config = readMonitorConfig(env);
  if (!config) return null;
  const server = await createAttackServer(config, createDiscordSink(client, config), { log, gameState });
  log.info(`[ATTACK] Internal receiver started on port ${config.port}; dry run: ${config.dryRun}.`);
  return server;
}
