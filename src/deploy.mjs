import process from 'node:process';
import { Client, Events, GatewayIntentBits } from 'discord.js';
import { commandDefinitions } from './commands.mjs';

const TOKEN = process.env.DISCORD_TOKEN;
const GUILD_ID = process.env.DISCORD_GUILD_ID;

if (!TOKEN || !GUILD_ID) {
  console.error('Missing DISCORD_TOKEN or DISCORD_GUILD_ID in .env');
  process.exit(1);
}

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

client.once(Events.ClientReady, async (readyClient) => {
  try {
    const guild = await readyClient.guilds.fetch(GUILD_ID);
    const data = commandDefinitions().map((command) => command.toJSON());
    const deployed = await guild.commands.set(data);
    console.log(`[DEPLOY] Registered ${deployed.size} guild commands in ${guild.name}:`);
    for (const command of deployed.values()) console.log(`  /${command.name}`);
  } catch (error) {
    console.error('[DEPLOY] Failed:', error);
    process.exitCode = 1;
  } finally {
    client.destroy();
  }
});

await client.login(TOKEN);
