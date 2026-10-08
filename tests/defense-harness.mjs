// Offline Core command → real HTTP collector → mocked ephemeral Discord reply.
import { randomUUID } from 'node:crypto';
import { GameState } from '../src/game-state.mjs';
import { createGameHandler } from '../src/game-commands.mjs';
const now = Math.floor(Date.now() / 1000);
const config = { enabled: true, defense: true, serverId: 'test-cz1', allianceId: 444, guildId: '100000000000000001', channelId: '100000000000000002', collectorUrl: process.argv[2], secret: 'test-secret-at-least-32-characters' };
const state = new GameState(config);
state.accept({ schema_version: 2, server_id: config.serverId, alliance_id: 444, collector_instance_id: randomUUID(), sequence: 1, generated_at: now,
  members: { observed_at: now, complete: true, truncated: false, count: 1, items: [{ player_id: 222, name: 'Člen', online_state: 'unknown' }] } });
const interaction = { commandName: 'obrana', guildId: config.guildId, channelId: config.channelId, user: { id: 'member' }, inGuild: () => true, isAutocomplete: () => false,
  guild: { ownerId: 'owner', members: { fetch: async () => ({ id: 'member', roles: { cache: new Set(['member']) } }) } },
  options: { getString: () => '222' }, deferReply: async (options) => { if (options.flags !== 64) throw new Error('Not ephemeral'); }, editReply: async (reply) => console.log(JSON.stringify(reply)) };
await createGameHandler(config, state, { rolesResolver: async () => ({ member: { id: 'member' } }) })(interaction);
