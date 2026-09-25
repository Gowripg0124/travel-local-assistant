import requests

from services.geocoding_service import geocode_location


OVERPASS_URL = (
    "https://overpass-api.de/api/interpreter"
)

HEADERS = {
    "User-Agent": (
        "TravelLocalAssistant/1.0 "
        "(local development project)"
    )
}


def search_restaurants(location: str):

    coordinates = geocode_location(location)

    if not coordinates:
        print(
            f"Could not find location: {location}"
        )
        return

    latitude = coordinates["latitude"]
    longitude = coordinates["longitude"]

    print(
        f"Location: {coordinates['display_name']}"
    )

    print(
        f"Coordinates: "
        f"{latitude}, {longitude}"
    )

    query = f"""
    [out:json][timeout:8];

    node["amenity"="restaurant"]
    (around:1000,{latitude},{longitude});

    out;
    """

    response = requests.post(
        OVERPASS_URL,
        data=query,
        headers=HEADERS,
        timeout=15
    )

    print(
        "Overpass status:",
        response.status_code
    )

    response.raise_for_status()

    data = response.json()

    places = data.get(
        "elements",
        []
    )

    print(
        f"\nFound {len(places)} restaurants\n"
    )

    for place in places[:10]:

        tags = place.get(
            "tags",
            {}
        )

        name = tags.get(
            "name",
            "Unnamed restaurant"
        )

        print(
            "Name:",
            name
        )

        print(
            "Latitude:",
            place.get(
                "lat",
                place.get(
                    "center",
                    {}
                ).get("lat")
            )
        )

        print(
            "Longitude:",
            place.get(
                "lon",
                place.get(
                    "center",
                    {}
                ).get("lon")
            )
        )

        print("-" * 40)


if __name__ == "__main__":

    search_restaurants(
        "Peelamedu, Coimbatore"
    )