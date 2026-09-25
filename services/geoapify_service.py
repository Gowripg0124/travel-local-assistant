import os
import requests
import math

from dotenv import load_dotenv

load_dotenv()


GEOAPIFY_URL = (
    "https://api.geoapify.com/v2/places"
)

GEOAPIFY_API_KEY = os.getenv(
    "GEOAPIFY_API_KEY"
)


CATEGORY_MAP = {
    "restaurant": "catering.restaurant",
    "cafe": "catering.cafe",
    "hotel": "accommodation.hotel",
}


def search_geoapify_places(
    latitude: float,
    longitude: float,
    place_type: str,
    radius: int = 5000,
    max_results: int = 5
):

    if not GEOAPIFY_API_KEY:
        raise ValueError(
            "GEOAPIFY_API_KEY is not configured."
        )

    category = CATEGORY_MAP.get(
        place_type
    )

    if not category:
        raise ValueError(
            f"Unsupported place type: {place_type}"
        )

    params = {
        "categories": category,
        "filter": (
            f"circle:"
            f"{longitude},"
            f"{latitude},"
            f"{radius}"
        ),
        "limit": max_results,
        "apiKey": GEOAPIFY_API_KEY
    }

    response = requests.get(
        GEOAPIFY_URL,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    places = []

    for feature in data.get(
        "features",
        []
    ):

        properties = feature.get(
            "properties",
            {}
        )

        place_lat = properties.get(
            "lat"
        )

        place_lon = properties.get(
            "lon"
        )

        if (
            place_lat is None
            or place_lon is None
        ):
            continue

        name = properties.get(
            "name"
        )

        if not name:
            continue

        # -----------------------------------------
        # Validate actual Geoapify category
        # -----------------------------------------

        if not is_valid_place_type(
            properties,
            place_type
        ):
            continue

        address = properties.get(
            "formatted",
            "Address unavailable"
        )

        distance = calculate_distance(
            latitude,
            longitude,
            place_lat,
            place_lon
        )

        categories = properties.get(
            "categories",
            []
        )

        if distance > radius:
            continue

        if "catering.restaurant" in categories:
            detected_type = "restaurant"
        elif "catering.cafe" in categories:
            detected_type = "cafe"
        else:
            detected_type = place_type

        places.append({
            "name": name,
            "address": properties.get(
                "formatted",
                "Address unavailable"
            ),
            "type": detected_type,
            "latitude": place_lat,
            "longitude": place_lon,
            "distance_meters": round(distance),
            "place_id": properties.get(
                "place_id"
            ),
            "source": "Geoapify / OpenStreetMap"
        })

    return places


def calculate_distance(
    lat1,
    lon1,
    lat2,
    lon2
):

    earth_radius = 6371000

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    delta_lat = math.radians(
        lat2 - lat1
    )

    delta_lon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(delta_lat / 2) ** 2
        +
        math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius * c

def get_requested_category(place_type: str) -> str:
    category_map = {
        "restaurant": "catering.restaurant",
        "cafe": "catering.cafe",
        "hotel": "accommodation.hotel",
    }

    return category_map.get(
        place_type,
        ""
    )


def is_valid_place_type(
    properties: dict,
    place_type: str
) -> bool:

    categories = properties.get(
        "categories",
        []
    )

    if not categories:
        return False

    requested_category = get_requested_category(
        place_type
    )

    return any(
        category == requested_category
        or category.startswith(
            requested_category + "."
        )
        for category in categories
    )
