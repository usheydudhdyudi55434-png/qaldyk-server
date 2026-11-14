// Shares the worker's position with dispatchers while the tasks page is open,
// so they can send the nearest worker to a new request

const LOCATION_URL = "/tasks/location/";

// Often enough to draw the worker's path on the dispatcher's map
const SEND_EVERY = 20 * 1000;

document.addEventListener("DOMContentLoaded", () => {

    const status = document.getElementById("locationStatus");

    if (!status) {
        return;
    }

    if (!navigator.geolocation) {
        status.textContent = gettext("Браузер орынды анықтай алмайды");
        return;
    }

    const token = document.querySelector("input[name=csrfmiddlewaretoken]").value;

    let lastSent = 0;

    const send = async position => {

        if (Date.now() - lastSent < SEND_EVERY - 2000) {
            return;
        }

        lastSent = Date.now();

        try {

            const response = await fetch(LOCATION_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": token },
                body: JSON.stringify({
                    latitude: position.coords.latitude,
                    longitude: position.coords.longitude,
                    accuracy: position.coords.accuracy
                })
            });

            const time = new Date().toTimeString().slice(0, 5);

            status.textContent = response.ok
                ? interpolate(gettext("Орныңыз диспетчерге көрінеді · %(time)s"), { time }, true)
                : gettext("Орын жіберілмеді, бетті жаңартыңыз");

        } catch (error) {

            status.textContent = gettext("Орын жіберілмеді: интернет байланысын тексеріңіз");
            lastSent = 0;

        }

    };

    let latest = null;

    navigator.geolocation.watchPosition(
        position => {
            latest = position;
            send(position);
        },
        () => {
            status.textContent =
                gettext("Орынға рұқсат берілмеді: диспетчер сізді жақын жұмысшы ретінде көрмейді");
        },
        { enableHighAccuracy: true, maximumAge: 10000 }
    );

    // A worker standing still gets no new fixes; repeat the last one so they stay "online"
    setInterval(() => {
        if (latest && !document.hidden) {
            send(latest);
        }
    }, SEND_EVERY);

});
