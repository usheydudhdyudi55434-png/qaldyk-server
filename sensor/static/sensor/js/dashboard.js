const API_BASE_URL = "/api/sensor/";

const CONTAINER_IDS = [1, 2, 3];

const REFRESH_INTERVAL = 5000;

// Almaty center
const ALMATY_CENTER = [43.238949, 76.889709];

// 2GIS API KEY
const GIS_API_KEY = "97b20387-e967-45a0-86b9-064216dab634";

// ------------------------------------------------------------
// GLOBAL VARIABLES
// ------------------------------------------------------------

let map = null;

const markers = {};

const containerData = {};


// ------------------------------------------------------------
// DOM READY
// ------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {

    console.log("QALDYQ dashboard starting...");

    initTheme();

    initThemeToggle();

    initMap();

    loadAllContainers();

    setInterval(() => {
        loadAllContainers();
    }, REFRESH_INTERVAL);

});


// ============================================================
// THEME
// ============================================================

function initTheme() {

    const savedTheme = localStorage.getItem("qaldyk-theme");

    if (savedTheme === "light") {

        document.body.classList.add("light-theme");

        updateThemeButton();

    } else {

        document.body.classList.remove("light-theme");

        updateThemeButton();

    }
}


function initThemeToggle() {

    const themeButton = document.getElementById("themeToggle");

    if (!themeButton) {
        return;
    }

    themeButton.addEventListener("click", () => {

        document.body.classList.toggle("light-theme");

        const isLight =
            document.body.classList.contains("light-theme");

        localStorage.setItem(
            "qaldyk-theme",
            isLight ? "light" : "dark"
        );

        updateThemeButton();

    });

}


function updateThemeButton() {

    const themeButton = document.getElementById("themeToggle");

    if (!themeButton) {
        return;
    }

    const isLight =
        document.body.classList.contains("light-theme");

    themeButton.textContent =
        isLight ? "☀️" : "🌙";

}


// ============================================================
// MAP
// ============================================================

function initMap() {

    const mapElement =
        document.getElementById("map");

    if (!mapElement) {

        console.error("Map element not found");

        return;
    }

    map = L.map("map", {

        zoomControl: true,

        attributionControl: true

    }).setView(

        ALMATY_CENTER,

        12

    );


    // --------------------------------------------------------
    // 2GIS TILE LAYER
    // --------------------------------------------------------

    L.tileLayer(

        `https://tile{s}.maps.2gis.com/v2/tiles/online_hd/{z}/{x}/{y}.png?key=${GIS_API_KEY}`,

        {

            subdomains: [
                "0",
                "1",
                "2",
                "3",
                "4"
            ],

            minZoom: 1,

            maxZoom: 18,

            attribution:
                "&copy; 2GIS"

        }

    ).addTo(map);


    // --------------------------------------------------------
    // Fix Leaflet size after page load
    // --------------------------------------------------------

    setTimeout(() => {

        if (map) {

            map.invalidateSize();

        }

    }, 300);

}


// ============================================================
// LOAD ALL CONTAINERS
// ============================================================

async function loadAllContainers() {

    console.log(
        "Loading sensor data..."
    );

    for (const containerId of CONTAINER_IDS) {

        await loadContainer(containerId);

    }

    updateStatistics();

    updateAlerts();

}


// ============================================================
// LOAD ONE CONTAINER
// ============================================================

async function loadContainer(containerId) {

    try {

        const response =
            await fetch(
                `${API_BASE_URL}${containerId}/`
            );


        if (!response.ok) {

            throw new Error(
                `HTTP ${response.status}`
            );

        }


        const data =
            await response.json();


        console.log(
            `Container #${containerId}:`,
            data
        );


        if (!Array.isArray(data) || data.length === 0) {

            showNoData(containerId);

            return;
        }


        // API returns newest first
        const latest =
            data[0];


        containerData[containerId] =
            latest;


        updateContainerUI(
            containerId,
            latest
        );


        updateMapMarker(
            containerId,
            latest
        );


    } catch (error) {

        console.error(
            `Container #${containerId} error:`,
            error
        );


        showNoData(containerId);

    }

}


// ============================================================
// UPDATE CONTAINER CARD
// ============================================================

function updateContainerUI(
    containerId,
    data
) {

    const fillLevel =
        Number(data.fill_level) || 0;

    const distance =
        Number(data.distance) || 0;

    const status =
        data.status || "NORMAL";


    // --------------------------------------------------------
    // Fill level
    // --------------------------------------------------------

    const fillElement =
        document.getElementById(
            `fill${containerId}`
        );


    if (fillElement) {

        fillElement.textContent =
            `${fillLevel.toFixed(1)}%`;

    }


    // --------------------------------------------------------
    // Distance
    // --------------------------------------------------------

    const distanceElement =
        document.getElementById(
            `distance${containerId}`
        );


    if (distanceElement) {

        distanceElement.textContent =
            distance.toFixed(2);

    }


    // --------------------------------------------------------
    // Status
    // --------------------------------------------------------

    const statusElement =
        document.getElementById(
            `status${containerId}`
        );


    if (statusElement) {

        statusElement.textContent =
            formatStatus(status);

    }


    // --------------------------------------------------------
    // Progress bar
    // --------------------------------------------------------

    const progressElement =
        document.getElementById(
            `progress${containerId}`
        );


    if (progressElement) {

        progressElement.style.width =
            `${Math.min(
                Math.max(fillLevel, 0),
                100
            )}%`;


        progressElement.classList.remove(

            "progress-normal",

            "progress-half",

            "progress-full"

        );


        if (fillLevel >= 80) {

            progressElement.classList.add(
                "progress-full"
            );

        } else if (fillLevel >= 50) {

            progressElement.classList.add(
                "progress-half"
            );

        } else {

            progressElement.classList.add(
                "progress-normal"
            );

        }

    }


    // --------------------------------------------------------
    // Fill color/state
    // --------------------------------------------------------

    if (fillElement) {

        fillElement.classList.remove(

            "fill-normal",

            "fill-half",

            "fill-full"

        );


        if (fillLevel >= 80) {

            fillElement.classList.add(
                "fill-full"
            );

        } else if (fillLevel >= 50) {

            fillElement.classList.add(
                "fill-half"
            );

        } else {

            fillElement.classList.add(
                "fill-normal"
            );

        }

    }


    // --------------------------------------------------------
    // Status color
    // --------------------------------------------------------

    if (statusElement) {

        statusElement.classList.remove(

            "status-normal",

            "status-half",

            "status-full"

        );


        if (fillLevel >= 80) {

            statusElement.classList.add(
                "status-full"
            );

        } else if (fillLevel >= 50) {

            statusElement.classList.add(
                "status-half"
            );

        } else {

            statusElement.classList.add(
                "status-normal"
            );

        }

    }

}


// ============================================================
// NO DATA
// ============================================================

function showNoData(containerId) {

    const fillElement =
        document.getElementById(
            `fill${containerId}`
        );

    const distanceElement =
        document.getElementById(
            `distance${containerId}`
        );

    const statusElement =
        document.getElementById(
            `status${containerId}`
        );

    const progressElement =
        document.getElementById(
            `progress${containerId}`
        );


    if (fillElement) {

        fillElement.textContent =
            "NO DATA";

    }


    if (distanceElement) {

        distanceElement.textContent =
            "--";

    }


    if (statusElement) {

        statusElement.textContent =
            "NO DATA";

    }


    if (progressElement) {

        progressElement.style.width =
            "0%";

    }

}


// ============================================================
// STATUS TEXT
// ============================================================

function formatStatus(status) {

    switch (status) {

        case "FULL":
            return "FULL";

        case "HALF FULL":
            return "HALF FULL";

        case "NORMAL":
            return "NORMAL";

        default:
            return status || "UNKNOWN";

    }

}


// ============================================================
// STATISTICS
// ============================================================

function updateStatistics() {

    let total = 0;

    let full = 0;

    let half = 0;

    let normal = 0;


    for (const containerId of CONTAINER_IDS) {

        const data =
            containerData[containerId];


        if (!data) {
            continue;
        }


        total++;


        const fillLevel =
            Number(data.fill_level) || 0;


        if (fillLevel >= 80) {

            full++;

        } else if (fillLevel >= 50) {

            half++;

        } else {

            normal++;

        }

    }


    setText(
        "totalContainers",
        total
    );

    setText(
        "fullContainers",
        full
    );

    setText(
        "halfContainers",
        half
    );

    setText(
        "normalContainers",
        normal
    );

}


// ============================================================
// UPDATE ALERTS
// ============================================================

function updateAlerts() {

    const alertBox =
        document.getElementById(
            "alertBox"
        );

    const alertList =
        document.getElementById(
            "alertList"
        );


    if (!alertBox || !alertList) {
        return;
    }


    const fullContainers = [];


    for (const containerId of CONTAINER_IDS) {

        const data =
            containerData[containerId];


        if (!data) {
            continue;
        }


        const fillLevel =
            Number(data.fill_level) || 0;


        if (fillLevel >= 80) {

            fullContainers.push({

                id: containerId,

                fill: fillLevel,

                distance:
                    Number(data.distance) || 0

            });

        }

    }


    // --------------------------------------------------------
    // No alerts
    // --------------------------------------------------------

    if (fullContainers.length === 0) {

        alertBox.style.display =
            "none";

        alertList.innerHTML = "";

        return;

    }


    // --------------------------------------------------------
    // Show alert
    // --------------------------------------------------------

    alertBox.style.display =
        "block";


    alertList.innerHTML = "";


    fullContainers.forEach(item => {

        const alertItem =
            document.createElement("div");


        alertItem.className =
            "alert-item";


        alertItem.innerHTML = `

            <div class="alert-item-left">

                <span class="alert-item-icon">
                    🔴
                </span>

                <div>

                    <strong>
                        Container #${item.id}
                    </strong>

                    <span>
                        Collection required
                    </span>

                </div>

            </div>

            <div class="alert-item-right">

                ${item.fill.toFixed(1)}%

            </div>

        `;


        alertList.appendChild(
            alertItem
        );

    });

}


// ============================================================
// MAP MARKERS
// ============================================================

function updateMapMarker(
    containerId,
    data
) {

    if (!map) {
        return;
    }


    /*
     * --------------------------------------------------------
     * IMPORTANT
     *
     * These are demo coordinates.
     * Replace them with latitude/longitude
     * from your PostgreSQL Container model.
     * --------------------------------------------------------
     */

    const containerLocations = {

        1: [
            43.238949,
            76.889709
        ],

        2: [
            43.245000,
            76.900000
        ],

        3: [
            43.230000,
            76.875000
        ]

    };


    const location =
        containerLocations[containerId];


    if (!location) {
        return;
    }


    const fillLevel =
        Number(data.fill_level) || 0;


    const status =
        data.status || "NORMAL";


    const markerColor =
        getMarkerColor(fillLevel);


    // --------------------------------------------------------
    // Create custom marker
    // --------------------------------------------------------

    const icon =
        createContainerIcon(
            containerId,
            markerColor
        );


    // --------------------------------------------------------
    // Existing marker
    // --------------------------------------------------------

    if (markers[containerId]) {

        markers[containerId]
            .setLatLng(location)
            .setIcon(icon);


        markers[containerId]
            .setPopupContent(
                createPopupContent(
                    containerId,
                    data
                )
            );


        return;

    }


    // --------------------------------------------------------
    // New marker
    // --------------------------------------------------------

    const marker =
        L.marker(
            location,
            {
                icon: icon
            }
        ).addTo(map);


    marker.bindPopup(
        createPopupContent(
            containerId,
            data
        )
    );


    markers[containerId] =
        marker;

}


// ============================================================
// MARKER COLOR
// ============================================================

function getMarkerColor(
    fillLevel
) {

    if (fillLevel >= 80) {

        return "#ef4444";

    }


    if (fillLevel >= 50) {

        return "#f59e0b";

    }


    return "#22c55e";

}


// ============================================================
// CREATE CUSTOM MARKER
// ============================================================

function createContainerIcon(
    containerId,
    color
) {

    return L.divIcon({

        className:
            "qaldyk-container-marker",

        html: `

            <div
                class="qaldyk-marker"
                style="
                    background:${color};
                    box-shadow:
                        0 0 0 5px
                        ${color}33,
                        0 8px 20px
                        rgba(0,0,0,.35);
                "
            >

                <span>
                    ${containerId}
                </span>

            </div>

        `,

        iconSize: [
            42,
            42
        ],

        iconAnchor: [
            21,
            21
        ],

        popupAnchor: [
            0,
            -20
        ]

    });

}


// ============================================================
// POPUP CONTENT
// ============================================================

function createPopupContent(
    containerId,
    data
) {

    const fillLevel =
        Number(data.fill_level) || 0;


    const distance =
        Number(data.distance) || 0;


    const status =
        data.status || "NORMAL";


    const statusColor =
        getMarkerColor(fillLevel);


    return `

        <div class="qaldyk-popup">

            <div class="popup-title">

                <span>
                    🗑️
                </span>

                Container #${containerId}

            </div>


            <div class="popup-divider"></div>


            <div class="popup-row">

                <span>
                    Fill level
                </span>

                <strong
                    style="
                        color:${statusColor};
                    "
                >
                    ${fillLevel.toFixed(1)}%
                </strong>

            </div>


            <div class="popup-row">

                <span>
                    Distance
                </span>

                <strong>
                    ${distance.toFixed(2)} cm
                </strong>

            </div>


            <div class="popup-row">

                <span>
                    Status
                </span>

                <strong
                    style="
                        color:${statusColor};
                    "
                >
                    ${status}
                </strong>

            </div>


            <div class="popup-time">

                Updated:
                ${formatDate(
                    data.created_at
                )}

            </div>

        </div>

    `;

}


// ============================================================
// DATE FORMAT
// ============================================================

function formatDate(
    dateString
) {

    if (!dateString) {
        return "--";
    }


    const date =
        new Date(dateString);


    if (Number.isNaN(
        date.getTime()
    )) {

        return dateString;

    }


    return date.toLocaleString(
        "kk-KZ",
        {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        }
    );

}


// ============================================================
// HELPER
// ============================================================

function setText(
    elementId,
    value
) {

    const element =
        document.getElementById(
            elementId
        );


    if (element) {

        element.textContent =
            value;

    }

}