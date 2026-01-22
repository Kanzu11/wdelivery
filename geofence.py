from config import MIN_LAT, MAX_LAT, MIN_LON, MAX_LON

def in_werabe(lat, lon):
    return MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lon <= MAX_LON
