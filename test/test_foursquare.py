import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("FOURSQUARE_API_KEY")

if not api_key:
    raise ValueError(
        "FOURSQUARE_API_KEY not found in .env"
    )

url = "https://places-api.foursquare.com/places/search"

headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {api_key}",
    "X-Places-Api-Version": "2025-06-17"
}

params = {
    "query": "restaurants",
    "near": "Peelamedu, Coimbatore",
    "limit": 5,
    "fields": (
        "fsq_id,"
        "name,"
        "location,"
        "categories,"
        "rating"
    )
}

response = requests.get(
    url,
    headers=headers,
    params=params,
    timeout=30
)

print("Status:", response.status_code)

if response.status_code != 200:
    print("Error:")
    print(response.text)
else:
    data = response.json()

    print("\nLive Places:\n")

    for place in data.get("results", []):
        print("Name:", place.get("name"))

        location = place.get("location", {})

        print(
            "Address:",
            location.get(
                "formatted_address",
                "Address unavailable"
            )
        )

        print(
            "Rating:",
            place.get(
                "rating",
                "Not available"
            )
        )

        print(
            "Place ID:",
            place.get("fsq_id")
        )

        print("-" * 40)