from services.geocoding_service import (
    geocode_location
)


result = geocode_location(
    "Peelamedu, Coimbatore"
)

print(result)