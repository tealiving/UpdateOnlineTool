import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import { createElectronUotController, parseBridgeResponse, runUotBridge } from "./uot-host.mjs";

test("bridge invocation never uses a shell and returns JSON", async () => {
  let captured;
  const payload = await runUotBridge({
    bridgeExecutable: "/runtime/uot-bridge",
    configPath: "/runtime/config.json",
    command: "check",
    argumentsList: [],
    spawn: (command, argumentsList, options) => {
      captured = { command, argumentsList, options };
      return childWithResponse(0, '{"ok":true,"decision":"optional_update"}\n');
    }
  });

  assert.equal(captured.command, "/runtime/uot-bridge");
  assert.deepEqual(captured.argumentsList, ["check", "--config", "/runtime/config.json"]);
  assert.equal(captured.options.shell, false);
  assert.equal(payload.decision, "optional_update");
});

test("install saves state, confirms handoff, then exits Electron", async () => {
  const calls = [];
  const events = [];
  const controller = createElectronUotController({
    bridgeExecutable: "/runtime/uot-bridge",
    configPath: "/runtime/config.json",
    app: { exit: (code) => events.push(`exit:${code}`) },
    beforeHandoff: async () => events.push("saved"),
    spawn: (command, argumentsList) => {
      calls.push([command, ...argumentsList]);
      const bridgeCommand = argumentsList[0];
      const payload = bridgeCommand === "agent-start"
        ? { ok: true, request_path: "/install/operations/update.request.json" }
        : { ok: true, version: "1.2.0" };
      return childWithResponse(0, `${JSON.stringify(payload)}\n`);
    }
  });

  await controller.install("1.2.0");

  assert.deepEqual(calls.map((items) => items.slice(1, 2)), [["prepare"], ["agent-start"], ["agent-handoff"]]);
  assert.deepEqual(events, ["saved", "exit:0"]);
});

test("bridge failure keeps Electron running and exposes the UOT error", async () => {
  const error = parseBridgeResponse(1, "", '{"ok":false,"error":{"code":"PROCESS_TIMEOUT","message":"old app still running"}}', "agent-start");

  assert.ok(error instanceof Error);
  assert.match(error.message, /PROCESS_TIMEOUT/);
});

function childWithResponse(code, output) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  queueMicrotask(() => {
    if (code === 0) {
      child.stdout.end(output);
    } else {
      child.stderr.end(output);
    }
    child.emit("close", code);
  });
  return child;
}
