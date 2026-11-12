// The clipboard of this computer shared with the box's browser, added by agentbox to noVNC's
// page (noVNC 1.3 has only the clipboard box in its side panel).
//
// Paste: Ctrl+V (Cmd+V on a Mac, Shift+Insert) in the viewer reads this computer's clipboard
// (the browser asks once for permission), hands it to the box, and only then presses the
// shortcut there, so the box's browser pastes that text.
// Copy: text copied on the box (Ctrl+C there) is written to this computer's clipboard, when it
// follows a key or click of yours in the viewer (not when the viewer connects, or an agent
// copies something on its own).
// Without the Clipboard API (Firefox and Safari, which ask on every paste, a denied permission,
// a page that isn't on localhost),
// the viewer works as before: the clipboard box in the side panel, and a one-line hint.
import UI from "./app/ui.js";
import { keySequence, normalize, shortcut } from "./agentbox-clipboard-keys.js";

const mac = /Mac|iPhone|iPad/.test(navigator.platform);
const ua = navigator.userAgent;
const clip = window.isSecureContext ? navigator.clipboard : undefined;
// Firefox and Safari ask with a "Paste" pop-up on every read: they keep noVNC's side panel.
const popupRead = /Firefox\//.test(ua) || (/Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\//.test(ua));
const canRead = !!(clip && clip.readText) && !popupRead;
const canWrite = !!(clip && clip.writeText);
const COPY_WINDOW_MS = 5000;   // a copy on the box this soon after your key or click there is yours
const CONNECT_QUIET_MS = 1500;   // what the box announces as it (re)connects isn't a copy
const ECHO_MS = 3000;   // the box repeating a paste back isn't a copy either
// Pastes go to the box one at a time, this far apart: the box's browser fetches the text from
// this page after the keys, and drops a second paste that arrives while it's still fetching.
const PASTE_GAP_MS = 300;
let pastes = Promise.resolve();
let lastPaste = 0;

let lastInput = 0;   // your last key or click on the viewer's screen
let connectedAt = 0;
let sent = { text: null, at: 0 };   // the last text handed to the box
const hinted = new Set();

function hint(id, text) {
    if (hinted.has(id)) {
        return;
    }
    hinted.add(id);
    const el = document.createElement("div");
    el.textContent = text;
    el.style.cssText = "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:10000;" +
        "max-width:90%;padding:8px 14px;border-radius:6px;background:rgba(20,20,20,.9);color:#fff;" +
        "font:13px/1.4 sans-serif;pointer-events:none";
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 9000);
}
const PANEL = "use the clipboard box in the panel on the left edge.";

// Troubleshooting: with debug=1 in the viewer's address, each paste and copy says what happened
// in a line at the bottom (lengths only, never the text).
const DEBUG = new URLSearchParams(location.search).get("debug") === "1";
let debugEl = null;
function debug(text) {
    if (!DEBUG) {
        return;
    }
    if (!debugEl) {
        debugEl = document.createElement("div");
        debugEl.style.cssText = "position:fixed;left:8px;right:8px;bottom:8px;z-index:10001;padding:6px 10px;" +
            "border-radius:6px;background:rgba(0,60,30,.92);color:#fff;font:12px/1.45 monospace;" +
            "pointer-events:none;white-space:pre-wrap";
        document.body.appendChild(debugEl);
    }
    const line = new Date().toLocaleTimeString() + "  " + text;
    debugEl.textContent = (line + "\n" + debugEl.textContent).split("\n").slice(0, 6).join("\n");
    console.log("agentbox clipboard: " + text);
}

// Keys and clicks on the viewer's screen (noVNC's canvas), not in its side panel.
function onScreen(e) {
    const box = document.getElementById("noVNC_container");
    return !!UI.rfb && e.target instanceof HTMLCanvasElement && !!box && box.contains(e.target);
}

// Modifiers down on the box ({code: keysym}): noVNC's own record (its keyboard's key list in
// noVNC 1.3), else what this page saw go down and up (noVNC lets all keys go on blur, so does this).
const SUPER_L = 0xffeb;
const MODS = {
    ControlLeft: 0xffe3, ControlRight: 0xffe4, ShiftLeft: 0xffe1, ShiftRight: 0xffe2,
    MetaLeft: SUPER_L, MetaRight: SUPER_L, OSLeft: SUPER_L, OSRight: SUPER_L,
};
const mods = {};
function held() {
    const list = UI.rfb && UI.rfb._keyboard && UI.rfb._keyboard._keyDownList;
    return list && typeof list === "object" ? { ...list } : { ...mods };
}

function press(key) {
    if (UI.rfb) {
        for (const [keysym, code, down] of keySequence(key, held())) {
            UI.rfb.sendKey(keysym, code, down);
        }
    }
}

// The shortcut taken over: noVNC's handler on the canvas never sees this keydown. (Its keyup
// needs nothing: noVNC lets go only of keys it sent down.)
function take(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
}

function onKeyDown(e) {
    if (!onScreen(e)) {
        if (shortcut(e, mac)) {
            const where = e.target && e.target.tagName ? e.target.tagName.toLowerCase() + (e.target.id ? "#" + e.target.id : "") : "?";
            debug(e.code + " seen, but focus isn't on the remote screen (it's on " + where + "): click into the remote page first");
        }
        return;
    }
    lastInput = Date.now();
    if (e.code in MODS) {
        mods[e.code] = MODS[e.code];
    }
    const s = shortcut(e, mac);
    if (!s) {
        return;
    }
    debug(s.action + " shortcut (" + e.code + (e.repeat ? ", repeat" : "") + "); clipboard read " + (canRead ? "available" : "not available here"));
    if (s.action === "paste" && !canRead) {
        hint("noread", "Pasting from this computer works in Chrome or Edge; here, " + PANEL);
        if (e.metaKey && mac) {   // Cmd+V: the box (Linux) pastes its own clipboard with Ctrl+V
            take(e);
            if (!e.repeat) {
                press(s.key);
            }
        }
        return;   // otherwise noVNC sends the keys as they are
    }
    take(e);
    if (e.repeat) {
        return;
    }
    if (s.action === "copy") {
        press(s.key);
        return;
    }
    debug("reading this computer's clipboard...");
    const reading = clip.readText().then((text) => {
        text = normalize(text);
        debug("clipboard read: " + text.length + " characters" + (UI.rfb ? "" : ", but not connected to the box"));
        return text;
    }, (err) => {
        debug("the browser refused to read the clipboard: " + (err && err.name) + " " + (err && err.message));
        hint("denied", "This page may not read the clipboard (allow it with the icon in the address bar), or " + PANEL);
        return "";
    });
    pastes = pastes.then(async () => {
        const text = await reading;
        const wait = lastPaste + PASTE_GAP_MS - Date.now();
        if (wait > 0) {
            await new Promise((done) => setTimeout(done, wait));
        }
        if (text && UI.rfb) {
            sent = { text, at: Date.now() };
            UI.rfb.clipboardPasteFrom(text);   // before the keys, on the same connection
            debug("sent " + text.length + " characters to the box");
        }
        press(s.key);
        lastPaste = Date.now();
        debug("pressed " + s.key + " with its modifier on the box");
    });
}

function onKeyUp(e) {
    delete mods[e.code];
    if (onScreen(e)) {
        lastInput = Date.now();
    }
}

function onCopied(e) {
    const text = e.detail.text;
    const now = Date.now();
    if (text === sent.text && now - sent.at < ECHO_MS) {
        sent = { text: null, at: 0 };
        debug("box clipboard: " + (text || "").length + " characters, the echo of a paste (ignored)");
        return;
    }
    if (!canWrite || !text || now - connectedAt < CONNECT_QUIET_MS || now - lastInput > COPY_WINDOW_MS) {
        debug("box clipboard: " + (text || "").length + " characters, not written here (" +
            (!canWrite ? "no clipboard write" : !text ? "empty" : now - connectedAt < CONNECT_QUIET_MS ? "just connected" : "no key or click of yours in the last 5 s") + ")");
        return;
    }
    clip.writeText(text).then(() => debug("box clipboard: " + text.length + " characters, written to this computer"), () => {
        debug("box clipboard: " + text.length + " characters, but this browser refused to write it");
        hint("nowrite", "Text copied on the box is in the clipboard box of the panel on the left edge (this browser didn't let the page write to the clipboard).");
    });
}

window.addEventListener("keydown", onKeyDown, true);
window.addEventListener("keyup", onKeyUp, true);
window.addEventListener("blur", () => { for (const c in mods) { delete mods[c]; } });
window.addEventListener("pointerdown", (e) => { if (onScreen(e)) { lastInput = Date.now(); } }, true);

// noVNC makes a new connection object (UI.rfb) on every (re)connect: listen on each as it's set.
function listen(rfb) {
    if (rfb) {
        rfb.addEventListener("connect", () => { connectedAt = Date.now(); debug("connected to the box"); });
        rfb.addEventListener("disconnect", () => debug("disconnected from the box"));
        rfb.addEventListener("clipboard", onCopied);
    }
}
let current = UI.rfb;
listen(current);
Object.defineProperty(UI, "rfb", {
    configurable: true,
    get: () => current,
    set: (rfb) => { current = rfb; listen(rfb); },
});
