import { randomUUID } from 'node:crypto';
import { MessageFlags, SlashCommandBuilder } from 'discord.js';
import { ALLIANCE_RANK_KEYS } from './config.mjs';
import { resolveRoles } from './roles.mjs';
import { CASEFOLD } from './unicode-casefold.mjs';

const NO_MENTIONS = { parse: [], users: [], roles: [], repliedUser: false };
export const safe = (s, limit = 100) => [...String(s ?? 'Neznámé / Unknown')].slice(0, limit).join('').replace(/[\\`*_{}\[\]()~>|]/gu, '\\$&').replace(/@/gu, '@\u200b');
export const fold = (s) => [...s.normalize('NFC')].map((c) => CASEFOLD[c] ?? c.toLowerCase()).join('').normalize('NFC');
const stamp = (s) => `<t:${s}:F>`;
const count = (s) => s.accuracy === 'unknown' ? 'unknown / neznámé' : `${s.accuracy === 'estimated' ? '≈' : ''}${s.value} (${s.accuracy})`;
const warning = '⚠️ Neúplný snapshot; zobrazen poslední kompletní přehled / Incomplete snapshot; showing last complete list.';

export function gameDefinitions(config) {
  if (!config.enabled) return [];
  const page = (o) => o.setName('stranka').setDescription('Stránka / Page').setMinValue(1);
  const definitions = [
    new SlashCommandBuilder().setName('online').setDescription('Online členové ve hře / Online alliance members').addIntegerOption(page),
    new SlashCommandBuilder().setName('utoky').setDescription('Aktuální alianční útoky / Current alliance attacks').addIntegerOption(page),
  ];
  if (config.defense) definitions.push(new SlashCommandBuilder().setName('obrana').setDescription('Experiment: hlavní hrad člena / Member main castle defense')
    .addStringOption((o) => o.setName('hrac').setDescription('Člen aliance / Alliance member').setRequired(true).setAutocomplete(true).setMaxLength(200)));
  return definitions.map((d) => d.setContexts(0));
}

export class Cooldowns {
  constructor(clock = () => Date.now() / 1000, limit = 2000) { this.clock = clock; this.limit = limit; this.items = new Map(); }
  take(key, seconds) {
    const now = this.clock();
    for (const [k, expiry] of this.items) if (expiry <= now) this.items.delete(k);
    if (this.items.has(key) || this.items.size >= this.limit) return false;
    this.items.set(key, now + seconds); return true;
  }
}

export function renderOverview(state, kind, page = 1, now = Date.now() / 1000) {
  const section = state.get(kind);
  if (section.status === 'missing') return { content: `${section.partial ? warning + '\n' : ''}Čekáme na kompletní data v0.2 / Waiting for complete v0.2 data.` };
  const data = section.value;
  if (section.status === 'stale') return { content: `Zastaralá data / Stale data · ${stamp(data.observed_at)}${section.partial ? '\n' + warning : ''}` };
  const online = data.items.filter((m) => m.online_state === 'online');
  const items = kind === 'members' ? online.sort((a, b) => a.name.localeCompare(b.name, 'cs') || a.player_id - b.player_id)
    : data.items.sort((a, b) => (a.arrival_at ?? Infinity) - (b.arrival_at ?? Infinity) || a.movement_id - b.movement_id || a.kingdom_id - b.kingdom_id);
  const pageSize = kind === 'members' ? 20 : 10;
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  if (!Number.isSafeInteger(page) || page < 1 || page > pages) return { content: `Neplatná stránka / Invalid page · 1–${pages}` };
  const current = items.slice((page - 1) * pageSize, page * pageSize);
  const footer = `Stránka / Page ${page}/${pages} · ${stamp(data.observed_at)}`;
  if (kind === 'members') {
    const offline = data.items.filter((m) => m.online_state === 'offline').length;
    return { embeds: [{ title: 'Online ve hře / Online in game', description: [
      section.partial ? warning : '', current.map((m) => safe(m.name, 80)).join('\n') || 'Nikdo z členů s dostupným stavem není online / No members with known status are online.',
      `Online ${online.length} · Offline ${offline} · Unknown ${data.count - online.length - offline} · Celkem / Total ${data.count}`, footer,
    ].filter(Boolean).join('\n\n') }] };
  }
  const world = { 0: 'Velká říše / Great Empire', 1: 'Písky / Sands', 2: 'Ledovec / Ice', 3: 'Vrchy / Fire' };
  return { embeds: [{ title: 'Aktuální útoky / Current attacks', description: `${section.partial ? warning + '\n' : ''}${!current.length ? 'Žádné aktuální útoky / No current attacks.\n' : ''}${footer}`,
    fields: current.map((a) => ({ name: `${a.server_id} · ${a.kingdom_id} · movement ${a.movement_id}`, value:
      `${safe(a.attacker_name, 30)} (${safe(a.attacker_alliance, 25)}) → ${safe(a.defender_name, 30)}\n${safe(a.target_name, 30)} · ${a.target_x ?? '?'}:${a.target_y ?? '?'} · ${world[a.kingdom_id] ?? a.kingdom_id}\nVojáci / Troops: ${count(a.troops)} · Nástroje / Tools: ${count(a.tools)}\n${a.arrival_at === null ? 'Dopad neznámý / Arrival unknown' : `${stamp(a.arrival_at)}${a.arrival_at <= now ? ' · Po předpokládaném dopadu / Past expected arrival' : ''}`}` })) }] };
}

export function resolvePlayer(section, input) {
  if (section.status !== 'fresh' || section.partial) return null;
  const direct = /^\d+$/u.test(input) ? section.value.items.find((m) => String(m.player_id) === input) : null;
  if (direct) return direct;
  const matches = section.value.items.filter((m) => fold(m.name) === fold(input));
  return matches.length === 1 ? matches[0] : null;
}

export function renderDefense(d) {
  const capacity = (v) => v === null ? 'Neznámá / Unknown' : String(v);
  const total = d.capacities.yard, alliance = d.capacities.alliance;
  const courtyard = total !== null && alliance !== null && total >= alliance ? total - alliance : null;
  const age = d.source_age_seconds ?? null;
  const positionNames = ['Levá / Left', 'Střed / Middle', 'Pravá / Right', 'Nádvoří / Keep', 'Stronghold', 'Podpora / Support', 'Rezerva / Reserve'];
  return { embeds: [{ title: 'Obrana — experiment / Defense — experiment', description:
    `${safe(d.target.name)} · ${safe(d.target.castle_name)} · ${d.target.x}:${d.target.y}\nNačtení odpovědi / Response fetched: ${stamp(d.fetched_at ?? d.observed_at)} · ${d.quality}\nStáří zdroje AS při načtení / Source age at fetch: ${age === null ? 'Neznámé / Unknown' : `${age} s`}\nČas měření ve hře nepotvrzen / Game measurement time unconfirmed.\n⚠️ Hodnoty SDI; shoda s herním dialogem neověřena / SDI values; game dialog agreement unverified.\nKapacita hradeb / Wall: ${capacity(d.capacities.wall)}\nNádvoří bez alianční části / Courtyard excluding alliance: ${capacity(courtyard)}\nKapacita alianční podpory / Alliance support capacity: ${capacity(alliance)}\nCelková kapacita nádvoří včetně aliance / Total courtyard capacity including alliance: ${capacity(total)}${total !== null && alliance !== null && total < alliance ? '\n⚠️ UYL < AUYL; nádvoří nelze odvodit / Cannot derive courtyard.' : ''}\nKastelán / Castellan: ${d.castellan ? `${safe(d.castellan.name)} · ID ${d.castellan.id ?? '?'}` : 'Neznámý / Unknown'}\nBez vybavení a predikce výsledku / No equipment audit or battle prediction.`,
    fields: d.positions === null ? [{ name: 'Jednotky / Units', value: 'Neznámé / Unknown' }] : d.positions.length === 0 ? [{ name: 'Jednotky / Units', value: 'Explicitně prázdné S / Explicit empty S' }]
      : d.positions.map((p, i) => { const text = p.map((u) => `ID ${u.id}: ${u.count} (${u.kind})`).join(', ') || 'Explicitně prázdná pozice / Explicit empty position'; return { name: positionNames[i], value: text.length > 600 ? text.slice(0, 580) + '… (zkráceno / shortened)' : text }; }) }] };
}

export function validateDefense(d, request, now) {
  const numeric = (v, min = 0) => Number.isSafeInteger(v) && v >= min && v <= Number.MAX_SAFE_INTEGER;
  if (!d || d.schema_version !== 2 || d.request_id !== request.request_id || d.server_id !== request.server_id || d.alliance_id !== request.alliance_id || d.target?.player_id !== request.player_id || d.target.kingdom_id !== 0 || !numeric(d.target.castle_id, 1) || !numeric(d.target.x) || !numeric(d.target.y) || !numeric(d.observed_at, 1) || d.observed_at < now - 30 || d.observed_at > now + 5 || !['complete', 'partial', 'unavailable'].includes(d.quality)) throw new Error('Invalid defense');
  for (const k of ['name', 'castle_name']) if (typeof d.target[k] !== 'string' || [...d.target[k]].length > 200) throw new Error('Invalid defense label');
  for (const k of ['wall', 'yard', 'alliance']) if (d.capacities?.[k] !== null && !numeric(d.capacities?.[k])) throw new Error('Invalid capacity');
  // Additive fields: an older v0.2 collector remains readable, with unknown AS
  // and observed_at labelled only as receipt time. Never as game measurement.
  if (d.fetched_at !== undefined && d.fetched_at !== d.observed_at) throw new Error('Invalid fetch time');
  if (d.source_age_seconds !== undefined && d.source_age_seconds !== null && !numeric(d.source_age_seconds)) throw new Error('Invalid source age');
  const expectedCourtyard = d.capacities.yard !== null && d.capacities.alliance !== null && d.capacities.yard >= d.capacities.alliance ? d.capacities.yard - d.capacities.alliance : null;
  if (d.capacities.courtyard !== undefined && d.capacities.courtyard !== expectedCourtyard) throw new Error('Invalid courtyard');
  if (d.quality === 'complete' && d.capacities.yard < d.capacities.alliance) throw new Error('Inconsistent capacities');
  if (d.positions !== null && (!Array.isArray(d.positions) || d.positions.length > 7 || d.positions.some((p) => !Array.isArray(p) || p.length > 100 || p.some((u) => !numeric(u.id) || !numeric(u.count) || !['troop', 'tool', 'unknown'].includes(u.kind))))) throw new Error('Invalid positions');
  if (d.castellan !== null && (!d.castellan || (d.castellan.id !== null && !numeric(d.castellan.id)) || (d.castellan.name !== null && (typeof d.castellan.name !== 'string' || [...d.castellan.name].length > 200)))) throw new Error('Invalid castellan');
  if (d.target.x > 1_000_000 || d.target.y > 1_000_000 || (d.quality === 'complete' && (d.positions === null || d.positions.some((p) => p.some((u) => u.kind === 'unknown')) || Object.values(d.capacities).some((v) => v === null) || d.castellan === null || d.castellan.id === null || d.castellan.name === null))) throw new Error('Invalid defense quality or coordinates');
  return d;
}

export function createGameHandler(config, state, { rolesResolver = resolveRoles, fetcher = fetch, clock = () => Date.now() / 1000 } = {}) {
  const cooldowns = new Cooldowns(clock);
  const authorize = async (i) => {
    if (!config.enabled || (i.commandName === 'obrana' && !config.defense) || !i.inGuild() || i.guildId !== config.guildId || i.channelId !== config.channelId) return false;
    const [roles, actor] = await Promise.all([rolesResolver(i.guild), i.guild.members.fetch({ user: i.user.id, force: true })]);
    return i.guild.ownerId === actor.id || ['member', 'leadership', ...ALLIANCE_RANK_KEYS].some((key) => actor.roles.cache.has(roles[key].id));
  };
  return async (i) => {
    if (!['online', 'utoky', 'obrana'].includes(i.commandName)) return false;
    const autocomplete = i.isAutocomplete();
    if (!autocomplete) await i.deferReply({ flags: MessageFlags.Ephemeral });
    const reply = (body) => i.editReply({ ...body, allowedMentions: NO_MENTIONS });
    try {
      if (!await authorize(i)) {
        if (autocomplete) await i.respond([]);
        else await reply({ content: 'Funkce vypnutá nebo nepovolený server, kanál či role / Feature disabled or unauthorized guild, channel or role.' });
        return true;
      }
      if (autocomplete) {
        const s = state.get('members');
        const text = fold(i.options.getFocused());
        const choices = s.status === 'fresh' && !s.partial ? s.value.items.filter((m) => fold(m.name).includes(text)).sort((a, b) => a.name.localeCompare(b.name, 'cs') || a.player_id - b.player_id).slice(0, 25).map((m) => ({ name: `${[...m.name].slice(0, 35).join('')}${[...m.name].length > 35 ? '…' : ''} · ${m.player_id}`, value: String(m.player_id) })) : [];
        await i.respond(choices); return true;
      }
      if (!cooldowns.take(`${i.user.id}:${i.commandName === 'obrana' ? 'defense' : 'read'}`, i.commandName === 'obrana' ? 15 : 3)) {
        await reply({ content: 'Zkus příkaz později / Try again later.' }); return true;
      }
      if (i.commandName !== 'obrana') {
        await reply(renderOverview(state, i.commandName === 'online' ? 'members' : 'attacks', i.options.getInteger('stranka') ?? 1, clock())); return true;
      }
      const member = resolvePlayer(state.get('members'), i.options.getString('hrac', true));
      if (!member) { await reply({ content: 'Vyber jednoznačného člena z čerstvých kompletních dat / Select an unambiguous member from fresh complete data.' }); return true; }
      const request = { schema_version: 2, request_id: randomUUID(), server_id: config.serverId, alliance_id: config.allianceId, player_id: member.player_id };
      // No retry: an HTTP timeout must never start a second SDI operation.
      const response = await fetcher(config.collectorUrl + '/v2/defense', { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15_000), headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.secret}` }, body: JSON.stringify(request) });
      if (!response.ok) {
        await response.body?.cancel();
        const errors = { 422: 'Nepovolený nebo změněný cíl / Unsupported or changed target.', 429: 'Obrana je zaneprázdněná; zkus později / Defense busy; try later.', 503: 'Obrana vypnutá nebo relace nedostupná; po SDI timeoutu čeká na přirozené obnovení relace / Defense disabled or session unavailable; after SDI timeout it waits for a natural new session.', 504: 'Měření obrany vypršelo / Defense lookup timed out.' };
        await reply({ content: errors[response.status] ?? 'Služba obrany je nedostupná / Defense service unavailable.' }); return true;
      }
      const reader = response.body.getReader(); const chunks = []; let size = 0;
      while (true) { const { done, value } = await reader.read(); if (done) break; size += value.byteLength; if (size > 65_536) { await reader.cancel(); throw new Error('Oversized defense'); } chunks.push(Buffer.from(value)); }
      const dto = validateDefense(JSON.parse(Buffer.concat(chunks).toString('utf8')), request, clock());
      await reply(renderDefense(dto)); return true;
    } catch {
      if (autocomplete) await i.respond([]);
      else await reply({ content: 'Herní data nebo služba jsou nedostupné / Game data or service unavailable.' });
      return true;
    }
  };
}
