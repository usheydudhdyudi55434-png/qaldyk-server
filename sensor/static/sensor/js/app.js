// The installable Qaldyq app: install prompt, the resident's saved request codes,
// the "My requests" list and web push. Each part only runs where its markup is on the page.

(function () {

    "use strict";

    const CODES_KEY = "qaldyq-codes";
    const MY_CACHE_KEY = "qaldyq-my-cache";
    const DISMISS_KEY = "qaldyq-install-dismissed";
    const MAX_CODES = 30;
    const CODE_RE = /^Q-[A-Z0-9]{6}$/;

    // From Django's JS catalog, loaded before this script
    const gettext = window.gettext || (text => text);
    const interpolate = window.interpolate || ((text, params) => text.replace(/%\((\w+)\)s/g, (all, key) => params[key]));

    // Storage can be missing or blocked (private mode); everything still works without it
    function read(key, fallback) {
        try {
            const value = localStorage.getItem(key);
            return value === null ? fallback : JSON.parse(value);
        } catch (error) {
            return fallback;
        }
    }

    function write(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {}
    }

    function isStandalone() {
        return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
    }

    if (isStandalone()) {
        document.documentElement.classList.add("is-app");
    }

    // ============================================================
    // SAVED CODES
    // ============================================================

    function codes() {
        return read(CODES_KEY, []).filter(code => CODE_RE.test(code));
    }

    function saveCode(code) {
        code = String(code || "").trim().toUpperCase();
        if (!CODE_RE.test(code)) {
            return;
        }
        write(CODES_KEY, [code, ...codes().filter(item => item !== code)].slice(0, MAX_CODES));
    }

    function forgetCode(code) {
        write(CODES_KEY, codes().filter(item => item !== code));
    }

    // A status page the resident opened is remembered on this phone
    document.querySelectorAll("[data-save-code]").forEach(node => saveCode(node.dataset.saveCode));

    // Copy buttons, e.g. for the wallet code
    document.querySelectorAll("[data-copy]").forEach(button => {
        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(button.dataset.copy);
                const label = button.textContent;
                button.textContent = button.dataset.copied || label;
                setTimeout(() => { button.textContent = label; }, 1800);
            } catch (error) {}
        });
    });

    // ============================================================
    // INSTALL
    // ============================================================

    const card = document.querySelector("[data-install-card]");
    let installPrompt = null;

    function dismissedRecently() {
        const at = read(DISMISS_KEY, 0);
        return Date.now() - at < 7 * 24 * 60 * 60 * 1000;
    }

    if (card && !isStandalone() && !dismissedRecently()) {

        const button = card.querySelector("[data-install-button]");
        const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;

        window.addEventListener("beforeinstallprompt", event => {
            event.preventDefault();
            installPrompt = event;
            card.hidden = false;
            button.hidden = false;
        });

        // Safari has no install prompt; the card explains the two taps instead
        if (isIos) {
            card.hidden = false;
            card.querySelector("[data-install-hint]").hidden = true;
            card.querySelector("[data-install-ios]").hidden = false;
        }

        button.addEventListener("click", async () => {
            if (!installPrompt) {
                return;
            }
            installPrompt.prompt();
            await installPrompt.userChoice;
            installPrompt = null;
            card.hidden = true;
        });

        card.querySelector("[data-install-close]").addEventListener("click", () => {
            write(DISMISS_KEY, Date.now());
            card.hidden = true;
        });

        window.addEventListener("appinstalled", () => {
            card.hidden = true;
        });
    }

    // ============================================================
    // MY REQUESTS
    // ============================================================

    const list = document.getElementById("myRequests");

    function el(tag, attrs, ...children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
        children.flat().forEach(child => {
            if (child !== null && child !== undefined && child !== "") {
                node.append(child);
            }
        });
        return node;
    }

    function renderMy(rows, stale) {

        list.replaceChildren();

        if (stale) {
            list.append(el("p", { class: "field-hint my-stale" }, gettext("Байланыс жоқ: соңғы сақталған мәлімет көрсетілген.")));
        }

        const found = rows.filter(row => row.found);

        if (found.length === 0) {
            list.append(document.getElementById("myEmpty").content.cloneNode(true));
            return;
        }

        found.forEach(row => {

            const remove = el("button", {
                type: "button",
                class: "my-remove",
                "aria-label": gettext("Тізімнен алып тастау")
            }, "×");

            remove.addEventListener("click", event => {
                event.preventDefault();
                forgetCode(row.code);
                loadMy();
            });

            list.append(el(
                "a",
                { class: "panel my-card", href: row.url },
                el("span", { class: "my-card-head" },
                    el("span", { class: "request-code" }, row.code),
                    el("span", { class: `status-pill status-${row.status}` }, row.statusLabel),
                    remove
                ),
                el("strong", {}, row.category),
                el("span", { class: "cell-secondary" }, row.address),
                el("span", { class: "field-hint" }, interpolate(gettext("Жаңартылды: %(time)s"), { time: row.updatedAt }, true))
            ));
        });
    }

    async function fetchStatus(list) {
        const response = await fetch(`/my/status/?codes=${encodeURIComponent(list.join(","))}`, {
            headers: { Accept: "application/json" }
        });
        if (!response.ok) {
            throw new Error(String(response.status));
        }
        return (await response.json()).requests;
    }

    async function loadMy() {

        const saved = codes();

        if (saved.length === 0) {
            renderMy([], false);
            return;
        }

        try {
            const rows = await fetchStatus(saved);
            // Codes the server no longer knows are dropped from the phone
            rows.filter(row => !row.found).forEach(row => forgetCode(row.code));
            write(MY_CACHE_KEY, rows);
            renderMy(rows, false);
        } catch (error) {
            renderMy(read(MY_CACHE_KEY, []), true);
        }
    }

    if (list) {

        loadMy();

        const form = document.getElementById("myAdd");
        const input = document.getElementById("myCode");
        const error = document.getElementById("myAddError");

        form.addEventListener("submit", async event => {

            event.preventDefault();

            const code = input.value.trim().toUpperCase();
            error.hidden = true;

            try {
                const rows = CODE_RE.test(code) ? await fetchStatus([code]) : [];
                if (rows.length && rows[0].found) {
                    saveCode(code);
                    input.value = "";
                    loadMy();
                    return;
                }
            } catch (failure) {}

            error.hidden = false;
        });
    }

    // ============================================================
    // WEB PUSH
    // ============================================================

    function keyBytes(base64) {
        const padded = (base64 + "=".repeat((4 - base64.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/");
        return Uint8Array.from(atob(padded), char => char.charCodeAt(0));
    }

    document.querySelectorAll("[data-push-card]").forEach(box => {

        const button = box.querySelector("[data-push-code]");
        const note = box.querySelector("[data-push-note]");
        const code = button.dataset.pushCode;
        const doneKey = `qaldyq-push-${code}`;

        if (!("serviceWorker" in navigator) || !("PushManager" in window) || !button.dataset.pushKey) {
            return;
        }

        box.hidden = false;

        function markOn() {
            button.disabled = true;
            button.textContent = gettext("Хабарлама қосулы");
        }

        if (read(doneKey, false) && Notification.permission === "granted") {
            markOn();
        }

        button.addEventListener("click", async () => {

            note.hidden = true;
            button.disabled = true;
            button.textContent = gettext("Қосылуда…");

            try {
                const permission = await Notification.requestPermission();

                if (permission !== "granted") {
                    throw new Error("denied");
                }

                const registration = await navigator.serviceWorker.ready;
                const subscription = await registration.pushManager.getSubscription()
                    || await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: keyBytes(button.dataset.pushKey)
                    });

                const response = await fetch("/push/subscribe/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": box.querySelector("[name=csrfmiddlewaretoken]").value
                    },
                    body: JSON.stringify({ subscription: subscription.toJSON(), code })
                });

                if (!response.ok) {
                    throw new Error(String(response.status));
                }

                write(doneKey, true);
                saveCode(code);
                markOn();

            } catch (failure) {
                button.disabled = false;
                button.textContent = gettext("Хабарламаны қосу");
                note.textContent = failure.message === "denied"
                    ? gettext("Хабарламаға рұқсат берілмеді. Оны браузер баптауларынан қосыңыз.")
                    : gettext("Хабарламаны қосу мүмкін болмады. Кейінірек қайталаңыз.");
                note.hidden = false;
            }
        });
    });

})();
