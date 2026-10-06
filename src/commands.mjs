import {
  MessageFlags,
  SlashCommandBuilder,
} from 'discord.js';
import {
  ALLIANCE_RANK_KEYS,
  ONBOARDING_MANAGER_KEYS,
} from './config.mjs';
import {
  activeLanguageLabel,
  hasAnyLanguagePreference,
  resolveRoles,
  syncLanguageAccess,
} from './roles.mjs';

export const COMMANDS = [
  new SlashCommandBuilder()
    .setName('accept')
    .setDescription('Přijme hráče jako Recruit / Accept a player as Recruit')
    .addUserOption((option) =>
      option.setName('user').setDescription('Hráč / Player').setRequired(true),
    ),
  new SlashCommandBuilder()
    .setName('promote')
    .setDescription('Povýší Recruita na Member / Promote Recruit to Member')
    .addUserOption((option) =>
      option.setName('user').setDescription('Hráč / Player').setRequired(true),
    ),
  new SlashCommandBuilder()
    .setName('remove')
    .setDescription('Odebere alianční přístupy / Remove alliance access')
    .addUserOption((option) =>
      option.setName('user').setDescription('Hráč / Player').setRequired(true),
    ),
];

async function getFreshMember(guild, userId) {
  // Always bypass the member cache after role mutations. Without force:true,
  // discord.js may return the pre-mutation GuildMember and onboarding becomes
  // one command behind (Recruit access added during /promote, etc.).
  return guild.members.fetch({ user: userId, force: true });
}

function canManageOnboarding(actor, guild, roles) {
  if (guild.ownerId === actor.id) return true;
  return ONBOARDING_MANAGER_KEYS.some((key) => actor.roles.cache.has(roles[key].id));
}


function actorHasRole(actor, role) {
  return actor.roles.cache.has(role.id);
}

function removalAuthorization(actor, target, guild, roles) {
  if (guild.ownerId === actor.id) return { allowed: true };

  const actorIsLeader = actorHasRole(actor, roles.leader);
  const actorIsDeputy = actorHasRole(actor, roles.deputy);
  const targetIsLeader = actorHasRole(target, roles.leader);
  const targetIsLeadership = actorHasRole(target, roles.leadership) || ALLIANCE_RANK_KEYS.some((key) => actorHasRole(target, roles[key]));

  if (targetIsLeader && !actorIsLeader) {
    return { allowed: false, reason: '❌ Velitele může tímto příkazem odebrat pouze jiný Velitel nebo vlastník serveru.' };
  }

  if (targetIsLeadership && !(actorIsLeader || actorIsDeputy)) {
    return { allowed: false, reason: '❌ Člena vedení může odebrat pouze Velitel, Zástupce nebo vlastník serveru.' };
  }

  return { allowed: true };
}

async function ephemeral(interaction, content) {
  if (interaction.deferred || interaction.replied) {
    return interaction.editReply({ content });
  }
  return interaction.reply({ content, flags: MessageFlags.Ephemeral });
}

async function prepare(interaction) {
  if (!interaction.inGuild()) {
    await ephemeral(interaction, 'Tento příkaz funguje pouze na serveru / This command only works in a server.');
    return null;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  const roles = await resolveRoles(interaction.guild);
  const actor = await getFreshMember(interaction.guild, interaction.user.id);

  if (!canManageOnboarding(actor, interaction.guild, roles)) {
    await interaction.editReply('❌ Tento příkaz může použít pouze Velitel, Zástupce nebo Maršál.');
    return null;
  }

  const user = interaction.options.getUser('user', true);
  if (user.bot) {
    await interaction.editReply('❌ Tento onboarding workflow je určený pro hráče, ne boty.');
    return null;
  }

  let target;
  try {
    target = await getFreshMember(interaction.guild, user.id);
  } catch {
    await interaction.editReply('❌ Tento uživatel není členem Discord serveru SICARIOS.');
    return null;
  }

  return { roles, actor, target };
}

export async function handleAccept(interaction, logAction) {
  const ctx = await prepare(interaction);
  if (!ctx) return;
  const { roles, actor, target } = ctx;

  if (target.roles.cache.has(roles.leadership.id)) {
    await interaction.editReply('❌ Uživatel už má Leadership. / User already has Leadership.');
    return;
  }
  if (target.roles.cache.has(roles.member.id)) {
    await interaction.editReply('❌ Uživatel už je Member. Použij /remove, pokud chceš jeho přístup odebrat.');
    return;
  }
  if (target.roles.cache.has(roles.recruit.id)) {
    const synced = await syncLanguageAccess(target, roles);
    await interaction.editReply(`ℹ️ Uživatel už je Recruit. Jazykový přístup byl znovu synchronizován: **${activeLanguageLabel(synced, roles)}**.`);
    return;
  }
  if (!hasAnyLanguagePreference(target, roles)) {
    await interaction.editReply('❌ Hráč si nejdřív musí v #role-roles vybrat 🇨🇿 Češtinu, 🇬🇧 English nebo obě.');
    return;
  }

  await target.roles.remove(
    [roles.member.id, roles.attackAlerts.id],
    'SICARIOS /accept: normalize status before Recruit acceptance',
  );
  let freshTarget = await getFreshMember(interaction.guild, target.id);
  await freshTarget.roles.add(roles.recruit.id, `SICARIOS /accept by ${actor.user.tag}`);
  freshTarget = await getFreshMember(interaction.guild, target.id);
  const synced = await syncLanguageAccess(freshTarget, roles);

  await interaction.editReply(`✅ ${target} byl přijat jako **🌱 Recruit**. Jazyk: **${activeLanguageLabel(synced, roles)}**.`);
  await logAction(interaction.guild, `✅ /accept — ${actor.user.tag} accepted ${target.user.tag} as Recruit (${activeLanguageLabel(synced, roles)}).`);
}

export async function handlePromote(interaction, logAction) {
  const ctx = await prepare(interaction);
  if (!ctx) return;
  const { roles, actor, target } = ctx;

  if (target.roles.cache.has(roles.member.id) && !target.roles.cache.has(roles.recruit.id)) {
    await interaction.editReply('ℹ️ Uživatel už je Member.');
    return;
  }
  if (!target.roles.cache.has(roles.recruit.id)) {
    await interaction.editReply('❌ Uživatel není Recruit. / User is not a Recruit.');
    return;
  }
  if (!hasAnyLanguagePreference(target, roles)) {
    await interaction.editReply('❌ Recruit nemá žádnou jazykovou preferenci. Nejdřív vyber Czech/English v #role-roles.');
    return;
  }

  // Add the new status first, refresh, then remove Recruit using the fresh
  // member object. This avoids discord.js re-applying a stale pre-removal
  // role set during a second sequential mutation.
  await target.roles.add([roles.member.id, roles.attackAlerts.id], `SICARIOS /promote by ${actor.user.tag}`);
  let freshTarget = await getFreshMember(interaction.guild, target.id);
  await freshTarget.roles.remove(roles.recruit.id, `SICARIOS /promote by ${actor.user.tag}`);
  freshTarget = await getFreshMember(interaction.guild, target.id);
  const synced = await syncLanguageAccess(freshTarget, roles);

  await interaction.editReply(`✅ ${target} byl povýšen na **👤 Member** a dostal **🚨 Attack Alerts**. Jazyk: **${activeLanguageLabel(synced, roles)}**.`);
  await logAction(interaction.guild, `⬆️ /promote — ${actor.user.tag} promoted ${target.user.tag} to Member (${activeLanguageLabel(synced, roles)}).`);
}

export async function handleRemove(interaction, logAction) {
  const ctx = await prepare(interaction);
  if (!ctx) return;
  const { roles, actor, target } = ctx;

  const removal = removalAuthorization(actor, target, interaction.guild, roles);
  if (!removal.allowed) {
    await interaction.editReply(removal.reason);
    return;
  }

  const removeIds = new Set([
    roles.recruit.id,
    roles.member.id,
    roles.czRecruitAccess.id,
    roles.czMemberAccess.id,
    roles.enRecruitAccess.id,
    roles.enMemberAccess.id,
    roles.attackAlerts.id,
    roles.leadership.id,
    ...ALLIANCE_RANK_KEYS.map((key) => roles[key].id),
  ]);

  const existing = [...removeIds].filter((id) => target.roles.cache.has(id));
  if (!existing.length) {
    await interaction.editReply('ℹ️ Uživatel nemá žádné spravované alianční přístupy k odebrání.');
    return;
  }

  await target.roles.remove(existing, `SICARIOS /remove by ${actor.user.tag}`);
  await interaction.editReply(`✅ ${target} byly odebrány alianční přístupy. Jazykové preference Czech/English zůstaly zachované.`);
  await logAction(interaction.guild, `🚪 /remove — ${actor.user.tag} removed alliance access from ${target.user.tag}.`);
}

export async function handleCommand(interaction, logAction) {
  if (interaction.commandName === 'accept') return handleAccept(interaction, logAction);
  if (interaction.commandName === 'promote') return handlePromote(interaction, logAction);
  if (interaction.commandName === 'remove') return handleRemove(interaction, logAction);
}
