// The app's QR scanner. It reads Qaldyq codes only: a container sticker (/c/<id>/)
// and a cleanup day (/e/<token>/), and opens them on this site whatever host was printed.

(function () {

    "use strict";

    // Fallback decoder for browsers without BarcodeDetector (Safari, Firefox)
    const JSQR_URL = "https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js";

    const QALDYQ_PATH = /^\/(c\/\d+|e\/[A-Za-z0-9_-]+)\/?$/;

    const video = document.getElementById("scanVideo");
    const idle = document.getElementById("scanIdle");
    const startButton = document.getElementById("scanStart");
    const status = document.getElementById("scanStatus");
    const fileInput = document.getElementById("scanFile");
    const view = document.getElementById("scanView");

    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });

    let detector = null;
    let stream = null;
    let busy = false;
    let pausedUntil = 0;

    if ("BarcodeDetector" in window) {
        try {
            detector = new BarcodeDetector({ formats: ["qr_code"] });
        } catch (error) {
            detector = null;
        }
    }

    function say(text, kind) {
        status.textContent = text;
        status.className = "scan-status" + (kind ? ` is-${kind}` : "");
    }

    function loadJsQr() {
        if (window.jsQR) {
            return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = JSQR_URL;
            script.onload = resolve;
            script.onerror = reject;
            document.head.append(script);
        });
    }

    // Where a scanned text leads on this site, or null when it is not a Qaldyq code
    function target(text) {
        try {
            const url = new URL(String(text).trim(), window.location.origin);
            const path = url.pathname.endsWith("/") ? url.pathname : url.pathname + "/";
            return QALDYQ_PATH.test(path) ? path : null;
        } catch (error) {
            return null;
        }
    }

    function stop() {
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
            stream = null;
        }
    }

    function found(text) {

        const path = target(text);

        if (!path) {
            say(gettext("Бұл Qaldyq-тың QR-коды емес."), "error");
            pausedUntil = Date.now() + 2000;
            return false;
        }

        stop();
        view.classList.add("is-found");
        if (navigator.vibrate) {
            navigator.vibrate(60);
        }
        say(gettext("QR табылды, ашылуда…"), "ok");
        window.location.href = path;
        return true;
    }

    async function decode(source, width, height) {

        if (detector) {
            const codes = await detector.detect(source);
            return codes.length ? codes[0].rawValue : null;
        }

        // Smaller frames decode faster and are enough for a sticker held close
        const scale = Math.min(1, 640 / Math.max(width, height));
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        context.drawImage(source, 0, 0, canvas.width, canvas.height);
        const image = context.getImageData(0, 0, canvas.width, canvas.height);
        const code = window.jsQR(image.data, image.width, image.height, { inversionAttempts: "attemptBoth" });
        return code ? code.data : null;
    }

    async function tick() {

        if (!stream) {
            return;
        }

        if (!busy && Date.now() > pausedUntil && video.readyState >= 2) {
            busy = true;
            try {
                const text = await decode(video, video.videoWidth, video.videoHeight);
                if (text && found(text)) {
                    return;
                }
            } catch (error) {}
            busy = false;
        }

        setTimeout(tick, 180);
    }

    async function start() {

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            say(gettext("Бұл браузер камераны аша алмайды. «Фотодан оқу» батырмасын пайдаланыңыз."), "error");
            return;
        }

        say(gettext("Камера қосылуда…"));

        try {
            if (!detector) {
                await loadJsQr();
            }
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: "environment" } },
                audio: false
            });
        } catch (error) {
            say(
                error && error.name === "NotAllowedError"
                    ? gettext("Камераға рұқсат берілмеді. Оны браузер баптауларынан қосыңыз немесе «Фотодан оқу» батырмасын басыңыз.")
                    : gettext("Камераны ашу мүмкін болмады. «Фотодан оқу» батырмасын пайдаланыңыз."),
                "error"
            );
            return;
        }

        video.srcObject = stream;
        await video.play().catch(() => {});
        idle.hidden = true;
        view.classList.add("is-live");
        say(gettext("QR-ды рамкаға түсіріңіз"));
        tick();
    }

    startButton.addEventListener("click", start);

    // A photo of the code works where the live camera does not
    fileInput.addEventListener("change", async () => {

        const file = fileInput.files[0];

        if (!file) {
            return;
        }

        say(gettext("Фото оқылуда…"));

        try {
            if (!detector) {
                await loadJsQr();
            }
            const bitmap = await createImageBitmap(file);
            const text = await decode(bitmap, bitmap.width, bitmap.height);
            if (!text) {
                say(gettext("Фотодан QR табылмады. Кодты жақынырақ, анық түсіріңіз."), "error");
            } else {
                found(text);
            }
        } catch (error) {
            say(gettext("Фотодан QR табылмады. Кодты жақынырақ, анық түсіріңіз."), "error");
        }

        fileInput.value = "";
    });

    // The camera is released when the resident leaves the page
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            stop();
            idle.hidden = false;
            view.classList.remove("is-live");
        }
    });
    window.addEventListener("pagehide", stop);

    // Start at once where the browser allows it without a tap
    if (navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: "camera" })
            .then(result => { if (result.state === "granted") start(); })
            .catch(() => {});
    }

})();
