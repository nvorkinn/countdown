"""Sample TfL stop responses, as the Unified API returns them, shared by the tests that parse or build stops."""

BUS_STOP_JSON = {
    "naptanId": "490000123W",
    "commonName": "Elephant & Castle",
    "modes": ["bus"],
    "additionalProperties": [{"key": "CompassPoint", "value": "N"}],
    "children": [],
    "stopType": "NaptanPublicBusCoachTram",
    "stopLetter": "W",
}

METRO_STOP_JSON = {
    "naptanId": "940GZZLUKNG",
    "commonName": "Kennington Underground Station",
    "modes": ["tube"],
    "additionalProperties": [],
    "children": [],
    "stopType": "NaptanMetroStation",
}
