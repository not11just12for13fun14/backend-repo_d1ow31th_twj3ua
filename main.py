import os
import secrets
from datetime import datetime
from typing import Optional, List, Dict, Any

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

from database import db, create_document, get_documents
from schemas import Rider, Driver, Ride

app = FastAPI(title="Payana API", description="Ride booking backend for Payana")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class IdKeyResponse(BaseModel):
    id: str
    api_key: Optional[str] = None


class IdResponse(BaseModel):
    id: str


# Utility

def to_str_id(doc):
    if doc is None:
        return None
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d


def get_doc_by_id(collection: str, _id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        doc = db[collection].find_one({"_id": ObjectId(_id)})
        if not doc:
            raise HTTPException(status_code=404, detail=f"{collection} not found")
        return doc
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


@app.get("/")
def read_root():
    return {"message": "Payana backend is running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, "name") else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    return response


# Pricing and surge logic

def surge_multiplier(hour: Optional[int] = None) -> float:
    h = hour if hour is not None else datetime.utcnow().hour
    # Peak hours 7-9 and 17-20 UTC-based
    if 7 <= h <= 9 or 17 <= h <= 20:
        return 1.5
    # Late night slight surge
    if 22 <= h or h < 6:
        return 1.2
    return 1.0


def estimate_fare(distance_km: float, duration_min: Optional[float] = None, hour: Optional[int] = None) -> tuple[float, float]:
    base = 2.0
    per_km = 1.2
    per_min = 0.2
    mult = surge_multiplier(hour)
    fare = base + per_km * max(distance_km, 0)
    if duration_min is not None:
        fare += per_min * max(duration_min, 0)
    return round(fare * mult, 2), mult


class FareQuery(BaseModel):
    distance_km: float
    duration_min: Optional[float] = None
    hour: Optional[int] = None


@app.post("/pricing/estimate")
def pricing_estimate(payload: FareQuery):
    price, mult = estimate_fare(payload.distance_km, payload.duration_min, payload.hour)
    return {"fare": price, "surge_multiplier": mult}


# Geocoding (Nominatim) and Routing (OSRM)

BBOX = {
    "minLat": 12.80,
    "maxLat": 13.20,
    "minLng": 77.3,
    "maxLng": 77.85,
}


@app.get("/geo/search")
def geocode_search(q: str = Query(..., min_length=2), limit: int = 5):
    """Proxy to OpenStreetMap Nominatim search with Bengaluru bbox constraints."""
    try:
        params = {
            "q": q,
            "format": "json",
            "limit": max(1, min(limit, 10)),
            "addressdetails": 1,
            "viewbox": f"{BBOX['minLng']},{BBOX['maxLat']},{BBOX['maxLng']},{BBOX['minLat']}",
            "bounded": 1,
        }
        headers = {"User-Agent": "Payana/1.0 (demo)"}
        r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=10)
        r.raise_for_status()
        results = r.json()
        items: List[Dict[str, Any]] = []
        for it in results:
            try:
                items.append({
                    "display_name": it.get("display_name"),
                    "lat": float(it.get("lat")),
                    "lng": float(it.get("lon")),
                    "type": it.get("type"),
                })
            except Exception:
                continue
        return {"results": items[:limit]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Geocoding error: {str(e)[:120]}")


@app.get("/route")
def route(from_lat: float, from_lng: float, to_lat: float, to_lng: float):
    """Route via public OSRM server. Returns GeoJSON line, distance (km), duration (min)."""
    try:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{from_lng},{from_lat};{to_lng},{to_lat}"
        )
        params = {
            "overview": "full",
            "geometries": "geojson",
            "alternatives": "false",
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        routes = data.get("routes", [])
        if not routes:
            raise HTTPException(status_code=404, detail="No route found")
        best = routes[0]
        distance_km = round(best.get("distance", 0) / 1000.0, 3)
        duration_min = round(best.get("duration", 0) / 60.0, 1)
        geometry = best.get("geometry", {})
        coords: List[List[float]] = geometry.get("coordinates", [])
        # Ensure lat/lng ordering for frontend convenience
        latlngs = [{"lat": c[1], "lng": c[0]} for c in coords]
        return {
            "distance_km": distance_km,
            "duration_min": duration_min,
            "path": latlngs,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Routing error: {str(e)[:120]}")


# Riders
@app.post("/riders", response_model=IdKeyResponse)
async def create_rider(rider: Rider):
    # generate api key for rider if not provided
    api_key = rider.api_key or secrets.token_hex(16)
    data = rider.dict()
    data["api_key"] = api_key
    new_id = create_document("rider", data)
    return {"id": new_id, "api_key": api_key}


@app.get("/riders")
async def list_riders():
    docs = get_documents("rider")
    return [to_str_id(d) for d in docs]


# Drivers
@app.post("/drivers", response_model=IdKeyResponse)
async def create_driver(driver: Driver):
    api_key = driver.api_key or secrets.token_hex(16)
    data = driver.dict()
    data["api_key"] = api_key
    new_id = create_document("driver", data)
    return {"id": new_id, "api_key": api_key}


@app.get("/drivers")
async def list_drivers():
    docs = get_documents("driver")
    return [to_str_id(d) for d in docs]


class LocationUpdate(BaseModel):
    lat: float
    lng: float


@app.patch("/drivers/{driver_id}/location")
async def update_driver_location(driver_id: str, loc: LocationUpdate, x_api_key: Optional[str] = Header(None)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    # Auth: require correct API key for this driver
    doc = get_doc_by_id("driver", driver_id)
    if doc.get("api_key") != x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    res = db["driver"].update_one({"_id": ObjectId(driver_id)}, {"$set": {"location": {"lat": loc.lat, "lng": loc.lng}, "updated_at": datetime.utcnow()}})
    return {"updated": res.modified_count > 0}


@app.get("/drivers/nearby")
async def nearby_drivers(lat: float, lng: float, radius_km: float = 5.0):
    # Simple bounding box filter (not precise great-circle)
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    km_per_deg_lat = 110.574
    km_per_deg_lng = 111.320
    dlat = radius_km / km_per_deg_lat
    dlng = radius_km / km_per_deg_lng
    query = {
        "location.lat": {"$gte": lat - dlat, "$lte": lat + dlat},
        "location.lng": {"$gte": lng - dlng, "$lte": lng + dlng},
        "is_available": True,
    }
    docs = list(db["driver"].find(query).limit(50))
    return [to_str_id(d) for d in docs]


# Rides
@app.post("/rides", response_model=IdResponse)
async def request_ride(ride: Ride, x_api_key: Optional[str] = Header(None)):
    # Auth: verify rider api key
    rider_doc = get_doc_by_id("rider", ride.rider_id)
    if rider_doc.get("api_key") != x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    # Fare estimate with surge if not provided
    if ride.fare_estimate is None and ride.distance_km is not None:
        fare, mult = estimate_fare(ride.distance_km, ride.duration_min)
        ride.fare_estimate = fare
        ride.surge_multiplier = mult
    new_id = create_document("ride", ride)
    return {"id": new_id}


@app.get("/rides")
async def list_rides(status: Optional[str] = None):
    filter_dict = {"status": status} if status else {}
    docs = get_documents("ride", filter_dict=filter_dict)
    return [to_str_id(d) for d in docs]


@app.get("/rides/{ride_id}")
async def get_ride(ride_id: str):
    doc = get_doc_by_id("ride", ride_id)
    return to_str_id(doc)


class RideUpdate(BaseModel):
    status: Optional[str] = None
    driver_id: Optional[str] = None


@app.patch("/rides/{ride_id}")
async def update_ride(ride_id: str, payload: RideUpdate, x_api_key: Optional[str] = Header(None)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    update = {k: v for k, v in payload.dict().items() if v is not None}
    if not update:
        return {"updated": False}

    # If driver is assigning themselves or updating status, require driver key when driver_id present on ride
    ride_doc = get_doc_by_id("ride", ride_id)
    if payload.driver_id:
        # Assigning a driver requires that driver's key
        driver_doc = get_doc_by_id("driver", payload.driver_id)
        if driver_doc.get("api_key") != x_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key for driver")
        update["status"] = update.get("status", "assigned")
    else:
        # If ride already has driver, require that driver's key for status progress except rider can cancel
        if ride_doc.get("driver_id"):
            driver_doc = get_doc_by_id("driver", ride_doc["driver_id"])
            if update.get("status") == "cancelled":
                # allow rider to cancel with rider key
                rider_doc = get_doc_by_id("rider", ride_doc["rider_id"])
                if rider_doc.get("api_key") != x_api_key and driver_doc.get("api_key") != x_api_key:
                    raise HTTPException(status_code=401, detail="Invalid API key")
            else:
                if driver_doc.get("api_key") != x_api_key:
                    raise HTTPException(status_code=401, detail="Driver API key required")
        else:
            # no driver yet -> allow rider to update/cancel
            rider_doc = get_doc_by_id("rider", ride_doc["rider_id"])
            if rider_doc.get("api_key") != x_api_key:
                raise HTTPException(status_code=401, detail="Rider API key required")

    update["updated_at"] = datetime.utcnow()
    try:
        res = db["ride"].update_one({"_id": ObjectId(ride_id)}, {"$set": update})
        return {"updated": res.modified_count > 0}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ride id")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
