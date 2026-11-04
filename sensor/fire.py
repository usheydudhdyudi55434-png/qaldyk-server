"""Fire risk signs from a bin's temperature sensor and MQ-2 smoke/gas sensor.

The sensors cannot predict a fire. They detect signs that often come before
or with one: heat, a fast temperature rise, smoke or flammable gas.
"""

NORMAL = "NORMAL"
WARNING = "WARNING"
DANGER = "DANGER"

RISK_LEVELS = (NORMAL, WARNING, DANGER)

# Fixed-temperature heat detectors trip at about 57 °C
TEMPERATURE_WARNING = 50
TEMPERATURE_DANGER = 65

# Rate-of-rise heat detectors trip at about 8 °C a minute
TEMPERATURE_RISE_PER_MINUTE = 8

# Only compare with a previous reading this recent
RISE_WINDOW_MINUTES = 10

# MQ-2 raw ESP32 ADC value, 0..4095; each sensor needs its own calibration
GAS_MAX = 4095
GAS_WARNING = 3000
GAS_DANGER = 3800


def thresholds():
    return {
        "temperature_warning": TEMPERATURE_WARNING,
        "temperature_danger": TEMPERATURE_DANGER,
        "temperature_rise_per_minute": TEMPERATURE_RISE_PER_MINUTE,
        "gas_max": GAS_MAX,
        "gas_warning": GAS_WARNING,
        "gas_danger": GAS_DANGER,
    }


def gas_percent(gas_level):
    return round(gas_level / GAS_MAX * 100)


def assess(temperature, gas_level, device_risk=None, previous=None, now=None):
    """Returns (risk, note) for one reading.

    previous is (temperature, created_at) of the container's last reading.
    The risk is "" when the device has no fire sensors at all.
    """

    if temperature is None and gas_level is None and device_risk is None:
        return "", ""

    signs = []

    if temperature is not None:
        if temperature >= TEMPERATURE_DANGER:
            signs.append((DANGER, f"Температура {temperature:.0f}°C"))
        elif temperature >= TEMPERATURE_WARNING:
            signs.append((WARNING, f"Температура {temperature:.0f}°C"))

        if previous and previous[0] is not None and now is not None:
            minutes = (now - previous[1]).total_seconds() / 60

            if 0.25 <= minutes <= RISE_WINDOW_MINUTES:
                rise = (temperature - previous[0]) / minutes

                if rise >= TEMPERATURE_RISE_PER_MINUTE:
                    signs.append((
                        WARNING,
                        f"Температура минутына {rise:.0f}°C көтерілуде"
                    ))

    if gas_level is not None:
        if gas_level >= GAS_DANGER:
            signs.append((DANGER, f"Түтін/газ {gas_percent(gas_level)}%"))
        elif gas_level >= GAS_WARNING:
            signs.append((WARNING, f"Түтін/газ {gas_percent(gas_level)}%"))

    risk = max((level for level, _ in signs), key=RISK_LEVELS.index, default=NORMAL)

    # Two independent signs at once are treated as confirmed danger
    if risk == WARNING and len(signs) >= 2:
        risk = DANGER

    # The device's own judgement can raise the risk, never lower it
    if device_risk in RISK_LEVELS and RISK_LEVELS.index(device_risk) > RISK_LEVELS.index(risk):
        risk = device_risk
        signs.append((device_risk, "Құрылғы қауіп белгісін жіберді"))

    return risk, ", ".join(note for _, note in signs)
