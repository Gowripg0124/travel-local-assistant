from services.geocoding_service import (
    geocode_location
)

from services.geoapify_service import (
    search_geoapify_places
)


location = "Theni"

coordinates = geocode_location(
    location
)

print("\nLOCATION:")
print(coordinates)


places = search_geoapify_places(
    latitude=coordinates["latitude"],
    longitude=coordinates["longitude"],
    place_type="restaurant",
    radius=10000,
    max_results=20
)


print("\nRESTAURANTS:\n")

for place in places:

    print(
        "Name:",
        place["name"]
    )

    print(
        "Address:",
        place["address"]
    )

    print(
        "Distance:",
        place["distance_meters"],
        "meters"
    )

    print(
        "Latitude:",
        place["latitude"]
    )

    print(
        "Longitude:",
        place["longitude"]
    )

    print(
        "Place ID:",
        place["place_id"]
    )

    print("-" * 50)