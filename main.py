import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId

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


# Riders
@app.post("/riders", response_model=IdResponse)
async def create_rider(rider: Rider):
    new_id = create_document("rider", rider)
    return {"id": new_id}


@app.get("/riders")
async def list_riders():
    docs = get_documents("rider")
    return [to_str_id(d) for d in docs]


# Drivers
@app.post("/drivers", response_model=IdResponse)
async def create_driver(driver: Driver):
    new_id = create_document("driver", driver)
    return {"id": new_id}


@app.get("/drivers")
async def list_drivers():
    docs = get_documents("driver")
    return [to_str_id(d) for d in docs]


# Rides
@app.post("/rides", response_model=IdResponse)
async def request_ride(ride: Ride):
    # Basic fare estimate if not provided
    if ride.fare_estimate is None and ride.distance_km is not None:
        ride.fare_estimate = round(2.0 + 1.2 * ride.distance_km, 2)
    new_id = create_document("ride", ride)
    return {"id": new_id}


@app.get("/rides")
async def list_rides(status: Optional[str] = None):
    filter_dict = {"status": status} if status else {}
    docs = get_documents("ride", filter_dict=filter_dict)
    return [to_str_id(d) for d in docs]


@app.get("/rides/{ride_id}")
async def get_ride(ride_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        doc = db["ride"].find_one({"_id": ObjectId(ride_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Ride not found")
        return to_str_id(doc)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ride id")


class RideUpdate(BaseModel):
    status: Optional[str] = None
    driver_id: Optional[str] = None


@app.patch("/rides/{ride_id}")
async def update_ride(ride_id: str, payload: RideUpdate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    update = {k: v for k, v in payload.dict().items() if v is not None}
    if not update:
        return {"updated": False}
    update["updated_at"] = __import__("datetime").datetime.utcnow()
    try:
        res = db["ride"].update_one({"_id": ObjectId(ride_id)}, {"$set": update})
        return {"updated": res.modified_count > 0}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ride id")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
