// Lets a resident mark the problem on the map; the point fills two hidden fields

const ALMATY_CENTER = [43.238949, 76.889709];

document.addEventListener("DOMContentLoaded", () => {

    const mapElement = document.getElementById("pickMap");

    const latitude = document.getElementById("id_latitude");

    const longitude = document.getElementById("id_longitude");

    const status = document.getElementById("pickStatus");

    if (!mapElement || typeof L === "undefined") {
        return;
    }

    const map = L.map(mapElement).setView(ALMATY_CENTER, 12);

    L.tileLayer("https://tile{s}.maps.2gis.com/tiles?x={x}&y={y}&z={z}&v=1", {
        subdomains: ["0", "1", "2", "3", "4"],
        maxZoom: 18,
        attribution: "&copy; 2GIS"
    }).addTo(map);

    let marker = null;

    // A cleanup day shows the circle its QR is accepted in
    const radius = Number(mapElement.dataset.radius) || 0;
    let circle = null;

    const place = (lat, lng) => {

        latitude.value = lat.toFixed(6);

        longitude.value = lng.toFixed(6);

        if (marker) {
            marker.setLatLng([lat, lng]);
        } else {
            marker = L.marker([lat, lng]).addTo(map);
        }

        if (radius) {
            if (circle) {
                circle.setLatLng([lat, lng]);
            } else {
                circle = L.circle([lat, lng], {
                    radius,
                    color: "#2563eb",
                    weight: 2,
                    fillColor: "#3b82f6",
                    fillOpacity: .12
                }).addTo(map);
            }
        }

        status.textContent = gettext("Нүкте белгіленді");

        const clear = document.getElementById("clearPoint");
        if (clear) {
            clear.hidden = false;
        }

    };

    // Where it is allowed, the point can be taken away again
    const clearButton = document.getElementById("clearPoint");

    if (clearButton) {
        clearButton.addEventListener("click", () => {
            latitude.value = "";
            longitude.value = "";
            if (marker) {
                marker.remove();
                marker = null;
            }
            if (circle) {
                circle.remove();
                circle = null;
            }
            clearButton.hidden = true;
            status.textContent = gettext("Нүкте белгіленбеген");
        });
    }

    // A form sent back with errors keeps the point the resident chose
    if (latitude.value && longitude.value) {
        place(Number(latitude.value), Number(longitude.value));
        map.setView([Number(latitude.value), Number(longitude.value)], 16);
    }

    map.on("click", event => {
        place(event.latlng.lat, event.latlng.lng);
        // The acceptance circle is small; come close enough to see it
        if (radius && map.getZoom() < 15) {
            map.setView(event.latlng, 16);
        }
    });

    document.getElementById("locateMe").addEventListener("click", () => {

        if (!navigator.geolocation) {
            status.textContent = gettext("Браузер орынды анықтай алмайды, картадан белгілеңіз");
            return;
        }

        status.textContent = gettext("Орын анықталуда…");

        navigator.geolocation.getCurrentPosition(
            position => {
                place(position.coords.latitude, position.coords.longitude);
                map.setView([position.coords.latitude, position.coords.longitude], 17);
            },
            () => {
                status.textContent = gettext("Орын анықталмады, картадан белгілеңіз");
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );

    });

    new ResizeObserver(() => map.invalidateSize()).observe(mapElement);

});
