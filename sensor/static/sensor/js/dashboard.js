const CONTAINERS_URL = "/api/containers/";

const HISTORY_URL = "/api/containers/history/";

const LOGIN_URL = "/login/";

const DISTRICTS_URL = "/api/districts/";

const REFRESH_INTERVAL = 5000;

const HISTORY_REFRESH_INTERVAL = 60000;

// A sensor is considered offline when it has been silent for this long
const OFFLINE_AFTER = 5 * 60 * 1000;

// Same thresholds as the sensor_data view
const FULL_LEVEL = 80;

const HALF_LEVEL = 50;

// Almaty center
const ALMATY_CENTER = [43.238949, 76.889709];

const SERIES_SLOTS = 8;

const LEVELS = {
    full: { label: "Толы", icon: "i-full" },
    half: { label: "Жартылай толы", icon: "i-half" },
    normal: { label: "Қалыпты", icon: "i-normal" },
    none: { label: "Дерек жоқ", icon: "i-none" }
};

const THRESHOLDS = [
    { value: FULL_LEVEL, color: "var(--status-full)", name: "Толы шегі" },
    { value: HALF_LEVEL, color: "var(--status-half)", name: "Жартылай шегі" }
];

// Fire risk levels decided by the server (sensor/fire.py)
const FIRE_RISKS = {
    DANGER: { label: "Қауіпті", rank: 3 },
    WARNING: { label: "Ескерту", rank: 2 },
    NORMAL: { label: "Қалыпты", rank: 1 },
    "": { label: "Датчик жоқ", rank: 0 }
};

const PAGE_TITLE = document.title;

const SVG_NS = "http://www.w3.org/2000/svg";

// ------------------------------------------------------------
// GLOBAL VARIABLES
// ------------------------------------------------------------

let map = null;

let mapFitted = false;

const markers = {};

const state = {
    containers: [],
    clockOffset: 0,
    selectedId: null,
    filter: "all",
    query: "",
    sort: { key: "fill", direction: "desc" },
    hours: 24,
    metric: "fill",
    history: null,
    historyRequest: 0,
    fireThresholds: null,
    fireSignature: null
};


// ------------------------------------------------------------
// DOM READY
// ------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {

    initThemeToggle();

    initMap();

    initTableControls();

    initRangeFilter();

    initChartResize();

    refreshContainers();

    loadHistory();

    setInterval(() => {
        if (!document.hidden) {
            loadHistory();
        }
    }, HISTORY_REFRESH_INTERVAL);

});


// ============================================================
// HELPERS
// ============================================================

function el(tag, attributes = {}, ...children) {

    const node = document.createElement(tag);

    for (const [name, value] of Object.entries(attributes)) {
        if (name === "class") {
            node.className = value;
        } else if (value !== null && value !== undefined) {
            node.setAttribute(name, value);
        }
    }

    node.append(...children);

    return node;

}


function svgEl(tag, attributes = {}) {

    const node = document.createElementNS(SVG_NS, tag);

    for (const [name, value] of Object.entries(attributes)) {
        node.setAttribute(name, value);
    }

    return node;

}


function icon(name) {

    const svg = svgEl("svg", { class: "icon", "aria-hidden": "true" });

    svg.append(svgEl("use", { href: `#${name}` }));

    return svg;

}


function setText(elementId, value) {

    document.getElementById(elementId).textContent = value;

}


function pad(value) {

    return String(value).padStart(2, "0");

}


function formatClock(date) {

    return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;

}


function formatShortTime(date) {

    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;

}


function formatDay(date) {

    return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`;

}


function formatDateTime(date) {

    return `${formatDay(date)}.${date.getFullYear()} ${formatClock(date)}`;

}


function formatFill(value) {

    return value === null ? "–" : `${value.toFixed(1)}%`;

}


function formatDistance(value) {

    return value === null ? "–" : `${value.toFixed(1)} см`;

}


function formatTemperature(value) {

    return value === null ? "–" : `${value.toFixed(1)}°C`;

}


function gasPercent(value) {

    const max = state.fireThresholds ? state.fireThresholds.gas_max : 4095;

    return value / max * 100;

}


function formatGas(value) {

    return value === null ? "–" : `${Math.round(gasPercent(value))}%`;

}


function numberOrNull(value) {

    return value === null || value === undefined ? null : Number(value);

}


function formatAge(age) {

    const seconds = Math.max(Math.floor(age / 1000), 0);

    if (seconds < 60) {
        return `${seconds} сек бұрын`;
    }

    if (seconds < 3600) {
        return `${Math.floor(seconds / 60)} мин бұрын`;
    }

    if (seconds < 86400) {
        return `${Math.floor(seconds / 3600)} сағ бұрын`;
    }

    return `${Math.floor(seconds / 86400)} күн бұрын`;

}


function serverNow() {

    return Date.now() + state.clockOffset;

}


// Re-rendering a list must not drop keyboard focus from its current item
function renderKeepingFocus(parent, render) {

    const focused = document.activeElement;

    const focusedId =
        parent.contains(focused) ? focused.dataset.focusId : null;

    render();

    if (focusedId) {
        const next = parent.querySelector(
            `[data-focus-id="${CSS.escape(focusedId)}"]`
        );

        if (next) {
            next.focus();
        }
    }

}


// ============================================================
// THEME
// ============================================================

function isLightTheme() {

    return document.documentElement.dataset.theme === "light";

}


function updateThemeIcon() {

    document
        .getElementById("themeIcon")
        .setAttribute("href", isLightTheme() ? "#i-moon" : "#i-sun");

}


function initThemeToggle() {

    updateThemeIcon();

    document
        .getElementById("themeToggle")
        .addEventListener("click", () => {

            const theme = isLightTheme() ? "dark" : "light";

            document.documentElement.dataset.theme = theme;

            try {
                localStorage.setItem("qaldyk-theme", theme);
            } catch (error) {
                console.warn("Theme was not saved:", error);
            }

            updateThemeIcon();

        });

}


// ============================================================
// DATA
// ============================================================

async function fetchJson(url) {

    const response = await fetch(url);

    // The session has ended, so the data can only be reached after a new login
    if (response.status === 401 || response.status === 403) {
        window.location.assign(
            `${LOGIN_URL}?next=${encodeURIComponent(window.location.pathname)}`
        );
    }

    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }

    return response.json();

}


async function refreshContainers() {

    if (!document.hidden) {

        try {

            const data = await fetchJson(CONTAINERS_URL);

            state.clockOffset =
                new Date(data.server_time).getTime() - Date.now();

            state.fireThresholds = data.fire_thresholds || null;

            const isFirstLoad = state.containers.length === 0;

            state.containers = data.containers.map(describeContainer);

            setConnection(true);

            setText("lastUpdated", formatClock(new Date()));

            renderAll();

            // The history request may have finished before the containers
            if (isFirstLoad) {
                renderChart();
                renderChartTable();
            }

        } catch (error) {

            console.error("Containers error:", error);

            setConnection(false);

        }

    }

    setTimeout(refreshContainers, REFRESH_INTERVAL);

}


async function loadHistory() {

    const request = ++state.historyRequest;

    const chart = document.getElementById("trendChart");

    chart.classList.add("is-loading");

    try {

        const data = await fetchJson(
            `${HISTORY_URL}?hours=${state.hours}&metric=${state.metric}`
        );

        // A newer range selection already replaced this request
        if (request !== state.historyRequest) {
            return;
        }

        state.history = data;

        renderChart();

        renderChartTable();

    } catch (error) {

        console.error("History error:", error);

    } finally {

        if (request === state.historyRequest) {
            chart.classList.remove("is-loading");
        }

    }

}


function describeContainer(container, index) {

    const base = {
        ...container,
        slot: index < SERIES_SLOTS ? index + 1 : null
    };

    if (!container.latest) {
        return {
            ...base,
            fill: null,
            distance: null,
            level: "none",
            age: null,
            lastAt: null,
            offline: true,
            temperature: null,
            gas: null,
            fireRisk: "",
            fireRank: 0,
            fireNote: ""
        };
    }

    const fireRisk = FIRE_RISKS[container.latest.fire_risk]
        ? container.latest.fire_risk
        : "";

    const fill = Math.min(
        Math.max(Number(container.latest.fill_level) || 0, 0),
        100
    );

    const lastAt = new Date(container.latest.created_at);

    const age = serverNow() - lastAt.getTime();

    return {
        ...base,
        fill,
        distance: Number(container.latest.distance) || 0,
        level: getLevel(fill),
        age,
        lastAt,
        offline: age > OFFLINE_AFTER,
        temperature: numberOrNull(container.latest.temperature),
        gas: numberOrNull(container.latest.gas_level),
        fireRisk,
        fireRank: FIRE_RISKS[fireRisk].rank,
        fireNote: container.latest.fire_note || ""
    };

}


// WARNING and DANGER need a person to look; stale ones too, since a fire can kill the sensor
function isFireAlert(item) {

    return item.fireRank >= FIRE_RISKS.WARNING.rank;

}


function getLevel(fill) {

    if (fill >= FULL_LEVEL) {
        return "full";
    }

    if (fill >= HALF_LEVEL) {
        return "half";
    }

    return "normal";

}


function setConnection(isOnline) {

    const connection = document.getElementById("connection");

    connection.classList.toggle("is-online", isOnline);

    connection.classList.toggle("is-error", !isOnline);

    setText(
        "connectionText",
        isOnline ? "Сервер қосулы" : "Сервермен байланыс жоқ"
    );

}


function renderAll() {

    renderFireAlert();

    renderKpis();

    renderQueue();

    renderTable();

    renderMap();

    renderChartLegend();

}


// ============================================================
// FIRE ALERT
// ============================================================

function renderFireAlert() {

    const alerts = state.containers
        .filter(isFireAlert)
        .sort((a, b) => b.fireRank - a.fireRank);

    const hasDanger = alerts.some(item => item.fireRisk === "DANGER");

    document.title = alerts.length === 0
        ? PAGE_TITLE
        : `${hasDanger ? "Өрт қаупі" : "Ескерту"} (${alerts.length}) · ${PAGE_TITLE}`;

    const signature = alerts
        .map(item => `${item.id}:${item.fireRisk}:${item.fireNote}:${item.offline}:${item.id === state.selectedId}`)
        .join("|");

    // The banner is a live region, so it is only rebuilt when something changed
    if (signature === state.fireSignature) {
        return;
    }

    state.fireSignature = signature;

    const banner = document.getElementById("fireAlert");

    banner.hidden = alerts.length === 0;

    banner.classList.toggle("is-danger", hasDanger);

    setText(
        "fireAlertTitle",
        hasDanger
            ? "Өрт қаупінің белгілері анықталды"
            : "Өрт қаупі: ескерту белгілері"
    );

    const list = document.getElementById("fireAlertList");

    renderKeepingFocus(list, () => {

        list.replaceChildren(...alerts.map(item => {

            const details = [item.location, item.fireNote];

            if (item.offline) {
                details.push("датчик үнсіз, деректер ескі болуы мүмкін");
            }

            const button = el(
                "button",
                {
                    type: "button",
                    class:
                        `fire-alert-item fire-${item.fireRisk}`
                        + (item.id === state.selectedId ? " is-selected" : ""),
                    "data-focus-id": `fire-${item.id}`
                },
                icon("i-flame"),
                el(
                    "span",
                    { class: "fire-alert-text" },
                    el(
                        "strong",
                        {},
                        `${item.name} · ${FIRE_RISKS[item.fireRisk].label}`
                    ),
                    el("span", {}, details.filter(Boolean).join(" · "))
                )
            );

            button.addEventListener("click", () => selectContainer(item.id));

            return button;

        }));

    });

}


// ============================================================
// KPI
// ============================================================

function renderKpis() {

    const containers = state.containers;

    const count = level =>
        containers.filter(item => item.level === level).length;

    const withData = containers.filter(item => item.fill !== null);

    setText("kpiTotal", containers.length);

    setText("kpiFull", count("full"));

    setText("kpiHalf", count("half"));

    setText("kpiNormal", count("normal"));

    setText("kpiOffline", containers.filter(item => item.offline).length);

    setText("kpiFire", containers.filter(isFireAlert).length);

    document
        .getElementById("kpiFireTile")
        .classList.toggle(
            "has-alert",
            containers.some(isFireAlert)
        );

    setText(
        "kpiAverage",
        withData.length === 0
            ? "–"
            : `${Math.round(
                withData.reduce((sum, item) => sum + item.fill, 0)
                / withData.length
            )}%`
    );

    const share = document.getElementById("kpiShare");

    const parts = ["normal", "half", "full", "none"]
        .map(level => ({ level, amount: count(level) }))
        .filter(part => part.amount > 0);

    share.replaceChildren(...parts.map(part => {
        const name = `${LEVELS[part.level].label}: ${part.amount}`;
        const segment = el("span", { class: `level-${part.level}`, title: name });
        segment.style.flexGrow = part.amount;
        return segment;
    }));

    share.setAttribute(
        "aria-label",
        parts
            .map(part => `${LEVELS[part.level].label}: ${part.amount}`)
            .join(", ") || "Контейнер жоқ"
    );

}


// ============================================================
// ATTENTION QUEUE
// ============================================================

function renderQueue() {

    const list = document.getElementById("queueList");

    const items = state.containers
        .filter(item => isFireAlert(item) || item.level === "full" || item.offline)
        .sort((a, b) =>
            queuePriority(a) - queuePriority(b)
            || (b.fill ?? -1) - (a.fill ?? -1)
        );

    setText("queueCount", items.length);

    renderKeepingFocus(list, () => {

        if (items.length === 0) {

            list.replaceChildren(
                el(
                    "div",
                    { class: "queue-empty level-normal" },
                    icon("i-normal"),
                    state.containers.length === 0
                        ? "Әзірге контейнер қосылмаған"
                        : "Барлық контейнер қалыпты жұмыс істеп тұр"
                )
            );

            return;

        }

        list.replaceChildren(...items.map(item => {

            const isFire = isFireAlert(item);

            const isFull = item.level === "full";

            const kind = isFire
                ? `fire-${item.fireRisk}`
                : isFull ? "level-full" : "level-none";

            const button = el(
                "button",
                {
                    type: "button",
                    class:
                        `queue-item ${kind}`
                        + (item.id === state.selectedId ? " is-selected" : ""),
                    "data-focus-id": `queue-${item.id}`
                },
                icon(isFire ? "i-flame" : isFull ? "i-full" : "i-offline"),
                el(
                    "span",
                    { class: "queue-text" },
                    el("span", { class: "queue-name" }, item.name),
                    el("span", { class: "queue-reason" }, getQueueReason(item))
                ),
                el(
                    "span",
                    { class: "queue-value" },
                    isFire && item.temperature !== null
                        ? formatTemperature(item.temperature)
                        : formatFill(item.fill)
                )
            );

            button.addEventListener("click", () => selectContainer(item.id));

            return button;

        }));

    });

}


function queuePriority(item) {

    if (item.fireRisk === "DANGER") {
        return 0;
    }

    if (isFireAlert(item)) {
        return 1;
    }

    return item.level === "full" ? 2 : 3;

}


function getQueueReason(item) {

    if (isFireAlert(item)) {
        return (
            `${FIRE_RISKS[item.fireRisk].label}: `
            + (item.fireNote || "өрт қаупінің белгісі")
            + (item.offline ? `, соңғы дерек ${formatAge(item.age)}` : "")
        );
    }

    if (item.lastAt === null) {
        return "Датчик әлі дерек жібермеген";
    }

    if (item.level !== "full") {
        return `Датчик үнсіз, соңғы дерек ${formatAge(item.age)}`;
    }

    if (item.offline) {
        return `Шығару қажет, соңғы дерек ${formatAge(item.age)}`;
    }

    return "Қоқысты шығару қажет";

}


// ============================================================
// TABLE
// ============================================================

function initTableControls() {

    document
        .getElementById("tableSearch")
        .addEventListener("input", event => {
            state.query = event.target.value.trim().toLocaleLowerCase("kk");
            renderTable();
        });

    const filters = document.getElementById("tableFilters");

    filters.addEventListener("click", event => {

        const button = event.target.closest("button");

        if (!button) {
            return;
        }

        state.filter = button.dataset.filter;

        for (const item of filters.querySelectorAll("button")) {
            item.setAttribute("aria-pressed", String(item === button));
        }

        renderTable();

    });

    for (const header of document.querySelectorAll("th[data-sort]")) {

        header.querySelector("button").addEventListener("click", () => {

            const key = header.dataset.sort;

            if (state.sort.key === key) {
                state.sort.direction =
                    state.sort.direction === "asc" ? "desc" : "asc";
            } else {
                state.sort = {
                    key,
                    direction: key === "fill" || key === "fireRank" ? "desc" : "asc"
                };
            }

            renderTable();

        });

    }

}


function matchesFilters(item) {

    if (state.filter === "offline" && !item.offline) {
        return false;
    }

    if (state.filter === "fire" && !isFireAlert(item)) {
        return false;
    }

    if (
        !["all", "offline", "fire"].includes(state.filter)
        && item.level !== state.filter
    ) {
        return false;
    }

    return `${item.name} ${item.location}`
        .toLocaleLowerCase("kk")
        .includes(state.query);

}


function compareContainers(a, b) {

    const { key, direction } = state.sort;

    const left = a[key];

    const right = b[key];

    // Containers without data stay at the bottom in both directions
    if (left === null || right === null) {
        return Number(left === null) - Number(right === null);
    }

    const result = typeof left === "string"
        ? left.localeCompare(right, "kk", { numeric: true })
        : left - right;

    return direction === "asc" ? result : -result;

}


function renderTable() {

    for (const header of document.querySelectorAll("th[data-sort]")) {

        if (header.dataset.sort === state.sort.key) {
            header.setAttribute(
                "aria-sort",
                state.sort.direction === "asc" ? "ascending" : "descending"
            );
        } else {
            header.removeAttribute("aria-sort");
        }

    }

    const body = document.getElementById("tableBody");

    const rows = state.containers
        .filter(matchesFilters)
        .sort(compareContainers);

    renderKeepingFocus(body, () => {

        if (rows.length === 0) {

            body.replaceChildren(
                el(
                    "tr",
                    {},
                    el(
                        "td",
                        { class: "table-empty", colspan: "7" },
                        state.containers.length === 0
                            ? "Әзірге контейнер қосылмаған"
                            : "Сүзгіге сәйкес контейнер табылмады"
                    )
                )
            );

            return;

        }

        body.replaceChildren(...rows.map(createTableRow));

    });

}


function createTableRow(item) {

    const meterFill = el("span");

    meterFill.style.width = `${item.fill ?? 0}%`;

    const lastSeen = el("span", { class: "last-seen" });

    if (item.offline) {
        lastSeen.append(icon("i-offline"));
    }

    lastSeen.append(
        item.lastAt === null ? "Дерек жоқ" : formatAge(item.age)
    );

    if (item.lastAt !== null) {
        lastSeen.title = formatDateTime(item.lastAt);
    }

    const row = el(
        "tr",
        {
            "data-id": item.id,
            class:
                (item.offline ? "is-offline" : "")
                + (item.id === state.selectedId ? " is-selected" : "")
        },
        el(
            "td",
            {},
            el(
                "button",
                {
                    type: "button",
                    class: "row-name",
                    "data-focus-id": `row-${item.id}`,
                    "aria-pressed": String(item.id === state.selectedId)
                },
                item.name
            )
        ),
        el("td", { class: "cell-secondary" }, item.location),
        el(
            "td",
            {},
            el(
                "div",
                { class: `meter-cell level-${item.level}` },
                el("div", { class: "meter" }, meterFill),
                el("span", { class: "meter-value" }, formatFill(item.fill))
            )
        ),
        el(
            "td",
            { class: "align-end numeric" },
            formatDistance(item.distance)
        ),
        el(
            "td",
            {},
            el(
                "span",
                { class: `badge level-${item.level}` },
                icon(LEVELS[item.level].icon),
                LEVELS[item.level].label
            )
        ),
        el("td", {}, createSafetyCell(item)),
        el("td", {}, lastSeen)
    );

    row.addEventListener("click", () => selectContainer(item.id));

    return row;

}


function createSafetyCell(item) {

    const risk = FIRE_RISKS[item.fireRisk];

    const iconName = isFireAlert(item)
        ? "i-flame"
        : item.fireRisk === "NORMAL" ? "i-normal" : "i-none";

    const values = [
        item.temperature === null ? null : formatTemperature(item.temperature),
        item.gas === null ? null : `газ ${formatGas(item.gas)}`
    ].filter(Boolean);

    const cell = el(
        "div",
        {
            class: `safety-cell fire-${item.fireRisk || "NONE"}`,
            title: item.fireNote || null
        },
        el("span", { class: "badge" }, icon(iconName), risk.label)
    );

    if (values.length > 0) {
        cell.append(el("span", { class: "safety-values numeric" }, values.join(" · ")));
    }

    return cell;

}


// ============================================================
// SELECTION
// ============================================================

function selectContainer(containerId) {

    const nextId = state.selectedId === containerId ? null : containerId;

    if (map) {
        map.closePopup();
    }

    setSelected(nextId);

    if (nextId !== null && markers[nextId]) {
        markers[nextId].openPopup();
    }

}


function setSelected(containerId) {

    if (state.selectedId === containerId) {
        return;
    }

    state.selectedId = containerId;

    renderFireAlert();

    renderQueue();

    renderTable();

    renderChartLegend();

    renderChart();

}


// ============================================================
// MAP
// ============================================================

function initMap() {

    if (typeof L === "undefined") {
        console.error("Leaflet was not loaded");
        return;
    }

    map = L.map("map").setView(ALMATY_CENTER, 12);

    L.tileLayer(

        "https://tile{s}.maps.2gis.com/tiles?x={x}&y={y}&z={z}&v=1",

        {
            subdomains: ["0", "1", "2", "3", "4"],
            minZoom: 1,
            maxZoom: 18,
            attribution: "&copy; 2GIS"
        }

    ).addTo(map);

    new ResizeObserver(() => map.invalidateSize())
        .observe(document.getElementById("map"));

    loadDistricts();

    initWorkers();

}


// District boundaries under the bins; a district manager's own district stands out
async function loadDistricts() {

    try {

        const data = await fetchJson(DISTRICTS_URL);

        const layer = L.geoJSON(data, {
            style: feature => ({
                className: feature.properties.mine ? "district-area is-mine" : "district-area"
            }),
            onEachFeature: (feature, shape) => shape.bindTooltip(
                feature.properties.name,
                { sticky: true, className: "district-tooltip" }
            )
        }).addTo(map);

        layer.bringToBack();

        const mine = data.features.filter(feature => feature.properties.mine);

        if (mine.length > 0) {
            map.fitBounds(L.geoJSON({ type: "FeatureCollection", features: mine }).getBounds(), {
                padding: [24, 24]
            });
            mapFitted = true;
        }

    } catch (error) {

        console.error("Districts error:", error);

    }

}


function renderMap() {

    if (!map) {
        return;
    }

    const located = state.containers.filter(item =>
        item.latitude !== null && item.longitude !== null
    );

    for (const item of located) {
        updateMarker(item);
    }

    const locatedIds = new Set(located.map(item => item.id));

    for (const id of Object.keys(markers)) {
        if (!locatedIds.has(Number(id))) {
            markers[id].remove();
            delete markers[id];
        }
    }

    const missing = state.containers.length - located.length;

    setText(
        "mapNote",
        missing > 0
            ? `${missing} контейнердің координаты көрсетілмеген`
            : "Алматыдағы бақылау аймағы"
    );

    if (!mapFitted && located.length > 0) {

        map.fitBounds(
            located.map(item => [item.latitude, item.longitude]),
            { padding: [60, 60], maxZoom: 15 }
        );

        mapFitted = true;

    }

}


function updateMarker(item) {

    const level = item.offline ? "none" : item.level;

    const fill = item.fill === null ? null : Math.round(item.fill);

    const fireRisk = isFireAlert(item) ? item.fireRisk : "";

    const iconKey = `${level}-${fill}-${fireRisk}`;

    // Fire risk first, then full bins, stay on top of their neighbours
    const zIndexOffset =
        fireRisk === "DANGER" ? 3000
            : fireRisk ? 2000
                : level === "full" ? 1000 : 0;

    const location = [item.latitude, item.longitude];

    const existing = markers[item.id];

    if (existing) {

        existing.setLatLng(location);

        // Rebuilding the icon on every poll would make the marker flicker
        if (existing.iconKey !== iconKey) {
            existing.setIcon(createMarkerIcon(level, fill, fireRisk));
            existing.setZIndexOffset(zIndexOffset);
            existing.iconKey = iconKey;
        }

        existing.setPopupContent(createPopupContent(item));

        return;

    }

    const marker = L.marker(location, {
        icon: createMarkerIcon(level, fill, fireRisk),
        title: item.name,
        alt: item.name,
        zIndexOffset
    }).addTo(map);

    marker.iconKey = iconKey;

    marker.bindPopup(createPopupContent(item), { minWidth: 210 });

    marker.on("popupopen", () => setSelected(item.id));

    marker.on("popupclose", () => {
        if (state.selectedId === item.id) {
            setSelected(null);
        }
    });

    markers[item.id] = marker;

}


// The bin body is a trapezoid; its contents rise from the bottom edge
const BIN_TOP = 13;

const BIN_BOTTOM = 42;

const BIN_TAPER = 3;


function binContents(fill) {

    if (fill === null || fill <= 0) {
        return "";
    }

    const y = BIN_BOTTOM - (BIN_BOTTOM - BIN_TOP) * Math.min(fill, 100) / 100;

    const inset = BIN_TAPER * (y - BIN_TOP) / (BIN_BOTTOM - BIN_TOP);

    return `<path class="bin-fill" d="M${5 + inset} ${y}H${29 - inset}L26 ${BIN_BOTTOM}H8Z"/>`;

}


function createMarkerIcon(level, fill, fireRisk = "") {

    const label = fill === null ? "–" : `${fill}%`;

    const pulse = level === "full"
        ? '<ellipse class="bin-pulse" cx="17" cy="44" rx="9" ry="3"/>'
        : "";

    // A flame badge on the lid; it pulses when the signs point to danger
    const fireBadge = fireRisk
        ? `
            <g class="bin-fire fire-${fireRisk}">
                ${fireRisk === "DANGER" ? '<circle class="bin-fire-pulse" cx="30" cy="9" r="7.5"/>' : ""}
                <circle class="bin-fire-dot" cx="30" cy="9" r="7.5"/>
                <path class="bin-fire-flame" d="M30 3.8c2 2.1 3.6 3.9 3.6 6a3.6 3.6 0 0 1-7.2 0c0-1.2.5-2.2 1.4-3 .1 1 .6 1.7 1.3 2-.2-1.8.1-3.4.9-5z"/>
            </g>
        `
        : "";

    return L.divIcon({
        className: "qaldyk-marker",
        html: `
            <div class="bin-marker level-${level}">
                <svg viewBox="0 0 34 46" width="34" height="46" aria-hidden="true">
                    ${pulse}
                    <ellipse class="bin-shadow" cx="17" cy="44" rx="8" ry="2"/>
                    <path class="bin-body" d="M5 ${BIN_TOP}H29L26 ${BIN_BOTTOM}H8Z"/>
                    ${binContents(fill)}
                    <path class="bin-ribs" d="M12 18V37M17 18V37M22 18V37"/>
                    <path class="bin-outline" d="M5 ${BIN_TOP}H29L26 ${BIN_BOTTOM}H8Z"/>
                    <path class="bin-handle" d="M13 8V5.5H21V8"/>
                    <rect class="bin-lid" x="3" y="8" width="28" height="4.5" rx="1.5"/>
                    <text class="bin-label" x="17" y="31" text-anchor="middle">${label}</text>
                    ${fireBadge}
                </svg>
            </div>
        `,
        iconSize: [34, 46],
        iconAnchor: [17, 44],
        popupAnchor: [0, -44]
    });

}


function createPopupContent(item) {

    const row = (label, value) =>
        el("div", {}, el("dt", {}, label), el("dd", {}, value));

    return el(
        "div",
        {},
        el("div", { class: "popup-title" }, item.name),
        el("div", { class: "popup-location" }, item.location),
        el(
            "dl",
            { class: "popup-rows" },
            row("Толу деңгейі", formatFill(item.fill)),
            row("Қашықтық", formatDistance(item.distance)),
            row("Күйі", LEVELS[item.level].label),
            row("Температура", formatTemperature(item.temperature)),
            row("Түтін/газ", formatGas(item.gas)),
            row("Өрт қаупі", FIRE_RISKS[item.fireRisk].label),
            row(
                "Соңғы дерек",
                item.lastAt === null ? "Дерек жоқ" : formatAge(item.age)
            )
        ),
        isFireAlert(item) && item.fireNote
            ? el("div", { class: `popup-fire fire-${item.fireRisk}` }, item.fireNote)
            : ""
    );

}


// ============================================================
// TREND CHART
// ============================================================

function initRangeFilter() {

    const filters = document.getElementById("rangeFilters");

    filters.addEventListener("click", event => {

        const button = event.target.closest("button");

        if (!button) {
            return;
        }

        state.hours = Number(button.dataset.hours);

        for (const item of filters.querySelectorAll("button")) {
            item.setAttribute("aria-pressed", String(item === button));
        }

        loadHistory();

    });

    const metrics = document.getElementById("metricFilters");

    metrics.addEventListener("click", event => {

        const button = event.target.closest("button");

        if (!button || button.dataset.metric === state.metric) {
            return;
        }

        state.metric = button.dataset.metric;

        for (const item of metrics.querySelectorAll("button")) {
            item.setAttribute("aria-pressed", String(item === button));
        }

        loadHistory();

    });

    document
        .getElementById("trendDetails")
        .addEventListener("toggle", renderChartTable);

}


function initChartResize() {

    const chart = document.getElementById("trendChart");

    let lastWidth = 0;

    new ResizeObserver(() => {
        if (chart.clientWidth !== lastWidth) {
            lastWidth = chart.clientWidth;
            renderChart();
        }
    }).observe(chart);

}


// Color follows the container; only the first eight get their own hue
function seriesColor(item) {

    return item.slot === null ? "var(--muted)" : `var(--series-${item.slot})`;

}


function renderChartLegend() {

    const legend = document.getElementById("trendLegend");

    renderKeepingFocus(legend, () => {

        legend.replaceChildren(...state.containers.map(item => {

            const key = el("span", { class: "line-key" });

            key.style.setProperty("--key", seriesColor(item));

            const button = el(
                "button",
                {
                    type: "button",
                    "data-focus-id": `legend-${item.id}`,
                    "aria-pressed": String(item.id === state.selectedId)
                },
                key,
                item.name
            );

            button.addEventListener("click", () => selectContainer(item.id));

            return el("li", {}, button);

        }));

    });

}


function getChartSeries() {

    // A response for another metric is still in flight
    if (!state.history || state.history.metric !== state.metric) {
        return [];
    }

    const toDisplay = state.metric === "gas"
        ? ([time, value]) => [time, gasPercent(value)]
        : point => point;

    return state.containers
        .map(item => ({
            item,
            points: (state.history.series[item.id] || []).map(toDisplay)
        }))
        .filter(series => series.points.length > 0);

}


function evenTicks(low, high) {

    const step = (high - low) / 4;

    return [0, 1, 2, 3, 4].map(index => Math.round(low + step * index));

}


// Scale, labels and thresholds of the metric shown on the chart
function chartMetric(series) {

    const limits = state.fireThresholds;

    if (state.metric === "temperature") {

        const values = series.flatMap(({ points }) => points.map(point => point[1]));

        const low = Math.min(0, Math.floor(Math.min(...values, 0) / 10) * 10);

        const high = Math.max(80, Math.ceil((Math.max(...values, 0) + 5) / 10) * 10);

        return {
            min: low,
            max: high,
            ticks: evenTicks(low, high),
            axis: value => `${value}°C`,
            format: formatTemperature,
            name: "температурасы",
            empty: "Таңдалған кезеңде температура дерегі жоқ.",
            thresholds: limits ? [
                { value: limits.temperature_danger, color: "var(--status-full)", name: "Қауіпті" },
                { value: limits.temperature_warning, color: "var(--fire-warning)", name: "Ескерту" }
            ] : []
        };

    }

    if (state.metric === "gas") {

        return {
            min: 0,
            max: 100,
            ticks: [0, 25, 50, 75, 100],
            axis: value => `${value}%`,
            format: value => value === null ? "–" : `${Math.round(value)}%`,
            name: "түтін/газ деңгейі (сенсор шкаласынан)",
            empty: "Таңдалған кезеңде түтін/газ дерегі жоқ.",
            thresholds: limits ? [
                { value: limits.gas_danger / limits.gas_max * 100, color: "var(--status-full)", name: "Қауіпті" },
                { value: limits.gas_warning / limits.gas_max * 100, color: "var(--fire-warning)", name: "Ескерту" }
            ] : []
        };

    }

    return {
        min: 0,
        max: 100,
        ticks: [0, 25, 50, 75, 100],
        axis: value => `${value}%`,
        format: formatFill,
        name: "толу деңгейі",
        empty: "Таңдалған кезеңде дерек жоқ. Ұзағырақ кезеңді таңдап көріңіз.",
        thresholds: THRESHOLDS
    };

}


function renderChart() {

    const host = document.getElementById("trendChart");

    const width = host.clientWidth;

    if (width === 0) {
        return;
    }

    const series = getChartSeries();

    const metric = chartMetric(series);

    const isNarrow = width < 560;

    const useDirectLabels = !isNarrow && series.length <= 4;

    const height = isNarrow ? 240 : 300;

    const margin = {
        top: 12,
        right: useDirectLabels ? 136 : 12,
        bottom: 28,
        left: 46
    };

    const plotWidth = width - margin.left - margin.right;

    const plotHeight = height - margin.top - margin.bottom;

    const endTime = serverNow();

    const startTime = endTime - state.hours * 3600 * 1000;

    const x = time =>
        margin.left + (time - startTime) / (endTime - startTime) * plotWidth;

    const y = value =>
        margin.top + (1 - (value - metric.min) / (metric.max - metric.min)) * plotHeight;

    const svg = svgEl("svg", {
        viewBox: `0 0 ${width} ${height}`,
        height,
        role: "img",
        tabindex: "0",
        "aria-label":
            `Контейнерлердің ${metric.name} графигі. Мәндерді көру үшін `
            + "сол және оң көрсеткілерді басыңыз."
    });

    drawChartGrid(svg, { x, y, margin, plotWidth, startTime, endTime, isNarrow, metric });

    const selectedId = state.selectedId;

    const bucket = (state.history ? state.history.bucket_seconds : 0) * 1000;

    for (const { item, points } of series) {

        const dimmed = selectedId !== null && selectedId !== item.id;

        const group = svgEl("g", { opacity: dimmed ? "0.22" : "1" });

        group.style.color = seriesColor(item);

        drawSeriesLine(group, points, bucket, x, y);

        svg.append(group);

    }

    drawThresholdLabels(svg, margin, y, metric);

    if (useDirectLabels) {
        drawEndLabels(svg, series, x, y, metric);
    }

    host.replaceChildren(svg);

    if (series.length === 0 && state.history) {
        host.append(el(
            "div",
            { class: "chart-empty" },
            el("span", {}, metric.empty)
        ));
    }

    attachCrosshair(host, svg, series, { x, y, margin, plotHeight, width, metric });

}


function drawChartGrid(svg, { x, y, margin, plotWidth, startTime, endTime, isNarrow, metric }) {

    const right = margin.left + plotWidth;

    for (const value of metric.ticks) {

        svg.append(svgEl("line", {
            x1: margin.left,
            x2: right,
            y1: y(value),
            y2: y(value),
            stroke: value === metric.min ? "var(--axis)" : "var(--grid)",
            "stroke-width": 1
        }));

        const label = svgEl("text", {
            x: margin.left - 8,
            y: y(value) + 4,
            "text-anchor": "end",
            class: "chart-axis"
        });

        label.textContent = metric.axis(value);

        svg.append(label);

    }

    for (const threshold of metric.thresholds) {

        svg.append(svgEl("line", {
            x1: margin.left,
            x2: right,
            y1: y(threshold.value),
            y2: y(threshold.value),
            stroke: threshold.color,
            "stroke-width": 1
        }));

    }

    const tickCount = isNarrow ? 3 : 6;

    const isWeek = endTime - startTime > 24 * 3600 * 1000;

    for (let index = 0; index <= tickCount; index++) {

        const time = startTime + (endTime - startTime) * index / tickCount;

        const date = new Date(time);

        const anchor =
            index === 0 ? "start" : index === tickCount ? "end" : "middle";

        const label = svgEl("text", {
            x: x(time),
            y: y(metric.min) + 18,
            "text-anchor": anchor,
            class: "chart-axis"
        });

        label.textContent = isWeek ? formatDay(date) : formatShortTime(date);

        svg.append(label);

    }

}


// Drawn above the lines, with a halo, so a line never strikes through the text
function drawThresholdLabels(svg, margin, y, metric) {

    for (const threshold of metric.thresholds) {

        const label = svgEl("text", {
            x: margin.left + 6,
            y: y(threshold.value) - 5,
            class: "chart-axis chart-threshold"
        });

        label.textContent = threshold.name;

        svg.append(label);

    }

}


function drawSeriesLine(group, points, bucket, x, y) {

    let path = "";

    points.forEach(([time, value], index) => {

        const previous = points[index - 1];

        const next = points[index + 1];

        // A sensor outage stays visible as a gap instead of a straight line
        const startsSegment = !previous || time - previous[0] > bucket * 3;

        const endsSegment = !next || next[0] - time > bucket * 3;

        if (startsSegment && endsSegment) {
            group.append(svgEl("circle", {
                cx: x(time),
                cy: y(value),
                r: 2.5,
                fill: "currentColor"
            }));
        }

        path += `${startsSegment ? "M" : "L"}${x(time).toFixed(1)} ${y(value).toFixed(1)}`;

    });

    group.append(svgEl("path", {
        d: path,
        fill: "none",
        stroke: "currentColor",
        "stroke-width": 2,
        "stroke-linecap": "round",
        "stroke-linejoin": "round"
    }));

    const [lastTime, lastValue] = points[points.length - 1];

    group.append(svgEl("circle", {
        cx: x(lastTime),
        cy: y(lastValue),
        r: 4,
        fill: "currentColor",
        stroke: "var(--surface)",
        "stroke-width": 2
    }));

}


function drawEndLabels(svg, series, x, y, metric) {

    const labels = series
        .map(({ item, points }) => {
            const [time, value] = points[points.length - 1];
            return { item, value, x: x(time) + 9, y: y(value) };
        })
        .sort((a, b) => a.y - b.y);

    labels.forEach((label, index) => {

        const previous = labels[index - 1];

        const next = labels[index + 1];

        // Converging lines rely on the legend and tooltip instead
        if (
            (previous && label.y - previous.y < 14)
            || (next && next.y - label.y < 14)
        ) {
            return;
        }

        const text = svgEl("text", {
            x: label.x,
            y: label.y + 4,
            class: "chart-label"
        });

        text.textContent =
            `${truncate(label.item.name, 14)} ${metric.axis(Math.round(label.value))}`;

        svg.append(text);

    });

}


function truncate(text, length) {

    return text.length > length ? `${text.slice(0, length - 1)}…` : text;

}


function attachCrosshair(host, svg, series, { x, y, margin, plotHeight, width, metric }) {

    const times = [...new Set(
        series.flatMap(({ points }) => points.map(point => point[0]))
    )].sort((a, b) => a - b);

    if (times.length === 0) {
        return;
    }

    const valuesByTime = series.map(({ item, points }) => ({
        item,
        values: new Map(points)
    }));

    const overlay = svgEl("g", { "pointer-events": "none" });

    svg.append(overlay);

    const tooltip = el("div", { class: "chart-tooltip", hidden: "" });

    host.append(tooltip);

    let activeIndex = null;

    const hide = () => {
        activeIndex = null;
        overlay.replaceChildren();
        tooltip.hidden = true;
    };

    const show = index => {

        activeIndex = index;

        const time = times[index];

        const position = x(time);

        overlay.replaceChildren(svgEl("line", {
            x1: position,
            x2: position,
            y1: margin.top,
            y2: margin.top + plotHeight,
            stroke: "var(--muted)",
            "stroke-width": 1
        }));

        const rows = valuesByTime
            .filter(({ values }) => values.has(time))
            .map(({ item, values }) => ({ item, value: values.get(time) }))
            .sort((a, b) => b.value - a.value);

        for (const row of rows) {
            overlay.append(svgEl("circle", {
                cx: position,
                cy: y(row.value),
                r: 4,
                fill: seriesColor(row.item),
                stroke: "var(--surface)",
                "stroke-width": 2
            }));
        }

        tooltip.replaceChildren(
            el("div", { class: "tooltip-time" }, formatDateTime(new Date(time))),
            ...rows.map(row => {
                const key = el("span", { class: "line-key" });
                key.style.setProperty("--key", seriesColor(row.item));
                return el(
                    "div",
                    { class: "tooltip-row" },
                    key,
                    el("strong", {}, metric.format(row.value)),
                    el("span", {}, row.item.name)
                );
            })
        );

        tooltip.hidden = false;

        const tooltipWidth = tooltip.offsetWidth;

        const left =
            position + 12 + tooltipWidth > width
                ? Math.max(position - 12 - tooltipWidth, 0)
                : position + 12;

        tooltip.style.left = `${left}px`;

        tooltip.style.top = `${margin.top}px`;

    };

    svg.addEventListener("pointermove", event => {

        const box = svg.getBoundingClientRect();

        const pointer = event.clientX - box.left;

        let nearest = 0;

        times.forEach((time, index) => {
            if (Math.abs(x(time) - pointer) < Math.abs(x(times[nearest]) - pointer)) {
                nearest = index;
            }
        });

        show(nearest);

    });

    svg.addEventListener("pointerleave", hide);

    svg.addEventListener("blur", hide);

    svg.addEventListener("keydown", event => {

        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
            return;
        }

        event.preventDefault();

        const step = event.key === "ArrowLeft" ? -1 : 1;

        const start = activeIndex === null ? times.length : activeIndex;

        show(Math.min(Math.max(start + step, 0), times.length - 1));

    });

}


// The same values as the chart, reachable without hovering
function renderChartTable() {

    const details = document.getElementById("trendDetails");

    if (!details.open) {
        return;
    }

    const target = document.getElementById("trendTable");

    const series = getChartSeries();

    if (series.length === 0) {
        target.replaceChildren(
            el("div", { class: "table-empty" }, "Таңдалған кезеңде дерек жоқ")
        );
        return;
    }

    const lookups = series.map(({ points }) => new Map(points));

    const metric = chartMetric(series);

    const times = [...new Set(
        series.flatMap(({ points }) => points.map(point => point[0]))
    )].sort((a, b) => b - a);

    target.replaceChildren(el(
        "table",
        { class: "data-table" },
        el(
            "thead",
            {},
            el(
                "tr",
                {},
                el("th", { scope: "col" }, "Уақыт"),
                ...series.map(({ item }) =>
                    el("th", { scope: "col", class: "align-end" }, item.name)
                )
            )
        ),
        el(
            "tbody",
            {},
            ...times.map(time => el(
                "tr",
                {},
                el("td", { class: "numeric" }, formatDateTime(new Date(time))),
                ...lookups.map(values => el(
                    "td",
                    { class: "align-end numeric" },
                    metric.format(values.has(time) ? values.get(time) : null)
                ))
            ))
        )
    ));

}


// ============================================================
// LIVE WORKERS
// ============================================================

const WORKERS_URL = "/api/workers/";

const WORKER_REFRESH_INTERVAL = 15000;

const WORKER_TOGGLE_KEY = "qaldyk-workers";

// Points walked in the last few minutes are drawn brighter than the rest
const RECENT_TRAIL_POINTS = 12;

const WORKER_STATES = {
    live: "Желіде",
    recent: "Жақында белгі берді",
    offline: "Желіден тыс",
    unknown: "Орны белгісіз"
};

const workerMarks = {};

const workerView = {
    layer: null,
    workers: [],
    trailMinutes: 60,
    visible: readWorkerToggle()
};


function readWorkerToggle() {

    try {
        return localStorage.getItem(WORKER_TOGGLE_KEY) !== "off";
    } catch (error) {
        return true;
    }

}


function initWorkers() {

    workerView.layer = L.layerGroup();

    if (workerView.visible) {
        workerView.layer.addTo(map);
    }

    const Roster = L.Control.extend({
        options: { position: "topright" },
        onAdd() {
            const box = el("div", { class: "worker-roster", id: "workerRoster" });
            L.DomEvent.disableClickPropagation(box);
            L.DomEvent.disableScrollPropagation(box);
            return box;
        }
    });

    new Roster().addTo(map);

    refreshWorkers();

}


async function refreshWorkers() {

    if (!document.hidden) {

        try {

            const data = await fetchJson(WORKERS_URL);

            workerView.workers = data.workers;
            workerView.trailMinutes = data.trailMinutes;

            renderWorkers();

        } catch (error) {

            console.error("Workers error:", error);

        }

    }

    setTimeout(refreshWorkers, WORKER_REFRESH_INTERVAL);

}


function workerAge(worker) {

    return worker.age === null ? "" : formatAge(worker.age * 1000);

}


function renderWorkers() {

    const located = workerView.workers.filter(worker => worker.latitude !== null);

    for (const worker of located) {
        updateWorkerMark(worker);
    }

    const ids = new Set(located.map(worker => worker.id));

    for (const id of Object.keys(workerMarks)) {
        if (!ids.has(Number(id))) {
            removeWorkerMark(id);
        }
    }

    renderRoster();

}


function removeWorkerMark(id) {

    const mark = workerMarks[id];

    for (const layer of [mark.marker, mark.trail, mark.recent, mark.route, mark.target]) {
        layer.remove();
    }

    delete workerMarks[id];

}


function updateWorkerMark(worker) {

    const position = [worker.latitude, worker.longitude];

    let mark = workerMarks[worker.id];

    if (!mark) {

        mark = {
            trail: L.polyline([], { className: "worker-trail", interactive: false }),
            recent: L.polyline([], { className: "worker-trail-recent", interactive: false }),
            route: L.polyline([], { className: "worker-route", interactive: false }),
            target: L.circleMarker(position, { radius: 7, className: "worker-target", interactive: false }),
            marker: L.marker(position, { zIndexOffset: 4000, title: worker.name, keyboard: true })
        };

        for (const layer of [mark.trail, mark.recent, mark.route, mark.marker]) {
            layer.addTo(workerView.layer);
        }

        mark.marker.bindPopup("", { minWidth: 220 });

        workerMarks[worker.id] = mark;

    } else {

        glideMarker(mark.marker, position);

    }

    const iconKey = `${worker.state}|${worker.initials}|${worker.taskCount}|${worker.task ? worker.task.status : ""}`;

    if (mark.iconKey !== iconKey) {
        mark.marker.setIcon(createWorkerIcon(worker));
        mark.iconKey = iconKey;
    }

    // The path ends where the worker is now
    const path = [...worker.trail, position];

    mark.trail.setLatLngs(path);
    mark.recent.setLatLngs(path.slice(-RECENT_TRAIL_POINTS));

    const task = worker.task;

    if (task && task.latitude !== null) {

        mark.route.setLatLngs([position, [task.latitude, task.longitude]]);
        mark.target.setLatLng([task.latitude, task.longitude]);

        if (!workerView.layer.hasLayer(mark.target)) {
            mark.target.addTo(workerView.layer);
        }

    } else {

        mark.route.setLatLngs([]);
        mark.target.remove();

    }

    mark.marker.setPopupContent(createWorkerPopup(worker));

}


// Moves a marker smoothly to its new position instead of jumping
function glideMarker(marker, to) {

    const from = marker.getLatLng();

    if (from.lat === to[0] && from.lng === to[1]) {
        return;
    }

    const started = performance.now();

    const step = now => {

        const t = Math.min((now - started) / 900, 1);
        const ease = t * (2 - t);

        marker.setLatLng([
            from.lat + (to[0] - from.lat) * ease,
            from.lng + (to[1] - from.lng) * ease
        ]);

        if (t < 1) {
            requestAnimationFrame(step);
        }

    };

    requestAnimationFrame(step);

}


function createWorkerIcon(worker) {

    const classes = ["worker-marker", `state-${worker.state}`];

    if (worker.task) {
        classes.push(`task-${worker.task.status}`);
    }

    const node = el(
        "div",
        { class: classes.join(" ") },
        el("span", { class: "worker-pulse" }),
        el("span", { class: "worker-avatar" }, worker.initials),
        worker.taskCount ? el("span", { class: "worker-badge" }, String(worker.taskCount)) : "",
        el("span", { class: "worker-label" }, worker.name)
    );

    return L.divIcon({
        className: "worker-icon",
        html: node,
        iconSize: [38, 38],
        iconAnchor: [19, 19],
        popupAnchor: [0, -22]
    });

}


function formatMeters(meters) {

    return meters < 1000
        ? `${meters} м`
        : `${(meters / 1000).toFixed(1).replace(".", ",")} км`;

}


function createWorkerPopup(worker) {

    const row = (label, value) =>
        el("div", {}, el("dt", {}, label), el("dd", {}, value));

    const task = worker.task;

    const taskValue = task
        ? el("span", {}, el("a", { href: task.url }, task.code), ` · ${task.statusLabel}`)
        : "Бос";

    return el(
        "div",
        {},
        el("div", { class: "popup-title" }, worker.name),
        el("div", { class: "popup-location" }, worker.district || "Ауданы берілмеген"),
        el(
            "dl",
            { class: "popup-rows" },
            row("Күйі", `${WORKER_STATES[worker.state]} · ${workerAge(worker)}`),
            row("Тапсырма", taskValue),
            task ? row("Мекенжай", task.address) : "",
            task && task.distance !== null ? row("Тапсырмаға дейін", formatMeters(task.distance)) : "",
            row(`${workerView.trailMinutes} минутта жүрді`, formatMeters(worker.walked)),
            worker.taskCount > 1 ? row("Барлық тапсырма", String(worker.taskCount)) : ""
        )
    );

}


function renderRoster() {

    const box = document.getElementById("workerRoster");

    if (!box) {
        return;
    }

    const workers = workerView.workers;

    const live = workers.filter(worker => worker.state === "live").length;

    const toggle = el(
        "button",
        {
            type: "button",
            class: "worker-roster-toggle",
            "data-focus-id": "workers-toggle",
            "aria-pressed": String(workerView.visible),
            title: workerView.visible ? "Жұмысшыларды картадан жасыру" : "Жұмысшыларды картада көрсету"
        },
        el("span", { class: "worker-roster-dot" }),
        el("span", {}, "Жұмысшылар"),
        el("span", { class: "worker-roster-count" }, `${live}/${workers.length} желіде`)
    );

    toggle.addEventListener("click", () => {

        workerView.visible = !workerView.visible;

        try {
            localStorage.setItem(WORKER_TOGGLE_KEY, workerView.visible ? "on" : "off");
        } catch (error) {}

        if (workerView.visible) {
            workerView.layer.addTo(map);
        } else {
            workerView.layer.remove();
        }

        renderRoster();

    });

    const children = [toggle];

    if (workerView.visible) {

        const list = el("ul", { class: "worker-roster-list" });

        if (workers.length === 0) {
            list.append(el("li", { class: "worker-roster-empty" }, "Ауданға жұмысшы бекітілмеген"));
        }

        for (const worker of workers) {

            const detail = worker.task
                ? `${worker.task.code} · ${worker.task.statusLabel}`
                : worker.state === "unknown" ? "Орнын әлі бөліспеген" : `Бос · ${workerAge(worker)}`;

            const button = el(
                "button",
                { type: "button", class: `worker-roster-item state-${worker.state}`, "data-focus-id": `worker-${worker.id}` },
                el("span", { class: "worker-roster-avatar" }, worker.initials),
                el(
                    "span",
                    { class: "worker-roster-text" },
                    el("span", { class: "worker-roster-name" }, worker.name),
                    el("span", { class: "worker-roster-detail" }, detail)
                )
            );

            if (worker.latitude === null) {
                button.disabled = true;
            } else {
                button.addEventListener("click", () => focusWorker(worker.id));
            }

            list.append(el("li", {}, button));

        }

        children.push(list);

    }

    renderKeepingFocus(box, () => box.replaceChildren(...children));

}


function focusWorker(id) {

    const mark = workerMarks[id];

    if (!mark) {
        return;
    }

    map.flyTo(mark.marker.getLatLng(), Math.max(map.getZoom(), 16), { duration: 0.8 });

    map.once("moveend", () => mark.marker.openPopup());

}
