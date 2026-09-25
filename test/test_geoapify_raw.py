import os
import requests

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEOAPIFY_API_KEY")

url = "https://api.geoapify.com/v2/places"

params = {
    "categories": "catering",
    "filter": "circle:77.4222974,9.8692558,10000",
    "limit": 20,
    "apiKey": API_KEY
}

response = requests.get(
    url,
    params=params,
    timeout=20
)

print("STATUS:", response.status_code)

response.raise_for_status()

data = response.json()

print(
    "TOTAL FEATURES:",
    len(data.get("features", []))
)

for feature in data.get(
    "features",
    []
):

    properties = feature.get(
        "properties",
        {}
    )

    print(
        "\nNAME:",
        properties.get("name")
    )

    print(
        "CATEGORY:",
        properties.get("categories")
    )

    print(
        "ADDRESS:",
        properties.get("formatted")
    )

    print(
        "DISTANCE:",
        properties.get("distance")
    )