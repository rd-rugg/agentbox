// What agentbox-clipboard.js decides, kept apart from noVNC so it can be tested on its own:
// which shortcuts it takes over, and the key events that then go to the box.

// X keysyms (as in noVNC's core/input/keysym.js).
export const XK = {
    Control_L: 0xffe3, Shift_L: 0xffe1, Insert: 0xff63, c: 0x0063, v: 0x0076, x: 0x0078,
};

// Text for the box: Windows (and old Mac) line ends become \n.
export function normalize(text) {
    return text.replace(/\r\n?/g, "\n");
}

// The shortcut a keydown is, or null: "paste" for Ctrl+V (also with Shift), Cmd+V on a Mac
// and Shift+Insert; "copy" for Cmd+C and Cmd+X on a Mac, which the box (Linux) only knows as
// Ctrl+C and Ctrl+X. KEY is what to press on the box.
export function shortcut(e, mac) {
    const cmd = mac && e.metaKey && !e.ctrlKey;
    if (e.altKey) {
        return null;
    }
    if (e.code === "KeyV" && (e.ctrlKey || cmd)) {
        return { action: "paste", key: "v" };
    }
    if (e.code === "Insert" && e.shiftKey && !e.ctrlKey && !e.metaKey) {
        return { action: "paste", key: "Insert" };
    }
    if (cmd && (e.code === "KeyC" || e.code === "KeyX")) {
        return { action: "copy", key: e.code === "KeyC" ? "c" : "x" };
    }
    return null;
}

// The key events [keysym, code, down] that press KEY with its modifier on the box: Ctrl, or
// Shift for Insert. HELD is what noVNC has down there ({code: keysym}): the modifier is pressed
// and let go around the key only if it isn't down already (the user may have let go of it
// while the browser asked to read the clipboard), and a Mac's Command key is let go for the
// shortcut and put back after, so the box sees Ctrl+V, not Super+Ctrl+V.
export function keySequence(key, held) {
    const seq = [];
    const meta = Object.keys(held).filter(c => c.startsWith("Meta") || c.startsWith("OS"));
    const shift = key === "Insert";
    const mods = shift ? ["ShiftLeft", "ShiftRight"] : ["ControlLeft", "ControlRight"];
    const mod = [shift ? XK.Shift_L : XK.Control_L, mods[0]];
    const press = !mods.some(c => c in held);
    const k = shift ? [XK.Insert, "Insert"] : [XK[key], "Key" + key.toUpperCase()];
    for (const c of meta) {
        seq.push([held[c], c, false]);
    }
    if (press) {
        seq.push([...mod, true]);
    }
    seq.push([...k, true], [...k, false]);
    if (press) {
        seq.push([...mod, false]);
    }
    for (const c of meta) {
        seq.push([held[c], c, true]);
    }
    return seq;
}
