export const ROLE_NAMES = Object.freeze({
  administrator: '🔧 Administrátor / Administrator',
  leader: '👑 Velitel / Leader',
  deputy: '⭐ Zástupce / Deputy',
  marshal: '⚔️ Maršál / Marshal',
  diplomat: '🕊️ Diplomat / Diplomat',
  treasurer: '💰 Pokladník / Treasurer',
  leadership: '🛡️ Vedení / Leadership',
  member: '👤 Člen / Member',
  recruit: '🌱 Nováček / Recruit',
  czRecruitAccess: '🔐 CZ Recruit Access',
  czMemberAccess: '🔐 CZ Member Access',
  enRecruitAccess: '🔐 EN Recruit Access',
  enMemberAccess: '🔐 EN Member Access',
  attackAlerts: '🚨 Útoky / Attack Alerts',
  czech: '🇨🇿 Čeština / Czech',
  english: '🇬🇧 Angličtina / English',
});

export const MANAGED_ACCESS_KEYS = Object.freeze([
  'czRecruitAccess',
  'czMemberAccess',
  'enRecruitAccess',
  'enMemberAccess',
]);

export const ALLIANCE_RANK_KEYS = Object.freeze([
  'leader',
  'deputy',
  'marshal',
  'diplomat',
  'treasurer',
]);

export const ONBOARDING_MANAGER_KEYS = Object.freeze([
  'leader',
  'deputy',
  'marshal',
]);

export const CHANNELS = Object.freeze({
  roles: 'role-roles',
  botLog: 'log-bota-bot-log',
});

export const CATEGORIES = Object.freeze({
  start: '📌 ZAČÍT / START HERE',
  bots: '🤖 BOTI / BOTS',
});

export const BUTTON_IDS = Object.freeze({
  czech: 'sicarios:language:czech',
  english: 'sicarios:language:english',
});
