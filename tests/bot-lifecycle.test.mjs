import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import net from 'node:net';

for (const enabled of [false, true]) {
  test(`real bot starts and handles SIGTERM with monitor ${enabled ? 'enabled' : 'disabled'}`, { timeout: 10_000 }, async () => {
    const reservation = net.createServer();
    reservation.listen(0, '127.0.0.1');
    await once(reservation, 'listening');
    const port = reservation.address().port;
    await new Promise((resolve) => reservation.close(resolve));
    const child = fork(new URL('../src/bot.mjs', import.meta.url), [], {
      execArgv: ['--import', new URL('./mock-discord-login.mjs', import.meta.url).href],
      silent: true,
      env: {
        ...process.env, DISCORD_TOKEN: 'offline-test-only', DISCORD_GUILD_ID: '100000000000000001',
        ATTACK_MONITOR_ENABLED: String(enabled), ATTACK_DRY_RUN: 'true',
        ATTACK_SHARED_SECRET: 'offline-test-secret-at-least-32-characters', GGE_SERVER_ID: 'test-cz1',
        ATTACK_CHANNEL_ID: '100000000000000002', ATTACK_ROLE_ID: '100000000000000003',
        ATTACK_STATUS_CHANNEL_ID: '', ATTACK_HTTP_HOST: '127.0.0.1', ATTACK_HTTP_PORT: String(port),
      },
    });
    const messages = [];
    child.on('message', (message) => messages.push(message.event));
    const closed = once(child, 'close');
    const deadline = setTimeout(() => child.kill('SIGKILL'), 8000);
    let output = '';
    child.stdout.on('data', (data) => { output += data; });
    child.stderr.on('data', (data) => { output += data; });
    try {
      const started = await Promise.race([once(child, 'message'), closed]);
      assert.equal(started[0]?.event, 'login', output);
      if (enabled) {
        assert.equal((await fetch(`http://127.0.0.1:${port}/healthz`)).status, 200);
        assert.equal((await fetch(`http://127.0.0.1:${port}/readyz`)).status, 503);
      } else {
        await assert.rejects(fetch(`http://127.0.0.1:${port}/healthz`));
      }
      child.kill('SIGTERM');
      const [code, signal] = await closed;
      assert.equal(code, 0, output);
      assert.equal(signal, null, output);
      assert.deepEqual(messages, ['login', 'destroyed']);
    } finally {
      clearTimeout(deadline);
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
      await closed;
    }
  });
}
