/** Electron Main Process 的 UOT 受控 bridge 参考实现。 */

import { spawn as defaultSpawn } from "node:child_process";

const AGENT_COMMANDS = new Set(["agent-start", "agent-switch", "agent-rollback"]);

/**
 * 创建仅供 Electron Main Process 使用的 UOT 控制器。
 *
 * Renderer 必须经 preload 暴露的窄 IPC 调用此对象；不能获得 bridge 路径、配置路径或
 * 任意命令执行能力。调用 handoff 后必须立即结束 Electron 进程，交由 Agent 接管。
 *
 * @param {object} options 控制器依赖。
 * @param {string} options.bridgeExecutable 已打包的 uot-bridge 可执行文件。
 * @param {string} options.configPath 由 Main Process 写入的无密钥 bridge 配置路径。
 * @param {{ exit: (code: number) => void }} options.app Electron app 实例。
 * @param {() => Promise<void>} [options.beforeHandoff] 保存业务状态的回调。
 * @param {typeof defaultSpawn} [options.spawn] 可注入的子进程启动函数。
 * @returns {ElectronUotController} 受控更新接口。
 */
export function createElectronUotController({
  bridgeExecutable,
  configPath,
  app,
  beforeHandoff = async () => {},
  spawn = defaultSpawn
}) {
  if (!bridgeExecutable || !configPath || !app || typeof app.exit !== "function") {
    throw new TypeError("bridgeExecutable, configPath, and app.exit are required.");
  }
  const call = (command, argumentsList = []) =>
    runUotBridge({ bridgeExecutable, configPath, command, argumentsList, spawn });

  return {
    check: () => call("check"),
    listInstalled: () => call("list-installed"),
    listRemote: () => call("list-remote"),
    status: () => call("status"),
    result: () => call("result"),
    install: async (version) => {
      const targetVersion = requireVersion(version);
      const oldPid = String(process.pid);
      const prepared = await call("prepare", ["--version", targetVersion, "--old-pid", oldPid]);
      await handoffAgent({ call, app, beforeHandoff, command: "agent-start", argumentsList: ["--old-pid", oldPid] });
      return prepared;
    },
    switchInstalled: async (version) => {
      const targetVersion = requireVersion(version);
      return handoffAgent({
        call,
        app,
        beforeHandoff,
        command: "agent-switch",
        argumentsList: ["--version", targetVersion, "--old-pid", String(process.pid)]
      });
    },
    rollback: () =>
      handoffAgent({
        call,
        app,
        beforeHandoff,
        command: "agent-rollback",
        argumentsList: ["--old-pid", String(process.pid)]
      })
  };
}

/**
 * 执行 bridge 并读取其唯一 JSON 响应。
 *
 * @param {object} options 执行参数。
 * @param {string} options.bridgeExecutable bridge 可执行文件。
 * @param {string} options.configPath 配置文件路径。
 * @param {string} options.command 固定的 bridge 子命令。
 * @param {string[]} options.argumentsList 经控制器校验后的命令参数。
 * @param {typeof defaultSpawn} options.spawn 子进程启动函数。
 * @returns {Promise<object>} bridge 成功负载。
 */
export function runUotBridge({ bridgeExecutable, configPath, command, argumentsList, spawn = defaultSpawn }) {
  return new Promise((resolve, reject) => {
    const child = spawn(bridgeExecutable, [command, "--config", configPath, ...argumentsList], {
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", (error) => reject(new Error(`Unable to start uot-bridge: ${error.message}`)));
    child.once("close", (code) => {
      const response = parseBridgeResponse(code, stdout, stderr, command);
      if (response instanceof Error) {
        reject(response);
      } else {
        resolve(response);
      }
    });
  });
}

/**
 * 完成 Agent ready、保存状态、handoff 和强制退出的不可分割顺序。
 *
 * @param {object} options 交接参数。
 * @param {(command: string, argumentsList?: string[]) => Promise<object>} options.call bridge 调用函数。
 * @param {{ exit: (code: number) => void }} options.app Electron app 实例。
 * @param {() => Promise<void>} options.beforeHandoff 状态保存回调。
 * @param {string} options.command Agent 启动命令。
 * @param {string[]} options.argumentsList Agent 启动参数。
 * @returns {Promise<object>} Agent ready 负载。
 */
async function handoffAgent({ call, app, beforeHandoff, command, argumentsList }) {
  if (!AGENT_COMMANDS.has(command)) {
    throw new TypeError(`Unsupported Agent command: ${command}`);
  }
  const agent = await call(command, argumentsList);
  const requestPath = agent.request_path;
  if (typeof requestPath !== "string" || !requestPath) {
    throw new Error("uot-bridge Agent response is missing request_path.");
  }
  await beforeHandoff();
  await call("agent-handoff", ["--request", requestPath]);
  app.exit(0);
  return agent;
}

/**
 * 解析 bridge 的成功或失败 JSON。
 *
 * @param {number | null} code 子进程退出码。
 * @param {string} stdout 标准输出。
 * @param {string} stderr 标准错误。
 * @param {string} command bridge 子命令。
 * @returns {object | Error} 成功负载或结构化错误。
 */
export function parseBridgeResponse(code, stdout, stderr, command) {
  const source = code === 0 ? stdout : stderr;
  let payload;
  try {
    payload = JSON.parse(source.trim());
  } catch {
    return new Error(`uot-bridge ${command} returned invalid JSON: ${source.trim() || "no output"}`);
  }
  if (code !== 0 || payload.ok !== true) {
    const error = payload.error || {};
    return new Error(`uot-bridge ${command} failed [${error.code || "UNKNOWN"}]: ${error.message || "no error message"}`);
  }
  return payload;
}

/**
 * 验证传入 bridge 的版本字符串。
 *
 * @param {unknown} version 用户选择的远端或本地版本。
 * @returns {string} 去除空白后的版本。
 */
function requireVersion(version) {
  const normalized = typeof version === "string" ? version.trim() : "";
  if (!normalized) {
    throw new TypeError("A non-empty version is required.");
  }
  return normalized;
}
