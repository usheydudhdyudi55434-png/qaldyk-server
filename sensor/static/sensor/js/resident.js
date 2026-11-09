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

    const place = (lat, lng) => {

        latitude.value = lat.toFixed(6);

        longitude.value = lng.toFixed(6);

        if (marker) {
            marker.setLatLng([lat, lng]);
        } else {
            marker = L.marker([lat, lng]).addTo(map);
        }

        status.textContent = "Нүкте белгіленді";

    };

    // A form sent back with errors keeps the point the resident chose
    if (latitude.value && longitude.value) {
        place(Number(latitude.value), Number(longitude.value));
        map.setView([Number(latitude.value), Number(longitude.value)], 16);
    }

    map.on("click", event => place(event.latlng.lat, event.latlng.lng));

    document.getElementById("locateMe").addEventListener("click", () => {

        if (!navigator.geolocation) {
            status.textContent = "Браузер орынды анықтай алмайды, картадан белгілеңіз";
            return;
        }

        status.textContent = "Орын анықталуда…";

        navigator.geolocation.getCurrentPosition(
            position => {
                place(position.coords.latitude, position.coords.longitude);
                map.setView([position.coords.latitude, position.coords.longitude], 17);
            },
            () => {
                status.textContent = "Орын анықталмады, картадан белгілеңіз";
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );

    });

    new ResizeObserver(() => map.invalidateSize()).observe(mapElement);

});
