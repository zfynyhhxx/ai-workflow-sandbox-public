// 固定版本的进程级修复；不改 node_modules，不伪造 running 状态。
const fs = require('node:fs');
const crypto = require('node:crypto');
const {pathToFileURL} = require('node:url');
const {registerHooks} = require('node:module');
const ORIGINAL = 'const hasNonTerminalSession = await projectHasNonTerminalSession(config, projectId);';

function rewriteSupervisor(source, project) {
  if (!/^[a-zA-Z0-9_-]+$/.test(project)) throw new Error('Invalid explicit AO project');
  if (source.split(ORIGINAL).length !== 2) throw new Error('AO supervisor source contract changed');
  // 显式启动本身就是监督需求，不再依赖已经存在会话才能首次派发。
  return source.replace(ORIGINAL,
    `const hasNonTerminalSession = projectId === ${JSON.stringify(project)} || await projectHasNonTerminalSession(config, projectId);`);
}

function rewriteOpenCode(source) {
  const header = 'getLaunchCommand(config) {';
  if (source.split(header).length !== 2) throw new Error('AO OpenCode launch contract changed');
  return source.replace(header, header + '\n return ["opencode", "--wf-stage", shellEscape(config.prompt || "")].join(" ");');
}

function installPinnedHook(target, expectedHash, project, rewrite = rewriteSupervisor) {
  const expectedURL = pathToFileURL(fs.realpathSync(target)).href;
  const hash = source => crypto.createHash('sha256').update(source).digest('hex');
  if (hash(fs.readFileSync(target)) !== expectedHash) throw new Error('AO_SOURCE_HASH_MISMATCH: re-audit before startup');
  return registerHooks({
    load(url, context, nextLoad) {
      const loaded = nextLoad(url, context);
      if (url !== expectedURL) return loaded;
      const source = Buffer.from(loaded.source).toString('utf8');
      if (hash(source) !== expectedHash) throw new Error('AO_LOADED_SOURCE_MISMATCH');
      return {...loaded, source: rewrite(source, project)};
    }
  });
}

if (process.env.WF_AO_SUPERVISOR) {
  const config = require('../config/ao-compat.json');
  installPinnedHook(process.env.WF_AO_SUPERVISOR, config.supervisor_sha256, config.project);
  if (!process.env.WF_AO_OPENCODE_PLUGIN) throw new Error('Missing pinned OpenCode adapter');
  installPinnedHook(process.env.WF_AO_OPENCODE_PLUGIN, config.opencode_plugin_sha256, config.project, rewriteOpenCode);
}
module.exports = {rewriteSupervisor, rewriteOpenCode, installPinnedHook};
