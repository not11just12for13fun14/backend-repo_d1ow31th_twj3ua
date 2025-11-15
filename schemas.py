"""
Database Schemas for Payana (Ride Booking)

Each Pydantic model represents a collection in MongoDB.
Collection name is the lowercase of the class name.

- Rider -> rider
- Driver -> driver
- Ride -> ride
"""

from pydantic import BaseModel, Field
from typing import Optional


class GeoPoint(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lng: float = Field(..., ge=-180, le=180, description="Longitude")


class Rider(BaseModel):
    name: str = Field(..., description="Full name")
    phone: str = Field(..., description="Phone number")
    rating: Optional[float] = Field(5.0, ge=0, le=5)
    api_key: Optional[str] = None


class Vehicle(BaseModel):
    make: str
    model: str
    plate: str
    color: Optional[str] = None


class Driver(BaseModel):
    name: str
    phone: str
    vehicle: Vehicle
    location: Optional[GeoPoint] = None
    is_available: bool = True
    rating: Optional[float] = Field(5.0, ge=0, le=5)
    api_key: Optional[str] = None


class Ride(BaseModel):
    rider_id: str
    driver_id: Optional[str] = None
    pickup: GeoPoint
    dropoff: GeoPoint
    distance_km: Optional[float] = None
    duration_min: Optional[float] = None
    fare_estimate: Optional[float] = None
    surge_multiplier: Optional[float] = None
    status: str = Field(
        "requested",
        description="Ride status: requested|assigned|ongoing|completed|cancelled",
    )
