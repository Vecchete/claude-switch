// Tests of the companion's worker.js with a fake chrome API. Usage: node test-worker.mjs WORKER
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(process.argv[2], "utf8");
let passes = 0;
const failures = [];
function check(name, condition) {
  if (condition) passes += 1;
  else {
    failures.push(name);
    console.log(`FAIL ${name}`);
  }
}

function cookie(name, value, domain = ".claude.ai") {
  return { name, value, domain, path: "/", hostOnly: !domain.startsWith("."), httpOnly: true, secure: true,
    sameSite: "lax", session: false, expirationDate: 4102444800, storeId: "0" };
}

function makeChrome(jar, log, { refuse = [] } = {}) {
  let blocked = false;
  const listeners = {};
  const replies = [];
  const chrome = {
    runtime: {
      connectNative: () => ({
        onMessage: { addListener: (fn) => (listeners.message = fn) },
        onDisconnect: { addListener: (fn) => (listeners.disconnect = fn) },
        postMessage: (message) => replies.push(message),
      }),
      onStartup: { addListener: () => {} },
    },
    alarms: { onAlarm: { addListener: () => {} }, create: () => {} },
    declarativeNetRequest: {
      updateSessionRules: async ({ addRules = [], removeRuleIds = [] }) => {
        if (removeRuleIds.length && blocked) log.push("unblock");
        blocked = addRules.length > 0;
        if (blocked) log.push("block");
      },
    },
    cookies: {
      getAll: async () => jar.map((c) => ({ ...c })),
      remove: async ({ name, url }) => {
        log.push(`remove:${name}${blocked ? "" : ":UNBLOCKED"}`);
        const host = new URL(url).host;
        const index = jar.findIndex((c) => c.name === name && c.domain.replace(/^\./, "") === host);
        if (index >= 0) jar.splice(index, 1);
      },
      set: async (details) => {
        log.push(`set:${details.name}${blocked ? "" : ":UNBLOCKED"}`);
        if (refuse.includes(details.name)) return null;
        const domain = details.domain ?? new URL(details.url).host;
        const index = jar.findIndex((c) => c.name === details.name && c.domain === domain);
        if (index >= 0) jar.splice(index, 1);
        jar.push({ ...cookie(details.name, details.value, domain), hostOnly: !details.domain });
      },
    },
    tabs: {
      query: async () => [{ id: 7 }],
      reload: async () => log.push(blocked ? "reload:BLOCKED" : "reload"),
    },
  };
  return { chrome, listeners, replies, isBlocked: () => blocked };
}

async function run(jar, message, options) {
  const log = [];
  const fake = makeChrome(jar, log, options);
  const context = vm.createContext({ chrome: fake.chrome, setTimeout, clearTimeout, URL, console, Promise });
  vm.runInContext(source, context);
  fake.listeners.message({ id: 1, ...message });
  for (let i = 0; i < 100 && !fake.replies.length; i += 1) await new Promise((r) => setTimeout(r, 50));
  return { reply: fake.replies[0], log, fake };
}

const SESSION = ["sessionKey", "sessionKeyLC", "routingHint", "lastActiveOrg"];
const device = [cookie("ion-vk", "v", "claude.ai"), cookie("anthropic-device-id", "d", "claude.ai")];

// capture
let jar = [cookie("sessionKey", "a"), cookie("lastActiveOrg", "org-a"), cookie("routingHint", "r"), ...device];
let { reply, log, fake } = await run(jar, { cmd: "capture", names: SESSION });
check("capture: blocks claude.ai first", log[0] === "block");
check("capture: returns the session cookies only", reply.cookies.map((c) => c.name).sort().join() === "lastActiveOrg,routingHint,sessionKey");
check("capture: leaves claude.ai blocked for the swap", fake.isBlocked());

// apply, from a capture
jar = [cookie("sessionKeyLC", "1"), cookie("lastActiveOrg", "org-a"), cookie("sessionKey", "a"), cookie("routingHint", "r"), ...device];
const remove = jar.filter((c) => SESSION.includes(c.name));
const add = [cookie("sessionKey", "b"), cookie("lastActiveOrg", "org-b"), cookie("sessionKeyLC", "2"), cookie("routingHint", "q")];
log = [];
const applied = makeChrome(jar, log);
const context = vm.createContext({ chrome: applied.chrome, setTimeout, clearTimeout, URL, console, Promise });
vm.runInContext(source, context);
applied.listeners.message({ id: 1, cmd: "capture", names: SESSION });
for (let i = 0; i < 100 && !applied.replies.length; i += 1) await new Promise((r) => setTimeout(r, 50));
applied.listeners.message({ id: 2, cmd: "apply", remove, add });
for (let i = 0; i < 100 && applied.replies.length < 2; i += 1) await new Promise((r) => setTimeout(r, 50));
const removals = log.filter((e) => e.startsWith("remove:"));
const sets = log.filter((e) => e.startsWith("set:"));
check("apply: no error", applied.replies[1] && !applied.replies[1].error);
check("apply: sessionKey leaves first", removals[0] === "remove:sessionKey");
check("apply: sessionKey comes back last", sets[sets.length - 1] === "set:sessionKey");
check("apply: every change happens while blocked", !log.some((e) => e.endsWith(":UNBLOCKED")));
check("apply: unblocks before the reload", log.indexOf("unblock") >= 0 && log.indexOf("unblock") < log.indexOf("reload"));
check("apply: reloads the claude.ai tabs", log.includes("reload") && !log.includes("reload:BLOCKED"));
check("apply: the other session is in place", jar.find((c) => c.name === "sessionKey")?.value === "b");
check("apply: the device cookies stay", device.every((d) => jar.some((c) => c.name === d.name && c.value === d.value)));

// apply when Chrome refuses a cookie
jar = [cookie("sessionKey", "a"), ...device];
({ reply, log, fake } = await run(jar, { cmd: "apply", remove: [cookie("sessionKey", "a")], add: [cookie("sessionKey", "b"), cookie("routingHint", "q")] }, { refuse: ["routingHint"] }));
check("refused: says which cookie", reply.error && reply.error.includes("routingHint"));
check("refused: still unblocks", !fake.isBlocked());

// release, and a host that goes away
({ reply, log, fake } = await run([...device], { cmd: "capture", names: SESSION }));
fake.listeners.message({ id: 2, cmd: "release" });
for (let i = 0; i < 100 && fake.replies.length < 2; i += 1) await new Promise((r) => setTimeout(r, 50));
check("release: unblocks", !fake.isBlocked());
({ reply, log, fake } = await run([...device], { cmd: "capture", names: SESSION }));
fake.listeners.disconnect();
await new Promise((r) => setTimeout(r, 50));
check("host gone: unblocks", !fake.isBlocked());

console.log(`${passes} passed, ${failures.length} failed`);
process.exit(failures.length ? 1 : 0);
