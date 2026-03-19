"""Travel planning, recommendation, confirmation, and booking-prep workflow."""

from __future__ import annotations

from datetime import datetime

from app.db import get_conn
from app.schemas import TravelRequestCreate
from app.services.integrations import search_live_travel_options


def create_travel_request(payload: TravelRequestCreate) -> dict:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO travel_requests (
                origin, destination, depart_date, return_date, traveler_count,
                baggage, budget, currency, purpose, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.origin,
                payload.destination,
                payload.depart_date,
                payload.return_date,
                payload.traveler_count,
                payload.baggage,
                payload.budget,
                payload.currency,
                payload.purpose,
                payload.notes,
            ),
        )
        row = conn.execute(
            "SELECT * FROM travel_requests WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def get_travel_request(travel_request_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM travel_requests WHERE id = ?",
            (travel_request_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Travel request {travel_request_id} not found")
    return dict(row)


def list_travel_requests(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT tr.*,
                   COUNT(DISTINCT tf.id) AS flight_options_count,
                   COUNT(DISTINCT ao.id) AS stay_options_count,
                   MIN(tf.price) AS best_flight_price,
                   tp.status AS plan_status,
                   tp.booking_status
            FROM travel_requests tr
            LEFT JOIN travel_options tf ON tf.travel_request_id = tr.id
            LEFT JOIN accommodation_options ao ON ao.travel_request_id = tr.id
            LEFT JOIN travel_plans tp ON tp.travel_request_id = tr.id
            GROUP BY tr.id
            ORDER BY tr.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_travel_options(travel_request_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM travel_options
            WHERE travel_request_id = ?
            ORDER BY price ASC, duration_hours ASC
            """,
            (travel_request_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_accommodation_options(travel_request_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM accommodation_options
            WHERE travel_request_id = ?
            ORDER BY total_price ASC, price_per_night ASC
            """,
            (travel_request_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_travel_plan(travel_request_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM travel_plans
            WHERE travel_request_id = ?
            """,
            (travel_request_id,),
        ).fetchone()
    return dict(row) if row else None


def _upsert_travel_plan(travel_request_id: int, **fields) -> None:
    existing = get_travel_plan(travel_request_id)
    with get_conn() as conn:
        if existing:
            assignments = ", ".join(f"{key} = ?" for key in fields.keys())
            conn.execute(
                f"UPDATE travel_plans SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE travel_request_id = ?",
                (*fields.values(), travel_request_id),
            )
        else:
            keys = ["travel_request_id", *fields.keys()]
            placeholders = ", ".join("?" for _ in keys)
            conn.execute(
                f"INSERT INTO travel_plans ({', '.join(keys)}) VALUES ({placeholders})",
                (travel_request_id, *fields.values()),
            )


def generate_travel_options(travel_request_id: int) -> dict:
    request_row = get_travel_request(travel_request_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM travel_options WHERE travel_request_id = ?", (travel_request_id,))

        budget = request_row["budget"] or 900
        currency = request_row["currency"] or "EUR"
        traveler_count = request_row["traveler_count"] or 1
        base = max(180, int(budget / max(traveler_count, 1)))

        options = [
            {
                "provider": "BudgetAir Strategy",
                "category": "cheapest workable",
                "price": int(base * 0.78),
                "duration_hours": 13.5,
                "stops": 2,
                "baggage_included": 0,
                "cancellation_flexibility": "Low",
                "transfer_risk": "High",
                "summary": "Lowest-cost flight mix; expect longer layover and stricter fare rules.",
                "booking_url": None,
            },
            {
                "provider": "Balanced Route Strategy",
                "category": "best value",
                "price": int(base * 0.93),
                "duration_hours": 10.0,
                "stops": 1,
                "baggage_included": 1,
                "cancellation_flexibility": "Medium",
                "transfer_risk": "Medium",
                "summary": "Best balance of fare, baggage, and manageable transit time.",
                "booking_url": None,
            },
            {
                "provider": "Flexible Fare Strategy",
                "category": "safest / most convenient",
                "price": int(base * 1.18),
                "duration_hours": 8.0,
                "stops": 0,
                "baggage_included": 1,
                "cancellation_flexibility": "High",
                "transfer_risk": "Low",
                "summary": "Higher fare with lower disruption risk and stronger cancellation protection.",
                "booking_url": None,
            },
        ]

        for item in options:
            conn.execute(
                """
                INSERT INTO travel_options (
                    travel_request_id, provider, category, price, currency,
                    duration_hours, stops, baggage_included, cancellation_flexibility,
                    transfer_risk, summary, booking_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    travel_request_id,
                    item["provider"],
                    item["category"],
                    item["price"],
                    currency,
                    item["duration_hours"],
                    item["stops"],
                    item["baggage_included"],
                    item["cancellation_flexibility"],
                    item["transfer_risk"],
                    item["summary"],
                    item["booking_url"],
                ),
            )

    return {
        "travel_request_id": travel_request_id,
        "generated_options": 3,
        "strategy_note": "Flight recommendations created.",
    }


def generate_live_travel_options(travel_request_id: int) -> dict:
    request_row = get_travel_request(travel_request_id)
    return search_live_travel_options(request_row)


def generate_accommodation_options(travel_request_id: int) -> dict:
    request_row = get_travel_request(travel_request_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM accommodation_options WHERE travel_request_id = ?", (travel_request_id,))

        budget = request_row["budget"] or 900
        currency = request_row["currency"] or "EUR"
        total_stay_budget = max(150, int(budget * 0.42))
        nights = 5 if request_row.get("return_date") else 7

        options = [
            {
                "provider": "Hostelworld-style Stay",
                "stay_type": "hostel",
                "category": "cheapest workable",
                "price_per_night": max(25, int(total_stay_budget / nights * 0.55)),
                "cancellation_flexibility": "Medium",
                "safety_level": "Medium",
                "summary": "Lowest stay cost; shared facilities and basic privacy.",
                "booking_url": None,
            },
            {
                "provider": "Budget Hotel Strategy",
                "stay_type": "hotel",
                "category": "best value",
                "price_per_night": max(45, int(total_stay_budget / nights * 0.85)),
                "cancellation_flexibility": "Medium",
                "safety_level": "High",
                "summary": "Best value for commute, safety, and predictable check-in.",
                "booking_url": None,
            },
            {
                "provider": "Airbnb-style Apartment",
                "stay_type": "airbnb",
                "category": "safest / most convenient",
                "price_per_night": max(60, int(total_stay_budget / nights)),
                "cancellation_flexibility": "Varies",
                "safety_level": "High",
                "summary": "More space and comfort, but check cleaning fees and cancellation terms carefully.",
                "booking_url": None,
            },
        ]

        for item in options:
            total_price = item["price_per_night"] * nights
            conn.execute(
                """
                INSERT INTO accommodation_options (
                    travel_request_id, provider, stay_type, category, price_per_night,
                    total_price, currency, cancellation_flexibility, safety_level,
                    summary, booking_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    travel_request_id,
                    item["provider"],
                    item["stay_type"],
                    item["category"],
                    item["price_per_night"],
                    total_price,
                    currency,
                    item["cancellation_flexibility"],
                    item["safety_level"],
                    item["summary"],
                    item["booking_url"],
                ),
            )

    return {"travel_request_id": travel_request_id, "generated_options": 3}


def recommend_travel_plan(travel_request_id: int) -> dict:
    if not list_travel_options(travel_request_id):
        generate_travel_options(travel_request_id)
    if not list_accommodation_options(travel_request_id):
        generate_accommodation_options(travel_request_id)

    flight_options = list_travel_options(travel_request_id)
    stay_options = list_accommodation_options(travel_request_id)
    best_flight = next((item for item in flight_options if item["category"] == "best value"), flight_options[0])
    best_stay = next((item for item in stay_options if item["category"] == "best value"), stay_options[0])

    notes = (
        "Recommendation generated. Review total cost, cancellation flexibility, baggage, commute, and safety before confirming. "
        "Booking actions stay locked until you confirm the selected flight and stay."
    )
    _upsert_travel_plan(
        travel_request_id,
        status="recommended",
        recommended_flight_option_id=best_flight["id"],
        recommended_stay_option_id=best_stay["id"],
        recommendation_notes=notes,
        booking_status="pending_confirmation",
    )

    return {
        "travel_request_id": travel_request_id,
        "recommended_flight_option_id": best_flight["id"],
        "recommended_stay_option_id": best_stay["id"],
        "recommendation_notes": notes,
    }


def confirm_travel_plan(travel_request_id: int) -> dict:
    plan = get_travel_plan(travel_request_id)
    if not plan or not plan.get("recommended_flight_option_id") or not plan.get("recommended_stay_option_id"):
        raise ValueError("Generate recommendations before confirming a travel plan")

    confirmed_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    _upsert_travel_plan(
        travel_request_id,
        status="confirmed",
        confirmed_flight_option_id=plan["recommended_flight_option_id"],
        confirmed_stay_option_id=plan["recommended_stay_option_id"],
        confirmed_at=confirmed_at,
        booking_status="ready_for_booking",
        booking_notes="Confirmed by user. Booking preparation unlocked.",
    )
    return get_travel_plan(travel_request_id) or {}


def prepare_travel_booking(travel_request_id: int) -> dict:
    plan = get_travel_plan(travel_request_id)
    if not plan or plan.get("status") != "confirmed":
        raise ValueError("Confirm the recommended plan before booking")

    with get_conn() as conn:
        flight = conn.execute(
            "SELECT * FROM travel_options WHERE id = ?",
            (plan["confirmed_flight_option_id"],),
        ).fetchone()
        stay = conn.execute(
            "SELECT * FROM accommodation_options WHERE id = ?",
            (plan["confirmed_stay_option_id"],),
        ).fetchone()

    if flight is None or stay is None:
        raise ValueError("Confirmed booking selections are incomplete")

    booking_notes = (
        "Booking preparation completed. Review the selected flight and stay one final time, then proceed to provider booking pages. "
        "This workflow intentionally stops before irreversible payment confirmation."
    )
    _upsert_travel_plan(
        travel_request_id,
        status="booking_prepared",
        booking_status="awaiting_final_manual_booking",
        booking_notes=booking_notes,
    )

    return {
        "travel_request_id": travel_request_id,
        "flight": dict(flight),
        "stay": dict(stay),
        "booking_notes": booking_notes,
    }


def get_travel_workflow(travel_request_id: int) -> dict:
    request_row = get_travel_request(travel_request_id)
    plan = get_travel_plan(travel_request_id)
    flight_options = list_travel_options(travel_request_id)
    stay_options = list_accommodation_options(travel_request_id)

    recommended_flight = None
    recommended_stay = None
    if plan:
        recommended_flight = next((item for item in flight_options if item["id"] == plan.get("recommended_flight_option_id")), None)
        recommended_stay = next((item for item in stay_options if item["id"] == plan.get("recommended_stay_option_id")), None)

    return {
        "request": request_row,
        "plan": plan,
        "flight_options": flight_options,
        "stay_options": stay_options,
        "recommended_flight": recommended_flight,
        "recommended_stay": recommended_stay,
    }


def dashboard_travel_summary() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total_requests
            FROM travel_requests
            """
        ).fetchone()
    return {"total_requests": row["total_requests"] or 0}
