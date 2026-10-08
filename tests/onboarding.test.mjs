import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Collection } from 'discord.js';
import { handleCommand } from '../src/commands.mjs';
import { ROLE_NAMES } from '../src/config.mjs';

function setup(actorRoles = ['marshal'], targetRoles = ['czech'], ownerId = 'owner') {
  const records = new Map([['actor', new Set(actorRoles)], ['target', new Set(targetRoles)]]);
  const roles = new Collection(Object.entries(ROLE_NAMES).map(([id, name]) => [id, { id, name }]));
  const calls = [];
  const guild = { ownerId, roles: { cache: roles, fetch: async () => {} }, members: { fetch: async ({ user, force }) => {
    assert.equal(force, true);
    const snapshot = new Set(records.get(user));
    return { id: user, guild, user: { tag: user }, roles: { cache: snapshot,
      add: async (ids) => { for (const id of [ids].flat()) records.get(user).add(id); },
      remove: async (ids) => { for (const id of [ids].flat()) records.get(user).delete(id); } } };
  } } };
  const i = { guild, user: { id: 'actor' }, options: { getUser: () => ({ id: 'target', bot: false }) }, inGuild: () => true,
    deferReply: async () => { i.deferred = true; }, editReply: async (v) => calls.push(v) };
  return { i, calls, records };
}

test('onboarding still accepts, promotes, assigns attack alerts, and removes access while retaining language', async () => {
  const { i, records } = setup(); const log = [];
  for (const commandName of ['accept', 'promote', 'remove']) {
    i.commandName = commandName; await handleCommand(i, async (...args) => log.push(args));
    const roles = records.get('target');
    if (commandName === 'accept') { assert.ok(roles.has('recruit')); assert.ok(roles.has('czRecruitAccess')); assert.ok(!roles.has('attackAlerts')); }
    if (commandName === 'promote') { assert.ok(roles.has('member')); assert.ok(roles.has('czMemberAccess')); assert.ok(roles.has('attackAlerts')); assert.ok(!roles.has('recruit')); assert.ok(!roles.has('czRecruitAccess')); }
    if (commandName === 'remove') assert.deepEqual([...roles], ['czech']);
  }
  assert.equal(log.length, 3);
});

test('onboarding authority and leadership removal restrictions are preserved', async () => {
  for (const [actorRoles, targetRoles] of [[['member'], ['czech']], [['marshal'], ['leader', 'leadership']], [['deputy'], ['leader']]]) {
    const { i, records, calls } = setup(actorRoles, targetRoles); i.commandName = 'remove';
    await handleCommand(i, async () => assert.fail('denied command must not log an action'));
    assert.deepEqual([...records.get('target')], targetRoles); assert.match(calls.at(-1), /❌/);
  }
});
