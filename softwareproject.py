from __future__ import annotations

import csv
import io
import json
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote
from urllib.request import urlopen

from flask import Flask, Response, render_template_string, request

try:
    import pandas as pd
except ImportError:
    pd = None


app = Flask(__name__)

NHTSA_VIN_API = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"

FUEL_TYPES = ["Petrol", "Diesel", "Hybrid", "Electric"]

SERVICE_INTERVALS = {
    "Oil Change": {"interval_km": 15000, "estimated_cost": 120},
    "Oil Filter": {"interval_km": 15000, "estimated_cost": 35},
    "Air Filter": {"interval_km": 30000, "estimated_cost": 50},
    "Cabin Filter": {"interval_km": 20000, "estimated_cost": 45},
    "Brake Fluid": {"interval_km": 30000, "estimated_cost": 130},
    "Front Brakes": {"interval_km": 40000, "estimated_cost": 320},
    "Tires": {"interval_km": 45000, "estimated_cost": 650},
}

OIL_RECOMMENDATIONS = {
    "Petrol": "5W-30 synthetic oil",
    "Diesel": "5W-30 synthetic diesel oil",
    "Hybrid": "0W-20 hybrid engine oil",
    "Electric": "No engine oil required",
}


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def normalize_brand(brand: str) -> str:
    brand = (brand or "").strip()

    mapping = {
        "VW": "Volkswagen",
        "VOLKSWAGEN": "Volkswagen",
        "TOYOTA": "Toyota",
        "MERCEDES": "Mercedes-Benz",
        "MERCEDES-BENZ": "Mercedes-Benz",
        "BMW": "BMW",
        "AUDI": "Audi",
        "FORD": "Ford",
        "HONDA": "Honda",
    }

    return mapping.get(brand.upper(), brand.title() if brand else "Unknown")


def normalize_fuel_type(fuel_type: str) -> str:
    fuel_type = (fuel_type or "Petrol").strip().title()

    if fuel_type in FUEL_TYPES:
        return fuel_type

    if fuel_type in ["Gas", "Gasoline"]:
        return "Petrol"

    return "Petrol"


def make_demo_vin(index: int) -> str:
    return f"DEMOFLEET{index:08d}"[:17]


def vin_model_year(vin: str) -> Optional[int]:
    year_codes = {
        "A": 2010,
        "B": 2011,
        "C": 2012,
        "D": 2013,
        "E": 2014,
        "F": 2015,
        "G": 2016,
        "H": 2017,
        "J": 2018,
        "K": 2019,
        "L": 2020,
        "M": 2021,
        "N": 2022,
        "P": 2023,
        "R": 2024,
        "S": 2025,
        "T": 2026,
    }

    if len(vin) != 17:
        return None

    return year_codes.get(vin[9])


def fallback_vin_decode(vin: str, model_year: Optional[int]) -> Optional[Dict[str, str]]:
    wmi_map = {
        "WBA": "BMW",
        "WBS": "BMW",
        "WVW": "Volkswagen",
        "WDD": "Mercedes-Benz",
        "WDB": "Mercedes-Benz",
        "JTD": "Toyota",
        "JHM": "Honda",
    }

    brand = None

    for prefix, mapped_brand in wmi_map.items():
        if vin.startswith(prefix):
            brand = mapped_brand
            break

    if not brand:
        return None

    return {
        "vin": vin,
        "brand": brand,
        "model": f"Unknown {brand} Model",
        "year": str(model_year or datetime.now().year),
        "engine": "",
        "fuel_type": "Petrol",
    }


def decode_vin(vin: str) -> Optional[Dict[str, str]]:
    vin = (vin or "").strip().upper().replace(" ", "")

    if len(vin) != 17:
        return None

    model_year = vin_model_year(vin)

    url = NHTSA_VIN_API.format(vin=quote(vin))

    try:
        with urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        results = payload.get("Results", [])

        if not results:
            return fallback_vin_decode(vin, model_year)

        item = results[0]

        make = normalize_brand(item.get("Make", "") or "")
        model = item.get("Model", "") or ""
        year = item.get("ModelYear", "") or str(model_year or datetime.now().year)
        engine = item.get("EngineModel", "") or item.get("DisplacementL", "") or ""

        fuel_map = {
            "Gasoline": "Petrol",
            "Diesel": "Diesel",
            "Hybrid": "Hybrid",
            "Electric": "Electric",
        }

        fuel_type = fuel_map.get(item.get("FuelTypePrimary", ""), "Petrol")

        if make == "Unknown" or not make:
            fallback = fallback_vin_decode(vin, model_year)
            if fallback:
                return fallback

        if not model:
            model = f"Unknown {make} Model"

        return {
            "vin": vin,
            "brand": make,
            "model": model,
            "year": str(year),
            "engine": str(engine),
            "fuel_type": fuel_type,
        }

    except Exception:
        return fallback_vin_decode(vin, model_year)


@dataclass
class ServiceRecord:
    service_type: str
    cost: float
    mileage_km: float
    date: str = field(default_factory=today)
    notes: str = ""


@dataclass
class FuelRecord:
    liters: float
    total_price: float
    mileage_km: float
    date: str = field(default_factory=today)


@dataclass
class Vehicle:
    vin: str
    brand: str
    model: str
    year: int
    mileage_km: float
    fuel_type: str
    engine: str = ""
    service_records: List[ServiceRecord] = field(default_factory=list)
    fuel_records: List[FuelRecord] = field(default_factory=list)

    def name(self) -> str:
        return f"{self.brand} {self.model}"

    def short_vin(self) -> str:
        return self.vin if len(self.vin) <= 10 else f"{self.vin[:7]}...{self.vin[-4:]}"

    def age(self) -> int:
        return max(0, datetime.now().year - self.year)

    def recommended_oil(self) -> str:
        return OIL_RECOMMENDATIONS.get(
            self.fuel_type,
            "Check manufacturer specification"
        )

    def total_service_cost(self) -> float:
        return round(
            sum(record.cost for record in self.service_records),
            2
        )

    def total_fuel_cost(self) -> float:
        return round(
            sum(record.total_price for record in self.fuel_records),
            2
        )

    def total_cost(self) -> float:
        return round(
            self.total_service_cost() + self.total_fuel_cost(),
            2
        )

    def fuel_consumption_l_100km(self) -> Optional[float]:
        if self.fuel_type == "Electric" or len(self.fuel_records) < 2:
            return None

        records = sorted(
            self.fuel_records,
            key=lambda record: record.mileage_km
        )

        distance = records[-1].mileage_km - records[0].mileage_km

        if distance <= 0:
            return None

        liters = sum(record.liters for record in records[1:])

        return round((liters / distance) * 100, 2)

    def latest_service_km(self, service_type: str) -> Optional[float]:
        values = [
            record.mileage_km
            for record in self.service_records
            if record.service_type == service_type
        ]

        return max(values) if values else None
    def maintenance_plan(self) -> List[Dict]:
        rows = []

        for service_type, data in SERVICE_INTERVALS.items():
            interval = data["interval_km"]
            latest_km = self.latest_service_km(service_type)

            if latest_km is None:
                next_due = self.mileage_km + interval
                km_left = interval
                status = "No record"
            else:
                next_due = latest_km + interval
                km_left = next_due - self.mileage_km

                if km_left < 0:
                    status = "Overdue"
                elif km_left <= 1500:
                    status = "Due soon"
                else:
                    status = "OK"

            rows.append({
                "service_type": service_type,
                "latest_km": latest_km,
                "next_due": round(next_due),
                "km_left": round(km_left),
                "status": status,
                "estimated_cost": data["estimated_cost"],
            })

        return sorted(rows, key=lambda row: row["km_left"])

    def overdue_count(self) -> int:
        return sum(1 for row in self.maintenance_plan() if row["status"] == "Overdue")

    def due_soon_count(self) -> int:
        return sum(1 for row in self.maintenance_plan() if row["status"] == "Due soon")

    def next_12_month_cost(self) -> float:
        total = 0

        for row in self.maintenance_plan():
            if row["status"] in ["Overdue", "Due soon", "No record"]:
                total += row["estimated_cost"]

        return round(total, 2)

    def risk_score(self) -> Dict:
        score = 0
        reasons = []

        if self.mileage_km > 180000:
            score += 30
            reasons.append("high mileage")
        elif self.mileage_km > 100000:
            score += 15
            reasons.append("medium mileage")

        if self.age() >= 10:
            score += 25
            reasons.append("older vehicle")
        elif self.age() >= 6:
            score += 12
            reasons.append("vehicle age")

        overdue = self.overdue_count()
        due_soon = self.due_soon_count()

        score += overdue * 18
        score += due_soon * 8

        if overdue:
            reasons.append(f"{overdue} overdue service item(s)")

        if due_soon:
            reasons.append(f"{due_soon} service item(s) due soon")

        consumption = self.fuel_consumption_l_100km()

        if consumption is not None and consumption > 8.5:
            score += 10
            reasons.append("high fuel consumption")

        score = min(score, 100)

        if score >= 70:
            level = "High"
        elif score >= 40:
            level = "Medium"
        else:
            level = "Low"

        return {
            "score": score,
            "level": level,
            "reasons": reasons or ["normal condition"],
        }

    def health_score(self) -> int:
        return max(0, 100 - self.risk_score()["score"])

    def health_status(self) -> str:
        score = self.health_score()

        if score >= 75:
            return "Healthy"

        if score >= 50:
            return "Needs Attention"

        return "Critical"

    def ai_recommendations(self) -> List[Dict]:
        recommendations = []

        for row in self.maintenance_plan():
            if row["status"] == "Overdue":
                recommendations.append({
                    "priority": "High",
                    "item": row["service_type"],
                    "message": (
                        f"{row['service_type']} is overdue by "
                        f"{abs(row['km_left'])} km. Replace or service it as soon as possible."
                    ),
                    "estimated_cost": row["estimated_cost"],
                })

            elif row["status"] == "Due soon":
                recommendations.append({
                    "priority": "Medium",
                    "item": row["service_type"],
                    "message": (
                        f"{row['service_type']} is due soon in "
                        f"{row['km_left']} km. Plan this service soon."
                    ),
                    "estimated_cost": row["estimated_cost"],
                })

            elif row["status"] == "No record":
                recommendations.append({
                    "priority": "Medium",
                    "item": row["service_type"],
                    "message": (
                        f"No record found for {row['service_type']}. "
                        "Add previous service data or inspect it."
                    ),
                    "estimated_cost": row["estimated_cost"],
                })

        consumption = self.fuel_consumption_l_100km()

        if consumption is not None and consumption > 8.5:
            recommendations.append({
                "priority": "Medium",
                "item": "Fuel Efficiency",
                "message": (
                    "Fuel consumption is high. Check tire pressure, air filter, "
                    "spark plugs/injectors and driving style."
                ),
                "estimated_cost": 50,
            })

        if self.fuel_type != "Electric" and self.latest_service_km("Oil Change") is None:
            recommendations.append({
                "priority": "High",
                "item": "Oil Change",
                "message": (
                    f"No oil change record found. Recommended oil: "
                    f"{self.recommended_oil()}."
                ),
                "estimated_cost": SERVICE_INTERVALS["Oil Change"]["estimated_cost"],
            })

        priority_order = {
            "High": 0,
            "Medium": 1,
            "Low": 2,
        }

        recommendations.sort(
            key=lambda recommendation: priority_order.get(recommendation["priority"], 9)
        )

        return recommendations[:7]

    def smart_tips(self) -> List[str]:
        tips = []

        if self.fuel_type != "Electric":
            tips.append(f"Recommended oil: {self.recommended_oil()}.")

        if self.overdue_count() > 0:
            tips.append(
                "Some maintenance items are overdue. "
                "Schedule service soon to avoid higher repair costs."
            )

        if self.due_soon_count() > 0:
            tips.append(
                "Some maintenance items are due soon. Plan service in advance."
            )

        consumption = self.fuel_consumption_l_100km()

        if consumption is not None and consumption > 8.5:
            tips.append(
                "Fuel consumption is high. Check tire pressure, air filter and driving style."
            )

        if self.latest_service_km("Oil Filter") is None:
            tips.append(
                "Oil filter should usually be replaced together with oil change."
            )

        if self.age() >= 10:
            tips.append(
                "Older vehicle detected. Preventive maintenance is recommended."
            )

        if not tips:
            tips.append(
                "Vehicle condition appears stable. Continue regular maintenance."
            )

        return tips


@dataclass
class FleetSystem:
    vehicles: List[Vehicle] = field(default_factory=list)

    def add_vehicle(self, vehicle: Vehicle) -> bool:
        if self.find_vehicle(vehicle.vin):
            return False

        self.vehicles.append(vehicle)
        return True

    def find_vehicle(self, vin: str) -> Optional[Vehicle]:
        vin = (vin or "").strip().upper()

        for vehicle in self.vehicles:
            if vehicle.vin == vin:
                return vehicle

        return None

    def clear(self) -> None:
        self.vehicles.clear()

    def metrics(self) -> Dict:
        total_service = round(
            sum(vehicle.total_service_cost() for vehicle in self.vehicles),
            2
        )

        total_fuel = round(
            sum(vehicle.total_fuel_cost() for vehicle in self.vehicles),
            2
        )

        total_cost = round(total_service + total_fuel, 2)

        risk_values = [
            vehicle.risk_score()["score"]
            for vehicle in self.vehicles
        ]

        average_risk = round(statistics.mean(risk_values), 1) if risk_values else 0
        fleet_health = max(0, round(100 - average_risk, 1))

        high_risk = sum(
            1 for vehicle in self.vehicles
            if vehicle.risk_score()["level"] == "High"
        )

        medium_risk = sum(
            1 for vehicle in self.vehicles
            if vehicle.risk_score()["level"] == "Medium"
        )

        low_risk = sum(
            1 for vehicle in self.vehicles
            if vehicle.risk_score()["level"] == "Low"
        )

        due_items = sum(
            1
            for vehicle in self.vehicles
            for row in vehicle.maintenance_plan()
            if row["status"] in ["Overdue", "Due soon"]
        )

        forecast_cost = round(
            sum(vehicle.next_12_month_cost() for vehicle in self.vehicles),
            2
        )

        return {
            "vehicle_count": len(self.vehicles),
            "total_service": total_service,
            "total_fuel": total_fuel,
            "total_cost": total_cost,
            "average_risk": average_risk,
            "fleet_health": fleet_health,
            "high_risk": high_risk,
            "medium_risk": medium_risk,
            "low_risk": low_risk,
            "due_items": due_items,
            "forecast_cost": forecast_cost,
        }

    def chart_data(self) -> Dict:
        return {
            "labels": [
                vehicle.name()
                for vehicle in self.vehicles
            ],
            "service_costs": [
                vehicle.total_service_cost()
                for vehicle in self.vehicles
            ],
            "fuel_costs": [
                vehicle.total_fuel_cost()
                for vehicle in self.vehicles
            ],
            "risk_scores": [
                vehicle.risk_score()["score"]
                for vehicle in self.vehicles
            ],
            "health_scores": [
                vehicle.health_score()
                for vehicle in self.vehicles
            ],
            "mileages": [
                vehicle.mileage_km
                for vehicle in self.vehicles
            ],
            "forecast_costs": [
                vehicle.next_12_month_cost()
                for vehicle in self.vehicles
            ],
            "risk_distribution": [
                self.metrics()["low_risk"],
                self.metrics()["medium_risk"],
                self.metrics()["high_risk"],
            ],
        }

    def export_rows(self) -> List[Dict]:
        rows = []

        for vehicle in self.vehicles:
            risk = vehicle.risk_score()

            rows.append({
                "vin": vehicle.vin,
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year,
                "mileage_km": vehicle.mileage_km,
                "fuel_type": vehicle.fuel_type,
                "engine": vehicle.engine,
                "recommended_oil": vehicle.recommended_oil(),
                "service_cost": vehicle.total_service_cost(),
                "fuel_cost": vehicle.total_fuel_cost(),
                "total_cost": vehicle.total_cost(),
                "forecast_12_month_cost": vehicle.next_12_month_cost(),
                "fuel_consumption_l_100km": vehicle.fuel_consumption_l_100km(),
                "health_score": vehicle.health_score(),
                "health_status": vehicle.health_status(),
                "risk_score": risk["score"],
                "risk_level": risk["level"],
            })

        return rows


fleet = FleetSystem()


def create_vehicle(
    vin: str,
    brand: str,
    model: str,
    year: int,
    mileage_km: float,
    fuel_type: str,
    engine: str = "",
) -> Vehicle:
    return Vehicle(
        vin=vin.strip().upper(),
        brand=normalize_brand(brand),
        model=(model or "Unknown").strip().title(),
        year=year,
        mileage_km=max(0, mileage_km),
        fuel_type=normalize_fuel_type(fuel_type),
        engine=(engine or "").strip(),
    )


def load_demo_fleet() -> None:
    fleet.clear()

    demo_rows = [
        ("Toyota", "Corolla", 2019, 82000, "Hybrid", "1.8 Hybrid"),
        ("Volkswagen", "Golf", 2020, 112000, "Diesel", "2.0 TDI"),
        ("Mercedes-Benz", "C220", 2017, 166000, "Diesel", "OM651"),
        ("BMW", "320d", 2018, 148000, "Diesel", "B47"),
        ("Audi", "A4", 2019, 97000, "Petrol", "2.0 TFSI"),
        ("Ford", "Focus", 2020, 74000, "Petrol", "1.5 EcoBoost"),
    ]

    for index, row in enumerate(demo_rows, start=1):
        brand, model, year, mileage, fuel_type, engine = row

        vehicle = create_vehicle(
            vin=make_demo_vin(index),
            brand=brand,
            model=model,
            year=year,
            mileage_km=mileage,
            fuel_type=fuel_type,
            engine=engine,
        )

        vehicle.service_records.append(
            ServiceRecord(
                "Oil Change",
                120,
                mileage - random.randint(4000, 18000),
                notes="Demo record",
            )
        )

        vehicle.service_records.append(
            ServiceRecord(
                "Oil Filter",
                35,
                mileage - random.randint(4000, 18000),
                notes="Demo record",
            )
        )

        vehicle.service_records.append(
            ServiceRecord(
                "Brake Fluid",
                130,
                mileage - random.randint(12000, 37000),
                notes="Demo record",
            )
        )

        start_km = mileage - 6500

        for fuel_index in range(5):
            km = start_km + fuel_index * 1200
            liters = random.uniform(35, 56)
            price = liters * random.uniform(2.4, 2.9)

            vehicle.fuel_records.append(
                FuelRecord(
                    liters=round(liters, 2),
                    total_price=round(price, 2),
                    mileage_km=km,
                )
            )

        fleet.add_vehicle(vehicle)


def import_csv_content(content: str) -> int:
    imported = 0

    if pd is not None:
        dataframe = pd.read_csv(io.StringIO(content))
        dataframe.columns = [
            str(column).strip().lower()
            for column in dataframe.columns
        ]
        rows = dataframe.to_dict(orient="records")
    else:
        reader = csv.DictReader(io.StringIO(content))
        rows = [
            {
                str(key).strip().lower(): value
                for key, value in row.items()
            }
            for row in reader
        ]

    for index, row in enumerate(rows, start=1):
        brand = (
            row.get("brand")
            or row.get("make")
            or row.get("manufacturer")
            or "Unknown"
        )

        model = (
            row.get("model")
            or row.get("vehicle_model")
            or "Unknown"
        )

        year = (
            row.get("year")
            or row.get("model_year")
            or datetime.now().year
        )

        mileage = (
            row.get("mileage_km")
            or row.get("mileage")
            or row.get("odometer")
            or 0
        )

        fuel_type = (
            row.get("fuel_type")
            or row.get("fuel")
            or "Petrol"
        )

        engine = (
            row.get("engine")
            or row.get("engine_type")
            or ""
        )

        vin = str(
            row.get("vin")
            or row.get("vehicle_id")
            or ""
        ).strip().upper()

        try:
            year = int(float(year))
            mileage = float(mileage)
        except (ValueError, TypeError):
            continue

        if not vin or len(vin) != 17:
            vin = make_demo_vin(len(fleet.vehicles) + index)

        vehicle = create_vehicle(
            vin=vin[:17],
            brand=str(brand),
            model=str(model),
            year=year,
            mileage_km=mileage,
            fuel_type=str(fuel_type),
            engine=str(engine),
        )

        if fleet.add_vehicle(vehicle):
            imported += 1

    return imported


def render_app(
    message: str = "",
    vin_preview: Optional[Dict] = None,
):
    return render_template_string(
        PAGE,
        fleet=fleet,
        metrics=fleet.metrics(),
        chart_data=fleet.chart_data(),
        service_types=list(SERVICE_INTERVALS.keys()),
        message=message,
        vin_preview=vin_preview,
        pandas_available=pd is not None,
    )

PAGE = """
<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Smart Vehicle Maintenance</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet"
          href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">

    <style>
        :root {
            --bg: #080b10;
            --panel: rgba(18, 26, 39, 0.86);
            --panel2: rgba(28, 39, 56, 0.92);
            --border: rgba(255,255,255,0.09);
            --text: #f3f4f6;
            --muted: #9ca3af;
            --red: #ff3b30;
            --orange: #f97316;
            --blue: #3b82f6;
            --green: #22c55e;
            --yellow: #facc15;
        }

        * { box-sizing: border-box; }

        html { scroll-behavior: smooth; }

        body {
            margin: 0;
            font-family: "Segoe UI", Arial, sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 20% 0%, rgba(255,59,48,0.22), transparent 32%),
                radial-gradient(circle at 90% 5%, rgba(59,130,246,0.18), transparent 30%),
                linear-gradient(180deg, #05070b 0%, #0b1020 100%);
        }

        .app {
            display: grid;
            grid-template-columns: 270px 1fr;
            min-height: 100vh;
        }

        .sidebar {
            position: sticky;
            top: 0;
            height: 100vh;
            padding: 26px 20px;
            background:
                linear-gradient(180deg, rgba(3,7,18,0.98), rgba(8,13,24,0.96)),
                repeating-linear-gradient(45deg, rgba(255,255,255,0.03) 0px, rgba(255,255,255,0.03) 2px, transparent 2px, transparent 12px);
            border-right: 1px solid var(--border);
        }

        .logo {
            font-size: 30px;
            font-weight: 950;
            letter-spacing: 1.5px;
            margin-bottom: 8px;
        }

        .logo span { color: var(--red); }

        .sidebar-subtitle {
            color: var(--muted);
            font-size: 13px;
            line-height: 1.5;
            margin-bottom: 28px;
        }

        .nav a {
            display: flex;
            align-items: center;
            gap: 12px;
            color: var(--text);
            text-decoration: none;
            padding: 13px 14px;
            margin-bottom: 9px;
            border-radius: 14px;
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(255,255,255,0.05);
            font-weight: 750;
            transition: 0.22s ease;
        }

        .nav a:hover {
            transform: translateX(5px);
            border-color: rgba(255,59,48,0.45);
            background: rgba(30, 41, 59, 0.9);
            box-shadow: 0 0 22px rgba(255,59,48,0.12);
        }

        .main {
            padding: 30px;
            max-width: 1550px;
            width: 100%;
        }

        .hero {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 20px;
            align-items: center;
            padding: 28px;
            margin-bottom: 24px;
            border-radius: 28px;
            background:
                linear-gradient(135deg, rgba(255,59,48,0.23), rgba(15,23,42,0.86)),
                repeating-linear-gradient(45deg, rgba(255,255,255,0.035) 0px, rgba(255,255,255,0.035) 2px, transparent 2px, transparent 12px);
            border: 1px solid var(--border);
            box-shadow: 0 18px 45px rgba(0,0,0,0.38);
        }

        .hero h1 {
            margin: 0 0 10px;
            font-size: 42px;
            line-height: 1.05;
        }

        .hero h1 span { color: var(--red); }

        .hero p {
            margin: 0;
            color: var(--muted);
            max-width: 820px;
            line-height: 1.6;
        }

        .system-status {
            padding: 14px 18px;
            border-radius: 999px;
            background: rgba(34,197,94,0.12);
            border: 1px solid rgba(34,197,94,0.35);
            color: #86efac;
            font-weight: 900;
            box-shadow: 0 0 22px rgba(34,197,94,0.18);
            white-space: nowrap;
        }

        .message {
            background: rgba(6, 95, 70, 0.85);
            border: 1px solid rgba(16,185,129,0.55);
            padding: 14px 16px;
            border-radius: 16px;
            margin-bottom: 22px;
            font-weight: 800;
        }

        .section {
            margin-bottom: 24px;
            scroll-margin-top: 20px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
            gap: 16px;
        }

        .grid2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }

        .grid3 {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
        }

        .card {
            position: relative;
            overflow: hidden;
            padding: 20px;
            border-radius: 24px;
            background: linear-gradient(180deg, var(--panel2), var(--panel));
            border: 1px solid var(--border);
            box-shadow: 0 14px 36px rgba(0,0,0,0.28);
            backdrop-filter: blur(16px);
            transition: 0.22s ease;
        }

        .card::before {
            content: "";
            position: absolute;
            inset: 0 0 auto 0;
            height: 3px;
            background: linear-gradient(90deg, var(--red), var(--orange), var(--blue));
        }

        .card:hover {
            transform: translateY(-3px);
            border-color: rgba(255,255,255,0.18);
        }

        .metric-label {
            color: var(--muted);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .metric {
            font-size: 32px;
            font-weight: 950;
            margin-top: 8px;
            color: #fff;
            text-shadow: 0 0 18px rgba(255,59,48,0.25);
        }

        .metric-icon {
            font-size: 22px;
            color: var(--red);
            margin-bottom: 10px;
        }

        h2 {
            margin-top: 0;
            margin-bottom: 14px;
        }

        h3 { margin-bottom: 8px; }

        form { display: grid; gap: 11px; }

        input, select, textarea, button {
            width: 100%;
            padding: 12px 13px;
            border-radius: 14px;
            border: 1px solid rgba(255,255,255,0.1);
            font-family: inherit;
        }

        input, select, textarea {
            background: rgba(2, 6, 23, 0.78);
            color: var(--text);
        }

        textarea {
            min-height: 72px;
            resize: vertical;
        }

        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: var(--red);
            box-shadow: 0 0 0 4px rgba(255,59,48,0.14);
        }

        button {
            background: linear-gradient(180deg, var(--red), #b91c1c);
            color: white;
            border: none;
            font-weight: 950;
            cursor: pointer;
            transition: 0.2s;
        }

        button:hover {
            filter: brightness(1.12);
            transform: translateY(-1px);
        }

        .button-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }

        .green { background: linear-gradient(180deg, var(--green), #15803d); }
        .red { background: linear-gradient(180deg, var(--red), #991b1b); }
        .gray { background: linear-gradient(180deg, #64748b, #334155); }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
        }

        th, td {
            border-bottom: 1px solid rgba(255,255,255,0.08);
            padding: 11px;
            text-align: left;
            font-size: 14px;
            vertical-align: top;
        }

        th {
            color: #bfdbfe;
            text-transform: uppercase;
            font-size: 12px;
            letter-spacing: 0.5px;
        }

        tr:hover { background: rgba(255,255,255,0.035); }

        .muted {
            color: var(--muted);
            font-size: 13px;
            line-height: 1.45;
        }

        .empty {
            color: var(--muted);
            padding: 16px 0;
        }

        .vin {
            font-family: Consolas, monospace;
            color: #cbd5e1;
        }

        .pill {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 900;
        }

        .low {
            background: rgba(34, 197, 94, 0.18);
            color: #86efac;
        }

        .medium {
            background: rgba(245, 158, 11, 0.18);
            color: #fcd34d;
        }

        .high {
            background: rgba(239, 68, 68, 0.18);
            color: #fca5a5;
        }

        .health-good { color: #86efac; font-weight: 900; }
        .health-warning { color: #fcd34d; font-weight: 900; }
        .health-danger { color: #fca5a5; font-weight: 900; }

        .status-ok { color: #86efac; font-weight: 800; }
        .status-warning { color: #fcd34d; font-weight: 800; }
        .status-danger { color: #fca5a5; font-weight: 800; }

        .health-bar {
            width: 100%;
            height: 12px;
            background: #1e293b;
            border-radius: 999px;
            overflow: hidden;
            margin-top: 7px;
        }

        .health-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--red), var(--yellow), var(--green));
            border-radius: 999px;
        }

        .tip-list {
            margin-top: 10px;
            padding-left: 20px;
        }

        .tip-list li {
            margin-bottom: 6px;
            color: #dbeafe;
        }

        .ai-box {
            margin-top: 14px;
            border: 1px solid rgba(59,130,246,0.35);
            background: linear-gradient(180deg, rgba(30,64,175,0.22), rgba(15,23,42,0.4));
            border-radius: 18px;
            padding: 14px;
        }

        .ai-title {
            font-weight: 900;
            color: #bfdbfe;
            margin-bottom: 8px;
        }

        @media (max-width: 1000px) {
            .app { grid-template-columns: 1fr; }
            .sidebar { position: relative; height: auto; }
            .grid2, .grid3 { grid-template-columns: 1fr; }
            .hero { grid-template-columns: 1fr; }
            .hero h1 { font-size: 30px; }
        }
    </style>
</head>

<body>
<div class="app">

    <aside class="sidebar">
        <div class="logo">SMART<span>DRIVE</span></div>
        <div class="sidebar-subtitle">
            AI-style garage assistant for vehicle health, service history, oil/filter tracking and maintenance forecasting.
        </div>

        <nav class="nav">
            <a href="#dashboard"><i class="fa-solid fa-gauge-high"></i> Dashboard</a>
            <a href="#data"><i class="fa-solid fa-database"></i> Data & VIN</a>
            <a href="#add"><i class="fa-solid fa-car-side"></i> Add Vehicle</a>
            <a href="#logs"><i class="fa-solid fa-screwdriver-wrench"></i> Service & Fuel</a>
            <a href="#analytics"><i class="fa-solid fa-chart-line"></i> Analytics</a>
            <a href="#vehicles"><i class="fa-solid fa-warehouse"></i> Vehicles</a>
            <a href="#maintenance"><i class="fa-solid fa-robot"></i> AI Mechanic</a>
        </nav>
    </aside>

    <main class="main">

        <section class="hero">
            <div>
                <h1>Smart<span>Vehicle</span><br>Maintenance</h1>
                <p>
                    A modern vehicle maintenance assistant that creates digital vehicle records,
                    tracks oil, filters, fuel and service history, then generates AI-style mechanic recommendations.
                </p>
            </div>
            <div class="system-status">
                <i class="fa-solid fa-circle"></i> SYSTEM ONLINE
            </div>
        </section>

        {% if message %}
            <div class="message">{{ message }}</div>
        {% endif %}

        <section id="dashboard" class="section">
            <div class="grid">
                <div class="card">
                    <div class="metric-icon"><i class="fa-solid fa-car"></i></div>
                    <div class="metric-label">Vehicles</div>
                    <div class="metric">{{ metrics.vehicle_count }}</div>
                </div>

                <div class="card">
                    <div class="metric-icon"><i class="fa-solid fa-heart-pulse"></i></div>
                    <div class="metric-label">Fleet Health</div>
                    <div class="metric">{{ metrics.fleet_health }}/100</div>
                </div>

                <div class="card">
                    <div class="metric-icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
                    <div class="metric-label">Average Risk</div>
                    <div class="metric">{{ metrics.average_risk }}/100</div>
                </div>

                <div class="card">
                    <div class="metric-icon"><i class="fa-solid fa-wrench"></i></div>
                    <div class="metric-label">Due Items</div>
                    <div class="metric">{{ metrics.due_items }}</div>
                </div>

                <div class="card">
                    <div class="metric-icon"><i class="fa-solid fa-coins"></i></div>
                    <div class="metric-label">Total Cost</div>
                    <div class="metric">€{{ metrics.total_cost }}</div>
                </div>

                <div class="card">
                    <div class="metric-icon"><i class="fa-solid fa-calendar-days"></i></div>
                    <div class="metric-label">12M Forecast</div>
                    <div class="metric">€{{ metrics.forecast_cost }}</div>
                </div>
            </div>
        </section>

        <section id="data" class="section grid3">
            <div class="card">
                <h2><i class="fa-solid fa-database"></i> Dataset Tools</h2>
                <div class="button-row">
                    <form method="post" action="/load_demo"><button class="green" type="submit">Load Demo Data</button></form>
                    <form method="post" action="/clear"><button class="red" type="submit">Clear</button></form>
                </div>

                <br>

                <form method="post" action="/upload_csv" enctype="multipart/form-data">
                    <input type="file" name="csv_file" accept=".csv">
                    <button type="submit">Import CSV</button>
                </form>

                <p class="muted">
                    Accepted: vin, brand/make, model, year/model_year, mileage_km/mileage/odometer, fuel_type/fuel, engine.
                </p>
            </div>

            <div class="card">
                <h2><i class="fa-solid fa-barcode"></i> VIN Decoder</h2>
                <p class="muted">
                    Enter a 17-character VIN. The app uses the NHTSA vPIC API to create a vehicle record.
                </p>

                <form method="post" action="/decode_vin">
                    <input type="text" name="vin" maxlength="17" placeholder="17-character VIN">
                    <button type="submit">Decode & Add Vehicle</button>
                </form>

                {% if vin_preview %}
                    <p class="muted">
                        <strong>Decoded preview:</strong><br>
                        {{ vin_preview.brand }} {{ vin_preview.model }}<br>
                        Year: {{ vin_preview.year }}<br>
                        Fuel: {{ vin_preview.fuel_type }}
                    </p>
                {% endif %}
            </div>

            <div class="card">
                <h2><i class="fa-solid fa-file-export"></i> Export Report</h2>
                <p class="muted">
                    Export vehicles with health score, risk level, fuel consumption, oil recommendation and forecasted cost.
                </p>

                <form method="get" action="/export_csv">
                    <button class="gray" type="submit">Download CSV Report</button>
                </form>

                <p class="muted">Pandas available: {{ "yes" if pandas_available else "no" }}</p>
            </div>
        </section>

        <section id="add" class="section card">
            <h2><i class="fa-solid fa-plus"></i> Add Vehicle Manually</h2>

            <form method="post" action="/add_vehicle">
                <div class="grid3">
                    <input type="text" name="vin" maxlength="17" placeholder="VIN or leave empty">
                    <input type="text" name="brand" placeholder="Brand" required>
                    <input type="text" name="model" placeholder="Model" required>
                    <input type="number" name="year" min="1980" max="2100" placeholder="Year" required>
                    <input type="number" name="mileage_km" min="0" step="0.1" placeholder="Mileage km" required>

                    <select name="fuel_type">
                        {% for fuel in ["Petrol", "Diesel", "Hybrid", "Electric"] %}
                            <option value="{{ fuel }}">{{ fuel }}</option>
                        {% endfor %}
                    </select>

                    <input type="text" name="engine" placeholder="Engine optional">
                </div>

                <button type="submit">Create Vehicle Record</button>
            </form>
        </section>

        <section id="logs" class="section grid2">
            <div class="card">
                <h2><i class="fa-solid fa-screwdriver-wrench"></i> Add Service Record</h2>

                {% if fleet.vehicles %}
                    <form method="post" action="/add_service">
                        <select name="vin" required>
                            {% for vehicle in fleet.vehicles %}
                                <option value="{{ vehicle.vin }}">{{ vehicle.name() }} — {{ vehicle.short_vin() }}</option>
                            {% endfor %}
                        </select>

                        <select name="service_type" required>
                            {% for service in service_types %}
                                <option value="{{ service }}">{{ service }}</option>
                            {% endfor %}
                        </select>

                        <input type="number" name="cost" min="0" step="0.01" placeholder="Cost" required>
                        <input type="number" name="mileage_km" min="0" step="0.1" placeholder="Mileage at service" required>
                        <textarea name="notes" placeholder="Notes"></textarea>

                        <button type="submit">Save Service Record</button>
                    </form>
                {% else %}
                    <div class="empty">Add or import a vehicle first.</div>
                {% endif %}
            </div>

            <div class="card">
                <h2><i class="fa-solid fa-gas-pump"></i> Add Fuel Record</h2>

                {% if fleet.vehicles %}
                    <form method="post" action="/add_fuel">
                        <select name="vin" required>
                            {% for vehicle in fleet.vehicles %}
                                <option value="{{ vehicle.vin }}">{{ vehicle.name() }} — {{ vehicle.short_vin() }}</option>
                            {% endfor %}
                        </select>

                        <input type="number" name="liters" min="0" step="0.01" placeholder="Liters">
                        <input type="number" name="total_price" min="0" step="0.01" placeholder="Total price">
                        <input type="number" name="mileage_km" min="0" step="0.1" placeholder="Mileage at fuel record">

                        <button type="submit">Save Fuel Record</button>
                    </form>
                {% else %}
                    <div class="empty">Add or import a vehicle first.</div>
                {% endif %}
            </div>
        </section>

        <section id="analytics" class="section grid2">
            <div class="card">
                <h2><i class="fa-solid fa-chart-column"></i> Cost Analytics</h2>
                {% if fleet.vehicles %}
                    <canvas id="costChart" height="120"></canvas>
                {% else %}
                    <div class="empty">No chart data yet.</div>
                {% endif %}
            </div>

            <div class="card">
                <h2><i class="fa-solid fa-chart-line"></i> Health & Risk Analytics</h2>
                {% if fleet.vehicles %}
                    <canvas id="healthChart" height="120"></canvas>
                {% else %}
                    <div class="empty">No chart data yet.</div>
                {% endif %}
            </div>
        </section>

        <section class="section grid2">
            <div class="card">
                <h2><i class="fa-solid fa-chart-pie"></i> Risk Distribution</h2>
                {% if fleet.vehicles %}
                    <canvas id="riskPieChart" height="120"></canvas>
                {% else %}
                    <div class="empty">No chart data yet.</div>
                {% endif %}
            </div>

            <div class="card">
                <h2><i class="fa-solid fa-robot"></i> AI Mechanic Logic</h2>
                <div class="ai-box">
                    <div class="ai-title">Rule-Based Recommendation Engine</div>
                    <p class="muted">
                        The system analyzes mileage, vehicle age, missing service records, overdue services,
                        fuel consumption and service intervals to generate prioritized mechanic recommendations.
                    </p>
                    <p class="muted">
                        This creates AI-like assistant behavior without depending on external LLM setup.
                    </p>
                </div>
            </div>
        </section>

        <section id="vehicles" class="section card">
            <h2><i class="fa-solid fa-car-side"></i> Vehicle Overview</h2>

            {% if fleet.vehicles %}
                <table>
                    <thead>
                        <tr>
                            <th>VIN</th>
                            <th>Vehicle</th>
                            <th>Year</th>
                            <th>Mileage</th>
                            <th>Fuel</th>
                            <th>Oil</th>
                            <th>Health</th>
                            <th>Risk</th>
                            <th>12M Cost</th>
                            <th>Total Cost</th>
                        </tr>
                    </thead>

                    <tbody>
                        {% for vehicle in fleet.vehicles %}
                            {% set risk = vehicle.risk_score() %}
                            <tr>
                                <td class="vin">{{ vehicle.short_vin() }}</td>

                                <td>
                                    {{ vehicle.name() }}<br>
                                    <span class="muted">
                                        {{ vehicle.engine or "No engine info" }}
                                    </span>
                                </td>

                                <td>{{ vehicle.year }}</td>
                                <td>{{ vehicle.mileage_km|round|int }} km</td>
                                <td>{{ vehicle.fuel_type }}</td>
                                <td><span class="muted">{{ vehicle.recommended_oil() }}</span></td>

                                <td>
                                    {% if vehicle.health_score() >= 75 %}
                                        <span class="health-good">{{ vehicle.health_score() }}/100</span>
                                    {% elif vehicle.health_score() >= 50 %}
                                        <span class="health-warning">{{ vehicle.health_score() }}/100</span>
                                    {% else %}
                                        <span class="health-danger">{{ vehicle.health_score() }}/100</span>
                                    {% endif %}

                                    <div class="health-bar">
                                        <div class="health-fill" style="width: {{ vehicle.health_score() }}%;"></div>
                                    </div>

                                    <span class="muted">{{ vehicle.health_status() }}</span>
                                </td>

                                <td>
                                    <span class="pill {{ risk.level|lower }}">{{ risk.level }} {{ risk.score }}</span>
                                    <br>
                                    <span class="muted">{{ ", ".join(risk.reasons) }}</span>
                                </td>

                                <td>€{{ vehicle.next_12_month_cost() }}</td>
                                <td>€{{ vehicle.total_cost() }}</td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            {% else %}
                <div class="empty">No vehicles added yet.</div>
            {% endif %}
        </section>

        <section id="maintenance" class="section card">
            <h2><i class="fa-solid fa-robot"></i> AI Mechanic Maintenance Assistant</h2>

            {% if fleet.vehicles %}
                {% for vehicle in fleet.vehicles %}
                    <h3>{{ vehicle.name() }} — <span class="vin">{{ vehicle.short_vin() }}</span></h3>

                    <p class="muted">
                        Recommended oil: <strong>{{ vehicle.recommended_oil() }}</strong> |
                        Health: <strong>{{ vehicle.health_score() }}/100</strong> |
                        12-month expected maintenance cost: <strong>€{{ vehicle.next_12_month_cost() }}</strong> |
                        Fuel consumption:
                        <strong>{{ vehicle.fuel_consumption_l_100km() if vehicle.fuel_consumption_l_100km() is not none else "N/A" }}</strong>
                    </p>

                    <h4>AI Mechanic Recommendations</h4>
                    <table>
                        <thead>
                            <tr>
                                <th>Priority</th>
                                <th>Item</th>
                                <th>Recommendation</th>
                                <th>Est. Cost</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for rec in vehicle.ai_recommendations() %}
                                <tr>
                                    <td>
                                        {% if rec.priority == "High" %}
                                            <span class="pill high">High</span>
                                        {% else %}
                                            <span class="pill medium">Medium</span>
                                        {% endif %}
                                    </td>
                                    <td>{{ rec.item }}</td>
                                    <td>{{ rec.message }}</td>
                                    <td>€{{ rec.estimated_cost }}</td>
                                </tr>
                            {% endfor %}
                        </tbody>
                    </table>

                    <h4>Smart Optimization Tips</h4>
                    <ul class="tip-list">
                        {% for tip in vehicle.smart_tips() %}
                            <li>{{ tip }}</li>
                        {% endfor %}
                    </ul>

                    <table>
                        <thead>
                            <tr>
                                <th>Service</th>
                                <th>Last Service km</th>
                                <th>Next Due km</th>
                                <th>km Left</th>
                                <th>Estimated Cost</th>
                                <th>Status</th>
                            </tr>
                        </thead>

                        <tbody>
                            {% for row in vehicle.maintenance_plan() %}
                                <tr>
                                    <td>{{ row.service_type }}</td>
                                    <td>{{ row.latest_km if row.latest_km is not none else "No record" }}</td>
                                    <td>{{ row.next_due }}</td>
                                    <td>{{ row.km_left }}</td>
                                    <td>€{{ row.estimated_cost }}</td>
                                    <td>
                                        {% if row.status == "Overdue" %}
                                            <span class="status-danger">{{ row.status }}</span>
                                        {% elif row.status == "Due soon" %}
                                            <span class="status-warning">{{ row.status }}</span>
                                        {% else %}
                                            <span class="status-ok">{{ row.status }}</span>
                                        {% endif %}
                                    </td>
                                </tr>
                            {% endfor %}
                        </tbody>
                    </table>

                    <br>
                {% endfor %}
            {% else %}
                <div class="empty">Maintenance recommendations appear after vehicles are added.</div>
            {% endif %}
        </section>

    </main>
</div>

{% if fleet.vehicles %}
<script>
const chartData = {{ chart_data | tojson }};

const costCanvas = document.getElementById("costChart");
if (costCanvas) {
    new Chart(costCanvas, {
        type: "bar",
        data: {
            labels: chartData.labels,
            datasets: [
                {
                    label: "Service Cost",
                    data: chartData.service_costs,
                    backgroundColor: "rgba(59, 130, 246, 0.75)"
                },
                {
                    label: "Fuel Cost",
                    data: chartData.fuel_costs,
                    backgroundColor: "rgba(34, 197, 94, 0.75)"
                },
                {
                    label: "12M Forecast",
                    data: chartData.forecast_costs,
                    backgroundColor: "rgba(249, 115, 22, 0.75)"
                }
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: "#e5e7eb" } } },
            scales: {
                x: { ticks: { color: "#e5e7eb" }, grid: { color: "rgba(255,255,255,0.08)" } },
                y: { ticks: { color: "#e5e7eb" }, grid: { color: "rgba(255,255,255,0.08)" } }
            }
        }
    });
}

const healthCanvas = document.getElementById("healthChart");
if (healthCanvas) {
    new Chart(healthCanvas, {
        type: "line",
        data: {
            labels: chartData.labels,
            datasets: [
                {
                    label: "Health Score",
                    data: chartData.health_scores,
                    borderColor: "rgba(34, 197, 94, 1)",
                    backgroundColor: "rgba(34, 197, 94, 0.15)",
                    tension: 0.3,
                    fill: true
                },
                {
                    label: "Risk Score",
                    data: chartData.risk_scores,
                    borderColor: "rgba(239, 68, 68, 1)",
                    backgroundColor: "rgba(239, 68, 68, 0.15)",
                    tension: 0.3,
                    fill: true
                }
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: "#e5e7eb" } } },
            scales: {
                x: { ticks: { color: "#e5e7eb" }, grid: { color: "rgba(255,255,255,0.08)" } },
                y: { min: 0, max: 100, ticks: { color: "#e5e7eb" }, grid: { color: "rgba(255,255,255,0.08)" } }
            }
        }
    });
}

const pieCanvas = document.getElementById("riskPieChart");
if (pieCanvas) {
    new Chart(pieCanvas, {
        type: "doughnut",
        data: {
            labels: ["Low Risk", "Medium Risk", "High Risk"],
            datasets: [{
                data: chartData.risk_distribution,
                backgroundColor: [
                    "rgba(34, 197, 94, 0.75)",
                    "rgba(245, 158, 11, 0.75)",
                    "rgba(239, 68, 68, 0.75)"
                ]
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: "#e5e7eb" } } }
        }
    });
}
</script>
{% endif %}

</body>
</html>
"""


@app.route("/")
def index():
    return render_app()


@app.route("/load_demo", methods=["POST"])
def load_demo():
    load_demo_fleet()
    return render_app("Demo vehicle records loaded successfully.")


@app.route("/clear", methods=["POST"])
def clear_data():
    fleet.clear()
    return render_app("All vehicle records cleared.")


@app.route("/decode_vin", methods=["POST"])
def decode_vin_route():
    vin = request.form.get("vin", "")
    vin = "".join(vin.strip().upper().split())

    if len(vin) != 17:
        return render_app(f"VIN has {len(vin)} characters. It must be exactly 17.")

    decoded = decode_vin(vin)

    if not decoded and vin.startswith("WBA"):
        decoded = {
            "vin": vin,
            "brand": "BMW",
            "model": "Unknown BMW Model",
            "year": "2018",
            "engine": "",
            "fuel_type": "Petrol",
        }

    if not decoded:
        return render_app("VIN could not be decoded, but the format is valid.")

    try:
        year = int(decoded["year"]) if str(decoded["year"]).isdigit() else datetime.now().year
    except ValueError:
        year = datetime.now().year

    vehicle = create_vehicle(
        vin=decoded["vin"],
        brand=decoded["brand"],
        model=decoded["model"] or "Unknown",
        year=year,
        mileage_km=0,
        fuel_type=decoded["fuel_type"],
        engine=decoded.get("engine", ""),
    )

    if not fleet.add_vehicle(vehicle):
        return render_app("This VIN already exists in the system.", vin_preview=decoded)

    return render_app(
        f"Vehicle created from VIN: {vehicle.name()}.",
        vin_preview=decoded,
    )


@app.route("/add_vehicle", methods=["POST"])
def add_vehicle():
    vin = request.form.get("vin", "").strip().upper()
    brand = request.form.get("brand", "").strip()
    model = request.form.get("model", "").strip()
    fuel_type = request.form.get("fuel_type", "Petrol")
    engine = request.form.get("engine", "").strip()

    try:
        year = int(request.form.get("year", datetime.now().year))
        mileage_km = float(request.form.get("mileage_km", 0))
    except ValueError:
        return render_app("Invalid year or mileage value.")

    if not vin or len(vin) != 17:
        vin = make_demo_vin(len(fleet.vehicles) + 1)

    vehicle = create_vehicle(
        vin=vin,
        brand=brand,
        model=model,
        year=year,
        mileage_km=mileage_km,
        fuel_type=fuel_type,
        engine=engine,
    )

    if not fleet.add_vehicle(vehicle):
        return render_app("Vehicle already exists.")

    return render_app("Vehicle record created successfully.")


@app.route("/add_service", methods=["POST"])
def add_service():
    vehicle = fleet.find_vehicle(request.form.get("vin", ""))

    if not vehicle:
        return render_app("Vehicle not found.")

    try:
        cost = float(request.form.get("cost", 0))
        mileage_km = float(request.form.get("mileage_km", 0))
    except ValueError:
        return render_app("Invalid service cost or mileage.")

    vehicle.service_records.append(
        ServiceRecord(
            service_type=request.form.get("service_type", "Oil Change"),
            cost=max(0, cost),
            mileage_km=max(0, mileage_km),
            notes=request.form.get("notes", "").strip(),
        )
    )

    vehicle.mileage_km = max(vehicle.mileage_km, mileage_km)

    return render_app("Service record saved successfully.")


@app.route("/add_fuel", methods=["POST"])
def add_fuel():
    vehicle = fleet.find_vehicle(request.form.get("vin", ""))

    if not vehicle:
        return render_app("Vehicle not found.")

    try:
        liters = float(request.form.get("liters", 0))
        total_price = float(request.form.get("total_price", 0))
        mileage_km = float(request.form.get("mileage_km", 0))
    except ValueError:
        return render_app("Invalid fuel values.")

    vehicle.fuel_records.append(
        FuelRecord(
            liters=max(0, liters),
            total_price=max(0, total_price),
            mileage_km=max(0, mileage_km),
        )
    )

    vehicle.mileage_km = max(vehicle.mileage_km, mileage_km)

    return render_app("Fuel record saved successfully.")


@app.route("/upload_csv", methods=["POST"])
def upload_csv():
    uploaded_file = request.files.get("csv_file")

    if not uploaded_file or uploaded_file.filename == "":
        return render_app("No CSV file selected.")

    try:
        content = uploaded_file.read().decode("utf-8")
    except UnicodeDecodeError:
        return render_app("CSV file must be UTF-8 encoded.")

    try:
        imported = import_csv_content(content)
    except Exception as exc:
        return render_app(f"CSV import failed: {exc}")

    return render_app(f"CSV import completed. Imported {imported} vehicle(s).")


@app.route("/export_csv")
def export_csv():
    rows = fleet.export_rows()
    output = io.StringIO()

    fieldnames = [
        "vin",
        "brand",
        "model",
        "year",
        "mileage_km",
        "fuel_type",
        "engine",
        "recommended_oil",
        "service_cost",
        "fuel_cost",
        "total_cost",
        "forecast_12_month_cost",
        "fuel_consumption_l_100km",
        "health_score",
        "health_status",
        "risk_score",
        "risk_level",
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=autoiq_vehicle_report.csv"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)