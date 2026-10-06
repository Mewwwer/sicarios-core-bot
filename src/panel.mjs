import {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  ChannelType,
} from 'discord.js';
import { BUTTON_IDS, CATEGORIES, CHANNELS } from './config.mjs';

const PANEL_CONTENT = `## 🌐 Jazyk / Language

Vyber si jazykovou preferenci tlačítky níže. Tlačítka fungují jako **přepínače** — můžeš mít češtinu, angličtinu nebo obě.

Preference sama o sobě neotevírá interní části serveru. Po přijetí bot automaticky synchronizuje správný Recruit/Member jazykový přístup.

Choose your language preference below. The buttons are **toggles** — you may select Czech, English, or both.

A preference alone does not unlock internal server areas. After acceptance, the bot automatically synchronizes the correct Recruit/Member language access.`;

export function rolePanelComponents() {
  const czech = new ButtonBuilder()
    .setCustomId(BUTTON_IDS.czech)
    .setLabel('Čeština / Czech')
    .setEmoji('🇨🇿')
    .setStyle(ButtonStyle.Secondary);

  const english = new ButtonBuilder()
    .setCustomId(BUTTON_IDS.english)
    .setLabel('Angličtina / English')
    .setEmoji('🇬🇧')
    .setStyle(ButtonStyle.Secondary);

  return [new ActionRowBuilder().addComponents(czech, english)];
}

function isOurPanel(message) {
  const customIds = message.components
    .flatMap((row) => row.components)
    .map((component) => component.customId)
    .filter(Boolean);

  return customIds.includes(BUTTON_IDS.czech) || customIds.includes(BUTTON_IDS.english);
}

export async function ensureRolePanel(guild, botUserId) {
  await guild.channels.fetch();

  const category = guild.channels.cache.find(
    (channel) => channel.type === ChannelType.GuildCategory && channel.name === CATEGORIES.start,
  );
  if (!category) throw new Error(`Missing category: ${CATEGORIES.start}`);

  const channel = guild.channels.cache.find(
    (item) => item.parentId === category.id && item.name === CHANNELS.roles && item.isTextBased(),
  );
  if (!channel) throw new Error(`Missing #${CHANNELS.roles} in ${CATEGORIES.start}`);

  const messages = await channel.messages.fetch({ limit: 50 });
  let panel = messages.find((message) => message.author.id === botUserId && isOurPanel(message));

  // Upgrade the static Setup Tool v2 message when the same Discord application is reused.
  if (!panel) {
    panel = messages.find(
      (message) =>
        message.author.id === botUserId &&
        (message.content.startsWith('## 🌐 Role / Roles') || message.content.startsWith('## 🌐 Jazyk / Language')),
    );
  }

  const payload = {
    content: PANEL_CONTENT,
    components: rolePanelComponents(),
  };

  if (panel) {
    await panel.edit(payload);
    return { channel, message: panel, created: false };
  }

  const created = await channel.send(payload);
  return { channel, message: created, created: true };
}
