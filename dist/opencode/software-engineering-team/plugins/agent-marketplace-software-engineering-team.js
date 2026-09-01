import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  existsSync,
  readFileSync,
  readdirSync,
  realpathSync,
} from 'node:fs';
import {
  basename,
  dirname,
  isAbsolute,
  relative,
  resolve,
  sep,
} from 'node:path';
import { fileURLToPath } from 'node:url';

const buildId = "snapshot.95d5570e01ba925c1708579195c17e4e90c75343e7a4038ccd99b348dc2fb4de";
const vaultHookSha256 = "75e9da2777ebfc29c399303f2bae663a09691a80c1d5f181efb7ade84db4b075";
const component = 'software-engineering-team';
const pluginFilename = 'agent-marketplace-software-engineering-team.js';
const mutators = new Set(['write', 'edit', 'apply_patch', 'bash']);
const safeTools = new Set([
  'glob',
  'grep',
  'list',
  'lsp',
  'plan_enter',
  'plan_exit',
  'question',
  'read',
  'skill',
  'task',
  'todoread',
  'todowrite',
  'webfetch',
  'websearch',
]);
const calls = new Map();
let boundShellFamily = 'unknown';

function fail(code, detail = '') {
  const clean = String(detail).replace(/\s+/g, ' ').trim().slice(0, 600);
  throw new Error(clean ? `${code}: ${clean}` : code);
}

function sha256(path, code) {
  try {
    return createHash('sha256').update(readFileSync(path)).digest('hex');
  } catch (error) {
    fail(code, error.message);
  }
}

function canonical(candidate, code = 'unsafe_path') {
  if (typeof candidate !== 'string' || !candidate) fail(code);
  const suffix = [];
  let probe = resolve(candidate);
  while (true) {
    try {
      return resolve(realpathSync.native(probe), ...suffix.reverse());
    } catch (error) {
      const parent = dirname(probe);
      if (parent === probe) fail(code, error.message);
      suffix.push(basename(probe));
      probe = parent;
    }
  }
}

function contained(root, candidate, code = 'pre_hook_denied') {
  const target = canonical(isAbsolute(candidate) ? candidate : resolve(root, candidate), code);
  const route = relative(root, target);
  if (route === '..' || route.startsWith(`..${sep}`) || isAbsolute(route)) {
    fail(code, `path escapes project root: ${candidate}`);
  }
  return target;
}

function readJson(path, code) {
  try {
    const value = JSON.parse(readFileSync(path, 'utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail(code);
    return value;
  } catch (error) {
    if (String(error.message).startsWith(code)) throw error;
    fail(code, error.message);
  }
}

function verifyFile(path, expected, code) {
  if (typeof expected !== 'string' || !/^[0-9a-f]{64}$/.test(expected)) fail(code);
  if (sha256(path, code) !== expected) fail(code, `hash differs: ${path}`);
}

function pluginPath() {
  return canonical(fileURLToPath(import.meta.url), 'hook_contract_incompatible');
}

function projectRoot() {
  const plugin = pluginPath();
  const plugins = dirname(plugin);
  const opencode = dirname(plugins);
  if (basename(plugin) !== pluginFilename
      || basename(plugins) !== 'plugins'
      || basename(opencode) !== '.opencode') {
    fail('agent_marketplace_runtime_unbound');
  }
  return canonical(dirname(opencode), 'agent_marketplace_runtime_unbound');
}

function privateRoot(root) {
  return canonical(
    resolve(root, '.opencode', 'agentrof', 'agent-marketplace'),
    'agent_marketplace_runtime_unbound',
  );
}

function normalizePluginReference(value) {
  if (typeof value !== 'string' || !value) fail('unsupported_plugin_set');
  if (value.startsWith('file:')) {
    try {
      return canonical(fileURLToPath(value), 'unsupported_plugin_set');
    } catch (error) {
      fail('unsupported_plugin_set', error.message);
    }
  }
  if (/^[A-Za-z]:[\\/]/.test(value)) {
    return canonical(value, 'unsupported_plugin_set');
  }
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)) fail('unsupported_plugin_set');
  return canonical(value, 'unsupported_plugin_set');
}

function assertSupportedPluginSet(config) {
  const own = pluginPath();
  const configured = config && config.plugin;
  if (!Array.isArray(configured)) {
    fail('unsupported_plugin_set', 'configured plugin value is not an array');
  }
  if (configured.length !== 1) {
    fail('unsupported_plugin_set', `expected one configured plugin, found ${configured.length}`);
  }
  if (normalizePluginReference(configured[0]) !== own) {
    fail('unsupported_plugin_set', 'configured plugin path differs from runtime plugin');
  }
  const discovered = readdirSync(dirname(own), { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(?:[cm]?js|tsx?)$/i.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  if (discovered.length !== 1 || discovered[0] !== pluginFilename) {
    fail('unsupported_plugin_set_directory', `unexpected plugin files: ${discovered.join(',')}`);
  }
}

function runtime() {
  const root = projectRoot();
  const privateDirectory = privateRoot(root);
  const installationPath = resolve(privateDirectory, 'installation.json');
  if (!existsSync(installationPath)) fail('agent_marketplace_runtime_unbound');
  const installation = readJson(installationPath, 'unsupported_installation_schema');
  if (installation.schema_version !== 1 || installation.component !== component) {
    fail('unsupported_installation_schema');
  }
  if (installation.active_full_build_id !== buildId) fail('client_restart_required');
  if (installation.transaction_state !== 'ready') fail('maintenance_busy');
  if (existsSync(resolve(privateDirectory, 'runtime', 'maintenance.json'))) {
    fail('maintenance_busy');
  }

  const key = installation.active_build_key;
  if (typeof key !== 'string' || !/^[A-Za-z0-9._-]+$/.test(key)) {
    fail('manifest_hash_mismatch');
  }
  const expectedManifestRelative = `packages/${key}/${component}/.agent-marketplace-package.json`;
  if (installation.package_manifest_path !== expectedManifestRelative) {
    fail('manifest_hash_mismatch');
  }
  const manifestPath = contained(
    privateDirectory,
    expectedManifestRelative,
    'manifest_hash_mismatch',
  );
  verifyFile(
    manifestPath,
    installation.package_manifest_sha256,
    'manifest_hash_mismatch',
  );
  const manifest = readJson(manifestPath, 'manifest_hash_mismatch');
  if (manifest.schema_version !== 2
      || manifest.component !== component
      || manifest.host !== 'opencode'
      || manifest.build_id !== buildId
      || !manifest.files
      || typeof manifest.files !== 'object'
      || Array.isArray(manifest.files)) {
    fail('manifest_hash_mismatch');
  }
  const hookRelative = 'scripts/vault_hook.py';
  if (manifest.files[hookRelative] !== vaultHookSha256) fail('manifest_hash_mismatch');
  const packageDirectory = dirname(manifestPath);
  const hookPath = contained(packageDirectory, hookRelative, 'manifest_hash_mismatch');
  verifyFile(hookPath, vaultHookSha256, 'manifest_hash_mismatch');

  const bindings = installation.runtime_bindings;
  if (!Array.isArray(bindings) || bindings.length !== 1
      || !bindings[0] || typeof bindings[0] !== 'object') {
    fail('runtime_unbound');
  }
  const binding = bindings[0];
  if (binding.opencode_version !== '1.18.17'
      || !Array.isArray(installation.tested_opencode_versions)
      || !installation.tested_opencode_versions.includes(binding.opencode_version)) {
    fail('runtime_binding_drift');
  }
  const own = pluginPath();
  if (!Array.isArray(binding.effective_plugins)
      || binding.effective_plugins.length !== 1
      || normalizePluginReference(binding.effective_plugins[0]) !== own) {
    fail('unsupported_plugin_set_binding');
  }
  const owned = installation.public_owned_files;
  const ownedPlugin = owned && owned[`plugins/${pluginFilename}`];
  if (!ownedPlugin || ownedPlugin.kind !== 'public') fail('projection_drift');
  verifyFile(own, ownedPlugin.sha256, 'projection_drift');

  const pythonPath = canonical(binding.python_path, 'runtime_binding_drift');
  const opencodePath = canonical(binding.opencode_path, 'runtime_binding_drift');
  verifyFile(pythonPath, binding.python_sha256, 'runtime_binding_drift');
  verifyFile(opencodePath, binding.opencode_sha256, 'runtime_binding_drift');
  return {
    hookPath,
    installation,
    opencodePath,
    privateDirectory,
    pythonPath,
    root,
  };
}

function callKey(input) {
  if (!input || typeof input.sessionID !== 'string' || !input.sessionID
      || typeof input.callID !== 'string' || !input.callID) {
    fail('hook_contract_incompatible');
  }
  return `${input.sessionID}:${input.callID}`;
}

function inputArgs(input, output) {
  const args = output && output.args !== undefined ? output.args : input.args;
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    fail('hook_contract_incompatible');
  }
  return args;
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  }
  const encoded = JSON.stringify(value);
  return encoded === undefined ? 'null' : encoded;
}

function argumentFingerprint(args) {
  return createHash('sha256').update(stableJson(args)).digest('hex');
}

function requiredString(args, ...names) {
  for (const name of names) {
    if (typeof args[name] === 'string' && args[name]) return args[name];
  }
  fail('hook_contract_incompatible');
}

function patchPaths(text) {
  const lines = text.split(/\r?\n/);
  while (lines.length && lines[lines.length - 1].trim() === '') lines.pop();
  if (lines[0]?.trim() !== '*** Begin Patch'
      || lines[lines.length - 1]?.trim() !== '*** End Patch') {
    fail('hook_contract_incompatible', 'missing apply_patch boundary');
  }
  const paths = [];
  for (const line of lines) {
    const operation = /^\*\*\* (?:Add|Update|Delete) File: (.+)$/.exec(line);
    const move = /^\*\*\* Move to: (.+)$/.exec(line);
    if (operation) paths.push(operation[1].trim());
    if (move) paths.push(move[1].trim());
  }
  if (!paths.length || paths.some((path) => !path)) fail('hook_contract_incompatible');
  return paths;
}

function rejectManagedHostPath(root, target) {
  const hostRoot = canonical(resolve(root, '.opencode'), 'pre_hook_denied');
  const route = relative(hostRoot, target);
  if (route === '' || (!route.startsWith(`..${sep}`) && route !== '..' && !isAbsolute(route))) {
    fail('pre_hook_denied', 'the project-local OpenCode projection is machine-managed');
  }
}

function effectiveShellFamily(config) {
  const configured = config && config.shell;
  if (configured !== undefined && configured !== null
      && typeof configured !== 'string') return 'unknown';
  if (process.platform === 'win32') {
    const commandShell = configured
      ?? process.env.ComSpec
      ?? process.env.COMSPEC;
    const systemRoot = process.env.SystemRoot || process.env.WINDIR;
    if (!commandShell || !systemRoot) return 'unknown';
    try {
      const actual = realpathSync(commandShell).toLowerCase();
      const expected = realpathSync(resolve(systemRoot, 'System32', 'cmd.exe')).toLowerCase();
      return actual === expected ? 'cmd' : 'unknown';
    } catch {
      return 'unknown';
    }
  }
  const commandShell = configured ?? '/bin/sh';
  if (!isAbsolute(commandShell)) return 'unknown';
  try {
    const actual = realpathSync.native(commandShell);
    const allowed = new Set();
    for (const directory of ['/bin', '/usr/bin']) {
      for (const name of ['sh', 'bash', 'dash', 'zsh', 'ksh']) {
        const candidate = resolve(directory, name);
        if (existsSync(candidate)) allowed.add(realpathSync.native(candidate));
      }
    }
    return allowed.has(actual) ? 'posix' : 'unknown';
  } catch {
    return 'unknown';
  }
}

function canonicalPayload(input, args, root, shellFamily) {
  const common = {
    cwd: root,
    session_id: input.sessionID,
    tool_call_id: input.callID,
  };
  if (input.tool === 'write') {
    const filePath = requiredString(args, 'filePath', 'file_path');
    const target = contained(root, filePath);
    rejectManagedHostPath(root, target);
    if (typeof args.content !== 'string') fail('hook_contract_incompatible');
    return {
      ...common,
      tool_name: 'Write',
      tool_input: { file_path: filePath, content: args.content },
    };
  }
  if (input.tool === 'edit') {
    const filePath = requiredString(args, 'filePath', 'file_path');
    const target = contained(root, filePath);
    rejectManagedHostPath(root, target);
    const oldString = requiredString(args, 'oldString', 'old_string');
    if (typeof (args.newString ?? args.new_string) !== 'string') {
      fail('hook_contract_incompatible');
    }
    return {
      ...common,
      tool_name: 'Edit',
      tool_input: {
        file_path: filePath,
        old_string: oldString,
        new_string: args.newString ?? args.new_string,
      },
    };
  }
  if (input.tool === 'apply_patch') {
    const patch = requiredString(args, 'patchText', 'patch', 'text');
    for (const path of patchPaths(patch)) {
      const target = contained(root, path);
      rejectManagedHostPath(root, target);
    }
    return { ...common, tool_name: 'apply_patch', tool_input: { patch } };
  }
  if (input.tool === 'bash') {
    const command = requiredString(args, 'command');
    const cwd = args.workdir === undefined
      ? root
      : contained(root, requiredString(args, 'workdir'));
    return {
      ...common,
      cwd,
      shell_family: shellFamily,
      tool_name: 'Bash',
      tool_input: { command },
    };
  }
  fail('unsupported_mutator');
}

function runHook(bound, phase, payload) {
  const result = spawnSync(
    bound.pythonPath,
    ['-B', bound.hookPath, phase],
    {
      cwd: payload.cwd,
      encoding: 'utf8',
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
      input: JSON.stringify(payload),
      maxBuffer: 1024 * 1024,
      timeout: 30000,
      windowsHide: true,
    },
  );
  const detail = result.stderr || result.error?.message || result.stdout || '';
  if (result.status === 0 && !result.error) return;
  if (phase === 'post') fail('post_hook_failed', detail);
  if (result.status === 2) fail('pre_hook_denied', detail);
  fail('hook_contract_incompatible', detail);
}

export const AgentMarketplacePlugin = async () => ({
  config: async (config) => {
    assertSupportedPluginSet(config);
    boundShellFamily = effectiveShellFamily(config);
  },
  'tool.execute.before': async (input, output) => {
    if (process.env.OPENCODE_EXPERIMENTAL_CODE_MODE || input.tool === 'execute') {
      fail('unsupported_mutator');
    }
    if (!mutators.has(input.tool)) {
      if (!safeTools.has(input.tool)) fail('unsupported_mutator');
      return;
    }
    const key = callKey(input);
    if (calls.has(key)) fail('hook_contract_incompatible', 'duplicate call identity');
    const args = inputArgs(input, output);
    const bound = runtime();
    assertSupportedPluginSet({ plugin: [pluginPath()] });
    const payload = canonicalPayload(input, args, bound.root, boundShellFamily);
    runHook(bound, 'pre', payload);
    calls.set(key, {
      bound,
      fingerprint: argumentFingerprint(args),
      payload,
    });
  },
  'tool.execute.after': async (input) => {
    if (!mutators.has(input.tool)) return;
    const key = callKey(input);
    const record = calls.get(key);
    if (!record) fail('post_hook_failed', 'missing matching pre-hook event');
    try {
      const args = inputArgs(input);
      if (argumentFingerprint(args) !== record.fingerprint) {
        fail('post_hook_failed', 'tool arguments changed after pre-hook validation');
      }
      runHook(record.bound, 'post', record.payload);
      runtime();
    } finally {
      calls.delete(key);
    }
  },
});
