import process from 'node:process';
import {
  Client,
  Events,
  GatewayIntentBits,
  MessageFlags,
} from 'discord.js';
import { BUTTON_IDS, CATEGORIES, CHANNELS, ROLE_NAMES } from './config.mjs';
import { handleCommand } from './commands.mjs';
import { ensureRolePanel } from './panel.mjs';
import { startAttackMonitor } from './attack-monitor.mjs';
import {
  activeLanguageLabel,
  resolveRoles,
  syncLanguageAccess,
} from './roles.mjs';

const TOKEN = process.env.DISCORD_TOKEN;
const GUILD_ID = process.env.DISCORD_GUILD_ID;

if (!TOKEN || !GUILD_ID) {
  console.error('Missing DISCORD_TOKEN or DISCORD_GUILD_ID in .env');
  process.exit(1);
}

const client = new Client({ intents: [GatewayIntentBits.Guilds] });
const attackMonitor = await startAttackMonitor(client);

async function getGuild() {
  return client.guilds.fetch(GUILD_ID);
}

async function logAction(guild, content) {
  try {
    await guild.channels.fetch();
    const botCategory = guild.channels.cache.find(
      (channel) => channel.name === CATEGORIES.bots && channel.type === 4,
    );
    const logChannel = guild.channels.cache.find(
      (channel) => channel.parentId === botCategory?.id && channel.name === CHANNELS.botLog && channel.isTextBased(),
    );
    if (logChannel) await logChannel.send(content);
  } catch (error) {
    console.warn(`[LOG] Could not write bot log: ${error.message}`);
  }
}

async function toggleLanguage(interaction, roleKey) {
  if (!interaction.inGuild()) return;
  await interaction.deferReply({ flags: MessageFlags.Ephemeral });

  try {
    const roles = await resolveRoles(interaction.guild);
    let member = await interaction.guild.members.fetch({ user: interaction.user.id, force: true });
    const role = roles[roleKey];
    const isRemoving = member.roles.cache.has(role.id);

    const hasRecruit = member.roles.cache.has(roles.recruit.id);
    const hasMember = member.roles.cache.has(roles.member.id);
    const hasLeadership = member.roles.cache.has(roles.leadership.id);

    if (isRemoving && (hasRecruit || hasMember) && !hasLeadership) {
      const otherPreference = roleKey === 'czech' ? roles.english : roles.czech;
      if (!member.roles.cache.has(otherPreference.id)) {
        await interaction.editReply('❌ Přijatý hráč musí mít alespoň jednu jazykovou preferenci / Accepted players must keep at least one language preference.');
        return;
      }
    }

    if (isRemoving) {
      await member.roles.remove(role.id, 'SICARIOS language preference toggle');
    } else {
      await member.roles.add(role.id, 'SICARIOS language preference toggle');
    }

    member = await interaction.guild.members.fetch({ user: member.id, force: true });
    member = await syncLanguageAccess(member, roles);

    const state = isRemoving ? 'vypnuta / disabled' : 'zapnuta / enabled';
    const statusText = hasLeadership
      ? 'Leadership vidí CZ i EN automaticky.'
      : hasRecruit || hasMember
        ? 'Technický jazykový přístup byl automaticky synchronizován.'
        : 'Preference je uložená; interní přístup získáš až po přijetí vedením.';

    await interaction.editReply(`✅ **${role.name}**: ${state}\nAktivní preference: **${activeLanguageLabel(member, roles)}**\n${statusText}`);
  } catch (error) {
    console.error('[BUTTON]', error);
    await interaction.editReply(`❌ Nepodařilo se změnit roli: ${error.message}`);
  }
}

client.once(Events.ClientReady, async (readyClient) => {
  console.log(`[READY] Logged in as ${readyClient.user.tag}`);

  try {
    const guild = await getGuild();
    console.log(`[READY] Connected to ${guild.name} (${guild.id})`);

    const roles = await resolveRoles(guild);
    const me = await guild.members.fetchMe();
    const managed = [
      roles.recruit,
      roles.member,
      roles.czRecruitAccess,
      roles.czMemberAccess,
      roles.enRecruitAccess,
      roles.enMemberAccess,
      roles.attackAlerts,
      roles.czech,
      roles.english,
      roles.leadership,
      roles.leader,
      roles.deputy,
      roles.marshal,
      roles.diplomat,
      roles.treasurer,
    ];
    const tooHigh = managed.filter((role) => role.position >= me.roles.highest.position);
    if (tooHigh.length) {
      console.warn('[READY] WARNING: Bot role is not above all roles it may need to manage:');
      for (const role of tooHigh) console.warn(`  - ${role.name}`);
    }

    const panel = await ensureRolePanel(guild, readyClient.user.id);
    console.log(`[READY] Role panel ${panel.created ? 'created' : 'updated'} in #${panel.channel.name}.`);
  } catch (error) {
    console.error('[READY] Initialization failed:', error);
  }
});

client.on(Events.InteractionCreate, async (interaction) => {
  try {
    if (interaction.isButton()) {
      if (interaction.customId === BUTTON_IDS.czech) return toggleLanguage(interaction, 'czech');
      if (interaction.customId === BUTTON_IDS.english) return toggleLanguage(interaction, 'english');
      return;
    }

    if (interaction.isChatInputCommand()) {
      return handleCommand(interaction, logAction);
    }
  } catch (error) {
    console.error('[INTERACTION]', error);
    try {
      const content = `❌ Neočekávaná chyba / Unexpected error: ${error.message}`;
      if (interaction.deferred || interaction.replied) await interaction.editReply({ content });
      else await interaction.reply({ content, flags: MessageFlags.Ephemeral });
    } catch {}
  }
});

let shuttingDown = false;
async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log('\n[SHUTDOWN] Closing attack monitor and Discord connection...');
  try {
    await attackMonitor?.close();
    await client.destroy();
    process.exit(0);
  } catch (error) {
    console.error('[SHUTDOWN] Failed:', error);
    process.exit(1);
  }
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

await client.login(TOKEN);
