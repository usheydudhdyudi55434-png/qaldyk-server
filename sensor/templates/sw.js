// Qaldyq service worker: keeps the resident pages usable on a weak connection,
// shows an offline page instead of the browser's error, and receives web push.

const VERSION = "qaldyq-{{ version }}";
const PAGES = `pages-${VERSION}`;
const ASSETS = `assets-${VERSION}`;
const OFFLINE_URL = "{{ offline_url }}";

// Public pages a resident opens; staff pages are never stored on the phone
const PUBLIC_PAGES = ["/", "/report/", "/my/", "/eco/", "/otinish/", "/wallet/", "/scan/"];

self.addEventListener("install", event => {
    event.waitUntil(
        caches.open(PAGES)
            .then(cache => cache.addAll([OFFLINE_URL, "/", "/my/"]))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(key => key !== PAGES && key !== ASSETS).map(key => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

function isPublicPage(url) {
    return url.origin === self.location.origin && PUBLIC_PAGES.includes(url.pathname);
}

self.addEventListener("fetch", event => {

    const request = event.request;

    if (request.method !== "GET") {
        return;
    }

    const url = new URL(request.url);

    // Pages: the network first, the saved copy when offline, then the offline page
    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request)
                .then(response => {
                    if (response.ok && isPublicPage(url)) {
                        const copy = response.clone();
                        caches.open(PAGES).then(cache => cache.put(request, copy));
                    }
                    return response;
                })
                .catch(() =>
                    caches.match(request, { ignoreSearch: url.pathname !== "/otinish/" })
                        .then(saved => saved || caches.match(OFFLINE_URL))
                )
        );
        return;
    }

    // Styles, scripts and images: the saved copy at once, refreshed in the background
    if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
        event.respondWith(
            caches.open(ASSETS).then(cache =>
                cache.match(request).then(saved => {
                    const fresh = fetch(request)
                        .then(response => {
                            if (response.ok) {
                                cache.put(request, response.clone());
                            }
                            return response;
                        })
                        .catch(() => saved);
                    return saved || fresh;
                })
            )
        );
    }

});

// A request moved on: show it like any app notification
self.addEventListener("push", event => {

    let data = {};

    try {
        data = event.data ? event.data.json() : {};
    } catch (error) {
        data = { title: "Qaldyq", body: event.data ? event.data.text() : "" };
    }

    event.waitUntil(
        self.registration.showNotification(data.title || "Qaldyq", {
            body: data.body || "",
            icon: "{{ icon }}",
            badge: "{{ badge }}",
            tag: data.tag || "qaldyq",
            renotify: true,
            data: { url: data.url || "/my/" }
        })
    );

});

// Tapping it opens the request, reusing an open window when there is one
self.addEventListener("notificationclick", event => {

    event.notification.close();

    const target = new URL(event.notification.data.url, self.location.origin).href;

    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(windows => {
            for (const client of windows) {
                if ("focus" in client) {
                    client.navigate(target);
                    return client.focus();
                }
            }
            return self.clients.openWindow(target);
        })
    );

});
