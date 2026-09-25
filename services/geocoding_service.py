import requests


NOMINATIM_URL = (
    "https://nominatim.openstreetmap.org/search"
)

HEADERS = {
    "User-Agent": (
        "TravelLocalAssistant/1.0 "
        "(local development project)"
    )
}


def geocode_location(
    location: str
) -> dict | None:

    params = {
        "q": location,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "in"
    }

    response = requests.get(
        NOMINATIM_URL,
        params=params,
        headers=HEADERS,
        timeout=15
    )

    response.raise_for_status()

    results = response.json()

    if not results:
        return None

    result = results[0]

    return {
        "latitude": float(result["lat"]),
        "longitude": float(result["lon"]),
        "display_name": result.get(
            "display_name",
            location
        )
    }


def reverse_geocode(
    latitude: float,
    longitude: float
) -> str | None:

    params = {
        "lat": latitude,
        "lon": longitude,
        "format": "jsonv2",
        "zoom": 18
    }

    response = requests.get(
        NOMINATIM_URL.replace(
            "/search",
            "/reverse"
        ),
        params=params,
        headers=HEADERS,
        timeout=15
    )

    response.raise_for_status()

    result = response.json()

    return result.get(
        "display_name"
    )