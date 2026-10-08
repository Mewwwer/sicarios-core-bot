import { GameState } from '../src/game-state.mjs';
import { renderOverview } from '../src/game-commands.mjs';
// Local integration test harness. Does not import discord.js or use any token.
import { createAttackServer } from '../src/attack-monitor.mjs';
const gameState = new GameState({ enabled: true, serverId: 'test-cz1', allianceId: 444 });
const messages = [];
let failed = false;
const sink = {
  isReady: () => true,
  async sendAttack(attack) {
    if (process.env.TEST_FAIL_FIRST === 'true' && !failed) { failed = true; throw new Error('simulated failure'); }
    messages.push(attack);
  },
  async sendStatus() {},
};
const server = await createAttackServer({
  secret: process.env.ATTACK_SHARED_SECRET, serverId: 'test-cz1',
  host: '127.0.0.1', port: 0, dryRun: true,
  startupGraceSeconds: 180, staleSeconds: 120, maxSeen: 1000, maxConcurrent: 20,
}, sink, { log: { warn() {} }, gameState });
console.log(JSON.stringify({ origin: `http://127.0.0.1:${server.address.port}` }));
let stopping = false;
const stop = async () => {
  if (stopping) return;
  stopping = true;
  await server.close();
  console.log(JSON.stringify({ attackCount: messages.length, messages, state: gameState.diagnostics(), overview: renderOverview(gameState, 'attacks'), online: renderOverview(gameState, 'members') }));
  process.stdin.destroy();
};
process.once('SIGTERM', stop);
process.stdin.once('data', stop);
