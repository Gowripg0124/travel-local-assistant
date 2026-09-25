from services.query_analyzer import analyze_query
from services.geocoding_service import geocode_location
from services.geoapify_service import search_geoapify_places

import requests


def get_place_type(intents):
    if "restaurant" in intents:
        return "restaurant"

    if "cafe" in intents:
        return "cafe"

    if "hotel" in intents:
        return "hotel"

    return None


def search_places(question, history=None, max_results=5):

    analysis = analyze_query(
        question,
        history
    )

    intents = analysis["intents"]
    location = analysis["location"]
    parent_location = analysis.get("parent_location")

    # --------------------------------
    # 1. Validate location
    # --------------------------------

    if not location:
        return {
            "places": [],
            "location": None,
            "coordinates": None,
            "error": (
                "I need a location to search for nearby places. "
                "Please mention a city or locality."
            )
        }

    # --------------------------------
    # 2. Detect place type
    # --------------------------------

    place_type = get_place_type(intents)

    if not place_type:
        return {
            "places": [],
            "location": location,
            "coordinates": None,
            "error": (
                "I couldn't determine what type of place "
                "you're looking for."
            )
        }

    # --------------------------------
    # 3. Build location with city context
    # --------------------------------

    geocoding_location = location

    if parent_location:
        location_lower = location.lower().strip()
        parent_lower = parent_location.lower().strip()

        if parent_lower not in location_lower:
            geocoding_location = (
                f"{location}, {parent_location}"
            )

    print("\n==============================")
    print("PLACE SEARCH")
    print("==============================")
    print("Question:", question)
    print("Intents:", intents)
    print("Location:", location)
    print(
        "Geocoding location:",
        geocoding_location
    )
    print("Place type:", place_type)

    # --------------------------------
    # 4. Geocode location
    # --------------------------------

    try:

        coordinates = geocode_location(
            geocoding_location
        )

    except requests.exceptions.RequestException as e:

        print("Geocoding error:", e)

        return {
            "places": [],
            "location": location,
            "coordinates": None,
            "error": (
                "I couldn't reach the location service "
                "right now. Please try again later."
            )
        }

    except Exception as e:

        print("Unexpected geocoding error:", e)

        return {
            "places": [],
            "location": location,
            "coordinates": None,
            "error": (
                "Something went wrong while finding "
                "that location."
            )
        }

    # --------------------------------
    # 5. Invalid location
    # --------------------------------

    if not coordinates:

        return {
            "places": [],
            "location": location,
            "coordinates": None,
            "error": (
                f"I couldn't find the location "
                f"'{location}'. Please try another "
                "locality or city."
            )
        }

    print(
        "Coordinates:",
        coordinates["latitude"],
        coordinates["longitude"]
    )

    # --------------------------------
    # 6. Search Geoapify
    # --------------------------------

    try:

        places = search_geoapify_places(
            latitude=coordinates["latitude"],
            longitude=coordinates["longitude"],
            place_type=place_type,
            radius=5000,
            max_results=max_results
        )

    except requests.exceptions.RequestException as e:

        print("Geoapify error:", e)

        return {
            "places": [],
            "location": location,
            "coordinates": coordinates,
            "error": (
                "The live places service is temporarily "
                "unavailable. Please try again later."
            )
        }

    except Exception as e:

        print("Unexpected places error:", e)

        return {
            "places": [],
            "location": location,
            "coordinates": coordinates,
            "error": (
                "Something went wrong while searching "
                "for nearby places."
            )
        }

    print(
        f"Found {len(places)} valid places."
    )

    # --------------------------------
    # 7. No places found
    # --------------------------------

    if not places:

        return {
            "places": [],
            "location": location,
            "coordinates": coordinates,
            "error": (
                f"I couldn't find any {place_type}s "
                f"near {location}."
            )
        }

    # --------------------------------
    # 8. Successful result
    # --------------------------------

    return {
        "places": places,
        "location": location,
        "coordinates": coordinates,
        "error": None
    }