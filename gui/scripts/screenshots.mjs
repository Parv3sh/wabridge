#!/usr/bin/env node
/**
 * Render every screen of the desktop app in headless Chromium against the browser mock engine
 * (src/dev/mockEngine.ts) and save PNGs to gui/screenshots/. No phones, no Tauri window needed.
 *
 *   npx playwright install chromium        (once; Playwright itself is a devDependency)
 *   npm run screenshots                    → gui/screenshots/NN-scene.png
 *
 * Uses the dev server on :1420 if one is running (`bash build-app.sh dev` or `npm run dev`),
 * otherwise starts its own on :1427 for the duration.
 */
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const gui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = path.join(gui, "screenshots");
mkdirSync(outDir, { recursive: true });

const KEY = "0123456789abcdef".repeat(4);
const VIEWPORT = { width: 980, height: 660 };

// ---- scenes ------------------------------------------------------------------------------------
// [name, mock flags (+ optional &speed=), async steps(page)]. Each scene is a fresh page load.

const begin = async (page) => page.getByRole("button", { name: "Begin" }).click();
const resume = async (page) => page.getByRole("button", { name: "Continue where I left off" }).click();
const decrypt = async (page) => {
  await page.getByText("ready, encrypted backup found").waitFor();
  await page.getByLabel("64-digit key").fill(KEY);
  await page.getByRole("button", { name: "Decrypt backup" }).click();
};
const fetched = async (page) => {
  await begin(page);
  await decrypt(page);
  await page.getByRole("button", { name: "Skip media for now" }).waitFor();
};
const toTransfer = async (page) => {
  await resume(page);
  await page.getByRole("heading", { name: "Move the chats across" }).waitFor();
};
const convertAndRestore = async (page) => {
  await toTransfer(page);
  await page.getByLabel("Find My iPhone is off").check();
  await page.getByRole("button", { name: "Move the chats to the iPhone" }).click();
};
const verify = async (page) => {
  await convertAndRestore(page);
  await page.getByText("Restore finished").waitFor({ timeout: 30_000 });
};
const progressShowing = async (page) => page.getByRole("progressbar").waitFor();

const SCENES = [
  ["01-boot-crash", "crash", async (page) => page.getByText("couldn’t start its engine").waitFor()],
  ["02-start", "", async () => {}],
  ["03-start-no-adb", "no-adb", async (page) => page.getByText("not found").waitFor()],
  ["04-start-low-disk", "low-disk", async () => {}],
  ["05-start-resume", "resume-android", async (page) => page.getByText("partway through").waitFor()],
  ["06-android-waiting", "", async (page) => (await begin(page), page.getByText("Waiting for the Android phone").waitFor())],
  ["07-android-unauthorized", "android-unauthorized", async (page) => (await begin(page), page.getByText("Waiting for permission").waitFor())],
  ["08-android-no-whatsapp", "android-no-whatsapp", async (page) => (await begin(page), page.getByText("isn’t set up").waitFor())],
  ["09-android-no-backup", "android-no-backup", async (page) => (await begin(page), page.getByText("Turn on end-to-end").waitFor())],
  ["10-android-key", "android", async (page) => (await begin(page), page.getByText("ready, encrypted backup found").waitFor())],
  [
    "11-android-key-partial",
    "android",
    async (page) => {
      await begin(page);
      await page.getByText("ready, encrypted backup found").waitFor();
      await page.getByLabel("64-digit key").fill("1234 5678 9012");
      await page.getByLabel("64-digit key").blur();
      await page.getByText("of 64 characters").waitFor();
    },
  ],
  [
    "11b-android-key-howto",
    "android",
    async (page) => {
      await begin(page);
      await page.getByText("ready, encrypted backup found").waitFor();
      await page.getByText("How to get it").click();
      await page.getByText("Generate your 64-digit key").waitFor();
    },
  ],
  ["12-android-wrong-key", "wrong-key", async (page) => (await begin(page), await decrypt(page), page.getByText("doesn’t open").waitFor())],
  ["13-android-decrypting", "android&speed=200", async (page) => (await begin(page), await decrypt(page), progressShowing(page))],
  ["14-android-fetched", "android", fetched],
  [
    "15-android-media-progress",
    "android&speed=150",
    async (page) => {
      await fetched(page);
      await page.getByRole("button", { name: "Copy media" }).click();
      await page.getByText("%").waitFor();
    },
  ],
  [
    "16-android-media-done",
    "android&speed=0",
    async (page) => {
      await fetched(page);
      await page.getByRole("button", { name: "Copy media" }).click();
      await page.getByText("Media copied").waitFor();
    },
  ],
  ["17-iphone-waiting", "resume-android", async (page) => (await resume(page), page.getByText("Waiting for the iPhone").waitFor())],
  ["18-iphone-encrypted", "resume-android,iphone-encrypted", async (page) => (await resume(page), page.getByText("Turn off backup encryption first").waitFor())],
  ["19-iphone-ready", "resume-android,iphone", async (page) => (await resume(page), page.getByText("in use, ready").waitFor())],
  ["20-iphone-big", "resume-android,iphone-big", async (page) => (await resume(page), page.getByText("Not enough space").waitFor())],
  [
    "21-iphone-backup-progress",
    "resume-android,iphone&speed=150",
    async (page) => {
      await resume(page);
      await page.getByRole("button", { name: "Back up iPhone" }).click();
      await page.getByText("%").waitFor();
    },
  ],
  [
    "22-iphone-backed-up",
    "resume-android,iphone&speed=0",
    async (page) => {
      await resume(page);
      await page.getByRole("button", { name: "Back up iPhone" }).click();
      await page.getByText("iPhone backed up").waitFor();
    },
  ],
  ["23-transfer-options", "resume-iphone,iphone", toTransfer],
  ["24-transfer-running", "resume-iphone,iphone&speed=200", async (page) => (await convertAndRestore(page), page.getByText("%").waitFor())],
  ["25-transfer-verify", "resume-iphone,iphone&speed=0", verify],
  ["26-transfer-find-my", "resume-iphone,iphone,find-my&speed=0", async (page) => (await convertAndRestore(page), page.getByText("Find My is off now").waitFor())],
  [
    "27-transfer-not-visible",
    "resume-iphone,iphone&speed=0",
    async (page) => {
      await verify(page);
      await page.getByRole("button", { name: /No, they/ }).click();
      await page.getByText("doesn’t show the chats").waitFor();
    },
  ],
  ["28-done", "resume-iphone,iphone&speed=0", async (page) => (await verify(page), await page.getByRole("button", { name: /Yes, I can see/ }).click(), page.getByText("Your chats are on the iPhone").waitFor())],
  [
    "29-done-delete-confirm",
    "resume-iphone,iphone&speed=0",
    async (page) => {
      await verify(page);
      await page.getByRole("button", { name: /Yes, I can see/ }).click();
      await page.getByRole("button", { name: "Delete my data" }).click();
      await page.getByText("Delete WaBridge’s copies").waitFor();
    },
  ],
  ["30-console-open", "android", async (page) => (await begin(page), await page.getByText("ready, encrypted backup found").waitFor(), page.getByRole("button", { name: "Console" }).click())],
  ["31-start-dark", "", async () => {}, { colorScheme: "dark" }],
  ["32-android-key-dark", "android", async (page) => (await begin(page), page.getByText("ready, encrypted backup found").waitFor()), { colorScheme: "dark" }],
  ["33-transfer-dark", "resume-iphone,iphone", toTransfer, { colorScheme: "dark" }],
  ["34-start-min-window", "", async () => {}, { viewport: { width: 880, height: 600 } }],
  ["35-android-fetched-min-window", "android", fetched, { viewport: { width: 880, height: 600 } }],
];

// ---- dev server -------------------------------------------------------------------------------

async function up(url) {
  try {
    return (await fetch(url)).ok;
  } catch {
    return false;
  }
}

let base = `http://localhost:${process.env.PORT || 1420}/`;
let devServer = null;
if (!(await up(base))) {
  base = "http://localhost:1427/";
  devServer = spawn("npx", ["vite", "--port", "1427", "--strictPort"], { cwd: gui, stdio: "ignore" });
  for (let i = 0; i < 100 && !(await up(base)); i++) await new Promise((r) => setTimeout(r, 200));
  if (!(await up(base))) {
    console.error("could not start the Vite dev server");
    devServer.kill();
    process.exit(1);
  }
}

// ---- run --------------------------------------------------------------------------------------

const only = process.argv.slice(2);
const browser = await chromium.launch();
let failures = 0;
for (const [name, flags, steps, opts = {}] of SCENES) {
  if (only.length && !only.some((o) => name.includes(o))) continue;
  const context = await browser.newContext({ viewport: opts.viewport ?? VIEWPORT, deviceScaleFactor: 2, colorScheme: opts.colorScheme ?? "light" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto(`${base}?mock=${flags}`, { waitUntil: "networkidle" });
    await Promise.race([page.getByRole("heading", { level: 1 }).first().waitFor(), page.waitForTimeout(5000)]);
    await steps(page);
    await page.waitForTimeout(250); // let transitions settle
    // Playwright scrolls fields into view while driving; shoot what a user sees first (top), then
    // the whole screen if it is taller than the window, so long screens can be judged in full.
    const overflow = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (main) main.scrollTop = 0;
      window.scrollTo(0, 0);
      return main ? main.scrollHeight > main.clientHeight + 4 : document.body.scrollHeight > window.innerHeight + 4;
    });
    await page.screenshot({ path: path.join(outDir, `${name}.png`) });
    if (overflow) {
      await page.addStyleTag({ content: "main{overflow:visible!important;height:auto!important;max-height:none!important}.shell{height:auto!important;min-height:100vh}" });
      await page.screenshot({ path: path.join(outDir, `${name}.full.png`), fullPage: true });
    }
    console.log(`✓ ${name}${overflow ? " (+full)" : ""}${errors.length ? `  (page errors: ${errors.join(" | ")})` : ""}`);
  } catch (e) {
    failures++;
    await page.screenshot({ path: path.join(outDir, `${name}.FAILED.png`) }).catch(() => {});
    console.log(`✗ ${name}: ${e.message.split("\n")[0]}`);
  } finally {
    await context.close();
  }
}
await browser.close();
devServer?.kill();
console.log(`${SCENES.length - failures}/${SCENES.length} scenes → ${outDir}`);
process.exit(failures ? 1 : 0);
