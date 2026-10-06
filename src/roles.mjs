import { ROLE_NAMES, MANAGED_ACCESS_KEYS } from './config.mjs';

export async function resolveRoles(guild) {
  await guild.roles.fetch();
  const result = {};

  for (const [key, name] of Object.entries(ROLE_NAMES)) {
    const matches = guild.roles.cache.filter((role) => role.name === name);
    if (matches.size === 0) {
      throw new Error(`Required SICARIOS role is missing: ${name}`);
    }
    if (matches.size > 1) {
      throw new Error(`Duplicate SICARIOS roles found with name: ${name}`);
    }
    result[key] = matches.first();
  }

  return result;
}

export function languagePreferences(member, roles) {
  return {
    czech: member.roles.cache.has(roles.czech.id),
    english: member.roles.cache.has(roles.english.id),
  };
}

export function memberStatus(member, roles) {
  return {
    recruit: member.roles.cache.has(roles.recruit.id),
    member: member.roles.cache.has(roles.member.id),
    leadership: member.roles.cache.has(roles.leadership.id),
  };
}

export async function syncLanguageAccess(member, roles) {
  const prefs = languagePreferences(member, roles);
  const status = memberStatus(member, roles);
  const desired = new Set();

  // Leadership sees both language sections directly through the Leadership role.
  // Keeping access roles off Leadership avoids stale/redundant technical state.
  if (!status.leadership) {
    // Member intentionally takes precedence over Recruit if both are ever
    // present due to a manual mistake or interrupted transition. The command
    // workflow still repairs the invariant so only one status remains.
    if (status.member) {
      if (prefs.czech) desired.add(roles.czMemberAccess.id);
      if (prefs.english) desired.add(roles.enMemberAccess.id);
    } else if (status.recruit) {
      if (prefs.czech) desired.add(roles.czRecruitAccess.id);
      if (prefs.english) desired.add(roles.enRecruitAccess.id);
    }
  }

  const managedIds = MANAGED_ACCESS_KEYS.map((key) => roles[key].id);
  const toRemove = managedIds.filter((id) => !desired.has(id) && member.roles.cache.has(id));

  // IMPORTANT: never remove and then add roles using the same GuildMember snapshot.
  // GuildMemberRoleManager#add() can otherwise send a stale role set and re-add a
  // role that was just removed. Always refresh from Discord between mutations.
  if (toRemove.length) {
    await member.roles.remove(toRemove, 'SICARIOS: synchronize language access');
    member = await member.guild.members.fetch({ user: member.id, force: true });
  }

  const toAdd = managedIds.filter((id) => desired.has(id) && !member.roles.cache.has(id));
  if (toAdd.length) {
    await member.roles.add(toAdd, 'SICARIOS: synchronize language access');
    member = await member.guild.members.fetch({ user: member.id, force: true });
  }

  return member;
}

export function hasAnyLanguagePreference(member, roles) {
  const prefs = languagePreferences(member, roles);
  return prefs.czech || prefs.english;
}

export function activeLanguageLabel(member, roles) {
  const prefs = languagePreferences(member, roles);
  if (prefs.czech && prefs.english) return '🇨🇿 Čeština + 🇬🇧 English';
  if (prefs.czech) return '🇨🇿 Čeština';
  if (prefs.english) return '🇬🇧 English';
  return 'žádný / none';
}
