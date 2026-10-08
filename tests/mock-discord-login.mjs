// Preloaded only in offline lifecycle tests; never connects to Discord.
import { Client } from 'discord.js';

Client.prototype.login = async function () {
  this.offlineTimer = setInterval(() => {}, 1000);
  process.send({ event: 'login' });
  return 'offline';
};

Client.prototype.destroy = async function () {
  clearInterval(this.offlineTimer);
  process.send({ event: 'destroyed' });
};
