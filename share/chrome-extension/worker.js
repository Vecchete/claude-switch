// Companion of claude-switch. Its native host reads the claude.ai session cookies through it and has
// it swap them. Which cookies make a session is the host's decision.
const HOST = "com.lucasvecchete.claude_switch";
const BLOCK = 1;
// Never leaves claude.ai blocked, whatever happens to the host.
const RELEASE_AFTER_MS = 20000;
// Answers already on their way when the block starts still set their cookies.
const SETTLE_MS = 1000;

let port = null;
let releaseTimer = null;

function cookieUrl(cookie) {
  return `https://${cookie.domain.replace(/^\./, "")}${cookie.path}`;
}

// A page that sends a half swapped session gets it revoked, so no claude.ai request leaves during a swap.
async function block() {
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: [BLOCK],
    addRules: [{ id: BLOCK, priority: 1, action: { type: "block" }, condition: { requestDomains: ["claude.ai"] } }],
  });
  clearTimeout(releaseTimer);
  releaseTimer = setTimeout(release, RELEASE_AFTER_MS);
}

async function release() {
  clearTimeout(releaseTimer);
  await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [BLOCK] });
}

async function sessionCookies(names) {
  return (await chrome.cookies.getAll({ domain: "claude.ai" })).filter((cookie) => names.includes(cookie.name));
}

// sessionKey leaves first and comes back last, so it is never sent without the rest of its session.
function sessionKeyLast(cookies) {
  return [...cookies].sort((a, b) => (a.name === "sessionKey") - (b.name === "sessionKey"));
}

async function capture({ names }) {
  await block();
  await new Promise((resolve) => setTimeout(resolve, SETTLE_MS));
  return { cookies: await sessionCookies(names) };
}

async function apply({ remove, add }) {
  for (const cookie of sessionKeyLast(remove).reverse()) {
    await chrome.cookies.remove({ url: cookieUrl(cookie), name: cookie.name, storeId: cookie.storeId });
  }
  for (const cookie of sessionKeyLast(add)) {
    const details = {
      url: cookieUrl(cookie),
      name: cookie.name,
      value: cookie.value,
      path: cookie.path,
      secure: cookie.secure,
      httpOnly: cookie.httpOnly,
      sameSite: cookie.sameSite,
    };
    if (!cookie.hostOnly) details.domain = cookie.domain;
    if (!cookie.session) details.expirationDate = cookie.expirationDate;
    await chrome.cookies.set(details);
  }
  const now = await chrome.cookies.getAll({ domain: "claude.ai" });
  const missing = add.filter(
    (cookie) => !now.some((kept) => kept.name === cookie.name && kept.domain === cookie.domain && kept.value === cookie.value),
  );
  await release();
  // The open pages still hold the previous account until they load again.
  const tabs = await chrome.tabs.query({ url: ["https://claude.ai/*", "https://*.claude.ai/*"] });
  await Promise.all(tabs.map((tab) => chrome.tabs.reload(tab.id)));
  if (missing.length) {
    throw new Error(`Chrome did not keep ${missing.map((cookie) => cookie.name).join(", ")}`);
  }
  return { tabs: tabs.length };
}

async function handle(message) {
  switch (message.cmd) {
    case "get":
      return { cookies: await chrome.cookies.getAll({ domain: "claude.ai" }) };
    case "capture":
      return capture(message);
    case "apply":
      return apply(message);
    case "release":
      await release();
      return {};
    default:
      throw new Error(`unknown command ${message.cmd}`);
  }
}

function connect() {
  if (port) return;
  // Each answer goes back on the port its question came from.
  const current = chrome.runtime.connectNative(HOST);
  port = current;
  current.onMessage.addListener(async (message) => {
    try {
      current.postMessage({ id: message.id, ...(await handle(message)) });
    } catch (error) {
      current.postMessage({ id: message.id, error: String(error) });
    }
  });
  current.onDisconnect.addListener(() => {
    if (port === current) port = null;
    release();
    setTimeout(connect, 10000);
  });
}

// Starts the worker with Chrome. The open port to the host keeps it running, and the alarm brings
// both back if Chrome stopped the worker anyway.
chrome.runtime.onStartup.addListener(connect);
chrome.alarms.onAlarm.addListener(connect);
chrome.alarms.create("connect", { periodInMinutes: 1 });
connect();
