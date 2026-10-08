import { validateAttack } from './attack-monitor.mjs';

export const STATE_TTL = 120;
const LIMITS = { members: 250, attacks: 500 };
const fail = () => { throw new Error('Invalid state'); };
const int = (v, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && v >= min && v <= max ? v : fail();
const timestamp = (v, ceiling) => { int(v, 1); if (v > ceiling + 5) fail(); return v; };
export function readGameConfig(env = process.env) {
  const enabled = env.GAME_COMMANDS_ENABLED === 'true';
  const defense = env.DEFENSE_LOOKUP_ENABLED === 'true';
  if (defense && !enabled) throw new Error('DEFENSE_LOOKUP_ENABLED requires GAME_COMMANDS_ENABLED');
  if (!enabled) return { enabled: false, defense: false };
  if (env.ATTACK_MONITOR_ENABLED !== 'true') throw new Error('GAME_COMMANDS_ENABLED requires ATTACK_MONITOR_ENABLED');
  const allianceId = int(Number(env.GGE_ALLIANCE_ID), 1);
  for (const name of ['DISCORD_GUILD_ID', 'GAME_COMMAND_CHANNEL_ID']) {
    if (!/^\d{17,20}$/u.test(env[name] || '')) throw new Error(`Missing or invalid ${name}`);
  }
  if (!/^[a-zA-Z0-9_.-]{1,80}$/u.test(env.GGE_SERVER_ID || '') || (env.ATTACK_SHARED_SECRET || '').length < 32) throw new Error('Missing game server or shared secret');
  let collectorUrl = null;
  if (defense) {
    const url = new URL(env.ATTACK_COLLECTOR_URL || 'http://sicarios-attack-collector:8081');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Invalid ATTACK_COLLECTOR_URL');
    collectorUrl = url.origin;
  }
  return { enabled, defense, allianceId, serverId: env.GGE_SERVER_ID, guildId: env.DISCORD_GUILD_ID,
    channelId: env.GAME_COMMAND_CHANNEL_ID, secret: env.ATTACK_SHARED_SECRET, collectorUrl };
}

function section(value, kind, config, generated) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
  const observed = timestamp(value.observed_at, generated);
  if (typeof value.complete !== 'boolean' || typeof value.truncated !== 'boolean' || (value.complete && value.truncated)) fail();
  if (!Array.isArray(value.items) || value.items.length > LIMITS[kind] || int(value.count, 0, 1_000_000) !== value.items.length) fail();
  const keys = new Set();
  const items = value.items.map((item) => {
    let result, key;
    if (kind === 'members') {
      if (!item || typeof item.name !== 'string' || !item.name.trim() || [...item.name].length > 200 || /[\x00-\x1f\x7f]/u.test(item.name) || !['online', 'offline', 'unknown'].includes(item.online_state)) fail();
      result = { player_id: int(item.player_id, 1), name: item.name.normalize('NFC'), online_state: item.online_state };
      key = result.player_id;
    } else {
      result = validateAttack(item, config.serverId, observed);
      if (result.observed_at !== observed) fail();
      key = JSON.stringify([result.server_id, result.kingdom_id, result.movement_id]);
    }
    if (keys.has(key)) fail();
    keys.add(key); return result;
  });
  return { observed_at: observed, complete: value.complete, truncated: value.truncated, count: items.length, items };
}

export class GameState {
  constructor(config, clock = () => Date.now() / 1000) {
    this.config = config; this.clock = clock; this.instance = null; this.sequence = 0;
    this.generated = 0; this.retired = new Set(); this.sections = {}; this.partial = {};
  }
  accept(value) {
    if (!this.config.enabled) throw Object.assign(new Error('Disabled'), { status: 503 });
    if (!value || value.schema_version !== 2 || value.server_id !== this.config.serverId || value.alliance_id !== this.config.allianceId || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu.test(value.collector_instance_id || '')) fail();
    int(value.sequence, 1);
    const generated = timestamp(value.generated_at, this.clock() + 25);
    if (generated < this.clock() - STATE_TTL) fail();
    const different = this.instance !== value.collector_instance_id;
    if (this.retired.has(value.collector_instance_id) || (!different && (value.sequence <= this.sequence || generated < this.generated)) || (different && this.instance && generated <= this.generated)) throw Object.assign(new Error('Out of order'), { status: 409 });
    const parsed = {};
    for (const kind of Object.keys(LIMITS)) if (value[kind] !== undefined) {
      parsed[kind] = section(value[kind], kind, this.config, generated);
      if (this.sections[kind] && parsed[kind].observed_at < this.sections[kind].observed_at) fail();
    }
    if (!Object.keys(parsed).length) fail();
    if (different) {
      if (this.instance) this.retired.add(this.instance);
      while (this.retired.size > 64) this.retired.delete(this.retired.values().next().value);
      // The generated-time high water mark protects even evicted retired UUIDs.
    }
    for (const [kind, current] of Object.entries(parsed)) {
      // Incomplete lists are withheld. Retain the last complete observation,
      // without renewing its timestamp or inferring removals from omissions.
      this.partial[kind] = !current.complete || current.truncated;
      if (!this.partial[kind]) this.sections[kind] = current;
    }
    this.instance = value.collector_instance_id; this.sequence = value.sequence; this.generated = generated;
  }
  get(kind) {
    const value = this.sections[kind];
    return { status: !value ? 'missing' : this.clock() - value.observed_at > STATE_TTL ? 'stale' : 'fresh',
      partial: Boolean(this.partial[kind]), value: value ? structuredClone(value) : null };
  }
  diagnostics() {
    return Object.fromEntries(Object.keys(LIMITS).map((kind) => { const s = this.get(kind); return [kind, { status: s.status, partial: s.partial, observed_at: s.value?.observed_at ?? null }]; }));
  }
}
