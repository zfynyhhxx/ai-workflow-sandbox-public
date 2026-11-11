// 仅供本项目 AO 进程树预加载；所有 TCP listener 限定回环，不修改系统防火墙或上游包。
const net = require('node:net');
const original = net.Server.prototype.listen;
net.Server.prototype.listen = function (...args) {
  const first = args[0];
  if (first && typeof first === 'object') {
    if (Object.hasOwn(first, 'port')) args[0] = {...first, host: '127.0.0.1'};
    else if (!Object.hasOwn(first, 'path')) throw new Error('Unsupported inherited listener; fail closed');
  } else if (typeof first === 'number' || (typeof first === 'string' && /^\d+$/.test(first))) {
    if (typeof args[1] === 'string') args[1] = '127.0.0.1';
    else args.splice(1, 0, '127.0.0.1');
  }
  return original.apply(this, args);
};
