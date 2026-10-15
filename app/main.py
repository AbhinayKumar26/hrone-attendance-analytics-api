"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

IST = timezone(timedelta(hours=5, minutes=30))


def to_epoch_ms(value):
    """Convert a datetime to epoch milliseconds for API responses."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

db.employees.create_index(
    [("department", 1), ("joined_on", 1)]
)

# Create indexes for faster queries and duplicate prevention
db.employees.create_index("emp_code", unique=True)

db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True
)

db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1)]
)

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(
        hour=h, minute=m, second=0, microsecond=0
    )
    minutes = int((punch_in - start).total_seconds() / 60)

    if minutes > 10:
        return minutes
    return 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))

    end = datetime.fromisoformat(date_str).replace(
        hour=h,
        minute=m,
        second=0,
        microsecond=0
    )

    # Make both datetimes timezone-naive for subtraction.
    if punch_out.tzinfo is not None:
        punch_out = punch_out.replace(tzinfo=None)

    minutes = int((punch_out - end).total_seconds() / 60)

    # Overtime starts after a 30-minute grace period.
    return minutes if minutes >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
):
    if page < 1:
        raise HTTPException(400, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(400, "page_size must be between 1 and 100")

    q = {}

    if department:
        q["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    ts = (
        datetime.fromtimestamp(body.punched_at / 1000, tz=IST)
        if body.punched_at
        else datetime.now(IST)
    )
    d = ts.date().isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    doc["punch_in"] = to_epoch_ms(doc["punch_in"])
    doc["punch_out"] = to_epoch_ms(doc["punch_out"])
    return doc


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    ts = (
        datetime.fromtimestamp(body.punched_at / 1000, tz=IST)
        if body.punched_at
        else datetime.now(IST)
    )
    date_str = ts.date().isoformat()

    record = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": date_str
    })

    if not record:
        raise HTTPException(status_code=404, detail="Punch-in record not found")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Already punched out")

    punch_in_time = record["punch_in"]

    # MongoDB stores timezone-aware dates as naive UTC dates.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    punch_in_time = punch_in_time.astimezone(IST)

    if ts <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be later than punch-in"
        )

    seconds = (ts - punch_in_time).total_seconds()
    if seconds > 86400:
        raise HTTPException(status_code=422, detail="Invalid work duration")

    work_hours = round(seconds / 3600, 2)
    
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    overtime = compute_overtime(ts, emp["shift_end"], date_str)
    half_day = work_hours < 4.5

    db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day
            }
        }
    )
    return {
        "emp_code": body.emp_code,
        "date": date_str,
        "punch_out": to_epoch_ms(ts),
        "work_hours": work_hours,
        "overtime_minutes": overtime,
        "half_day": half_day
    }


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(422, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    # Validate date filters
    try:
        if date_from:
            datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "Dates must use YYYY-MM-DD format")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        if status not in ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]:
            raise HTTPException(422, "Invalid status")
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    cursor = (
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    docs = list(cursor)

    for record in docs:
        for field in ["punch_in", "punch_out"]:
            if field in record:
                record[field] = to_epoch_ms(record[field])

        for entry in record.get("history", []):
            if "at" in entry:
                entry["at"] = to_epoch_ms(entry["at"])

            for change in entry.get("changes", {}).values():
                for key in ["from", "to"]:
                    if isinstance(change.get(key), datetime):
                        change[key] = to_epoch_ms(change[key])

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    regularized_by: str
    reason: str


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })
    if not record:
        raise HTTPException(404, "Attendance record not found")

    changes = {}
    updates = {}

    for field in ["status", "punch_in", "punch_out"]:
        value = getattr(body, field)
        if value is None:
            continue

        if field == "status" and value not in [
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        ]:
            raise HTTPException(422, "Invalid status")

        if field in ["punch_in", "punch_out"]:
            if value < 100000000000 or value > 4102444800000:
                raise HTTPException(422, "Timestamp must be epoch milliseconds")
            value = datetime.fromtimestamp(
                value / 1000, tz=timezone.utc
            ).replace(tzinfo=None, microsecond=0)

        old_value = record.get(field)
        if old_value != value:
            changes[field] = {
                "from": old_value,
                "to": value
            }
            updates[field] = value

    final_status = updates.get("status", record["status"])

    if final_status in ["ABSENT", "LEAVE"]:
        for field in ["punch_in", "punch_out"]:
            old_value = record.get(field)
            if old_value is not None:
                changes[field] = {"from": old_value, "to": None}
                updates[field] = None
    else:
        if updates.get("punch_in", record.get("punch_in")) is None:
            raise HTTPException(422, "Presence status requires punch_in")

    if not changes:
        raise HTTPException(422, "No changes supplied")

    final_in = updates.get("punch_in", record.get("punch_in"))
    final_out = updates.get("punch_out", record.get("punch_out"))

    if final_in:
        if final_in.tzinfo is None:
            final_in_aware = final_in.replace(tzinfo=timezone.utc)
        else:
            final_in_aware = final_in
        if final_in_aware.astimezone(IST).date().isoformat() != date:
            raise HTTPException(422, "punch_in must match attendance date")

    if final_out and final_in:
        if final_in.tzinfo is None:
            final_in = final_in.replace(tzinfo=timezone.utc)

        if final_out.tzinfo is None:
            final_out = final_out.replace(tzinfo=timezone.utc)

        seconds = (final_out - final_in).total_seconds()

        if seconds <= 0 or seconds > 86400:
            raise HTTPException(422, "Invalid punch_out time")

    if final_status in ["ABSENT", "LEAVE"]:
        updates.update({
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False
        })
    else:
        if final_in.tzinfo is None:
            final_in_aware = final_in.replace(tzinfo=timezone.utc)
        else:
            final_in_aware = final_in
        local_in = final_in_aware.astimezone(IST)
        updates["late_minutes"] = compute_late_minutes(
            local_in, emp["shift_start"]
        )

        if final_out:
            hours = Decimal(str((final_out - final_in).total_seconds())) / Decimal("3600")
            hours = float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            updates["work_hours"] = hours
            
            if final_out.tzinfo is None:
                final_out_aware = final_out.replace(tzinfo=timezone.utc)
            else:
                final_out_aware = final_out
            updates["overtime_minutes"] = compute_overtime(
                final_out_aware.astimezone(IST), emp["shift_end"], date
            )
            updates["half_day"] = hours < 4.5
        else:
            updates["work_hours"] = None
            updates["overtime_minutes"] = 0
            updates["half_day"] = False

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "history": record.get("history", [])},
        {
            "$set": updates,
            "$push": {"history": history_entry}
        }
    )
    if result.modified_count == 0:
        raise HTTPException(409, "Record changed; retry correction")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    if not updated:
        raise HTTPException(404, "Updated attendance record not found")
    updated.pop("_id", None)
    for field in ["punch_in", "punch_out"]:
        updated[field] = to_epoch_ms(updated.get(field))
    for entry in updated.get("history", []):
        if "at" in entry:
            entry["at"] = to_epoch_ms(entry["at"])
        for change in entry.get("changes", {}).values():
            for key in ["from", "to"]:
                if isinstance(change.get(key), datetime):
                    change[key] = to_epoch_ms(change[key])

    return updated


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str,
):
    import calendar

    # Validate month format
    try:
        month_date = datetime.strptime(month, "%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "month must use YYYY-MM format")

    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(404, "Employee not found")

    year = month_date.year
    month_number = month_date.month
    last_day = calendar.monthrange(year, month_number)[1]
    month_start = f"{month}-01"
    month_end = f"{month}-{last_day:02d}"

    joined_on = employee["joined_on"]
    effective_start = max(month_start, joined_on)

    working_days = 0
    if effective_start <= month_end:
        current = datetime.strptime(effective_start, "%Y-%m-%d").date()
        end_date = datetime.strptime(month_end, "%Y-%m-%d").date()

        while current <= end_date:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)

    records = list(
        db.attendance_logs.find(
            {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lte": month_end},
            },
            {"_id": 0, "date": 1, "status": 1, "work_hours": 1,
             "late_minutes": 1, "half_day": 1},
        )
    )

    present_statuses = {"PRESENT", "WFH", "ON_DUTY"}
    present_days = 0.0
    leave_days = 0
    late_count = 0

    for record in records:
        if record["status"] in present_statuses:
            if datetime.strptime(record["date"], "%Y-%m-%d").weekday() < 5:
                present_days += 0.5 if record.get("half_day", False) else 1.0

        if record["status"] == "LEAVE":
            leave_days += 1

        if record.get("late_minutes", 0) > 0:
            late_count += 1

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) / Decimal(working_days) * 100)
            .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None,
):
    import calendar

    try:
        month_date = datetime.strptime(month, "%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "month must use YYYY-MM format")

    year = month_date.year
    month_number = month_date.month
    last_day = calendar.monthrange(year, month_number)[1]
    month_start = f"{month}-01"
    month_end = f"{month}-{last_day:02d}"

    employee_query = {"joined_on": {"$lte": month_end}}
    if department:
        employee_query["department"] = department

    employees = list(
        db.employees.find(
            employee_query,
            {"_id": 0, "emp_code": 1, "department": 1}
        )
    )

    department_employees = {}
    employee_department = {}

    for employee in employees:
        dept = employee["department"]
        department_employees.setdefault(dept, set()).add(employee["emp_code"])
        employee_department[employee["emp_code"]] = dept

    attendance_query = {
        "date": {"$gte": month_start, "$lte": month_end}
    }

    if department:
        employee_codes = [
            employee["emp_code"] for employee in employees
        ]
        attendance_query["emp_code"] = {"$in": employee_codes}

    records = list(
        db.attendance_logs.find(
            attendance_query,
            {
                "_id": 0,
                "emp_code": 1,
                "status": 1,
                "work_hours": 1,
                "late_minutes": 1,
            }
        )
    )

    summary = {}

    for dept, codes in department_employees.items():
        summary[dept] = {
            "department": dept,
            "headcount": len(codes),
            "present_days": 0.0,
            "work_hours_total": 0.0,
            "work_hours_count": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "leave_count": 0,
            "on_duty_count": 0,
        }

    present_statuses = {"PRESENT", "WFH", "ON_DUTY"}

    for record in records:
        dept = employee_department.get(record["emp_code"])
        if dept not in summary:
            continue

        item = summary[dept]

        if record["status"] in present_statuses:
            item["present_days"] += 1

        if record["status"] == "LEAVE":
            item["leave_count"] += 1

        if record["status"] == "ON_DUTY":
            item["on_duty_count"] += 1

        late_minutes = record.get("late_minutes", 0)
        if late_minutes > 0:
            item["late_count"] += 1
            item["total_late_minutes"] += late_minutes

        work_hours = record.get("work_hours")
        if record["status"] in present_statuses and work_hours is not None:
            item["work_hours_total"] += work_hours
            item["work_hours_count"] += 1

    result = []

    for dept in sorted(summary):
        item = summary[dept]
        count = item.pop("work_hours_count")
        total_hours = item.pop("work_hours_total")

        item["avg_work_hours"] = (
            float(
                (Decimal(str(total_hours)) / Decimal(count)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )
            if count else None
        )

        result.append(item)

    return result


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    import calendar

    try:
        month_date = datetime.strptime(month, "%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "month must use YYYY-MM format")

    if limit < 1 or limit > 100:
        raise HTTPException(422, "limit must be between 1 and 100")

    last_day = calendar.monthrange(month_date.year, month_date.month)[1]
    month_start = f"{month}-01"
    month_end = f"{month}-{last_day:02d}"

    employee_query = {}
    if department:
        employee_query["department"] = department

    employees = list(
        db.employees.find(employee_query, {"_id": 0, "emp_code": 1, "name": 1, "department": 1})
    )
    employee_map = {e["emp_code"]: e for e in employees}

    query = {
        "date": {"$gte": month_start, "$lte": month_end},
        "late_minutes": {"$gt": 0},
        "emp_code": {"$in": list(employee_map.keys())},
    }

    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
    ]

    totals = list(db.attendance_logs.aggregate(pipeline))

    results = []
    previous_total = None
    current_rank = 0

    for position, item in enumerate(totals, start=1):
        total = item["total_late_minutes"]

        if total != previous_total:
            current_rank = position
            previous_total = total

        if current_rank > limit:
            break

        employee = employee_map.get(item["_id"])
        if employee:
            results.append({
                "rank": current_rank,
                "emp_code": item["_id"],
                "name": employee["name"],
                "department": employee["department"],
                "total_late_minutes": total,
                "late_count": item["late_count"],
            })

    return results


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = None,
    to_date: str = None,
):
    if not from_date or not to_date:
        raise HTTPException(
            status_code=422,
            detail="from_date and to_date are required",
        )

    try:
        start_date = datetime.strptime(from_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(to_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format",
        )

    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="to_date must be on or after from_date",
        )

    day_count = (end_date - start_date).days + 1
    if day_count > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404, detail="Department not found")

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())

    pipeline = [
        {"$match": {"department": department}},
        {
            "$project": {
                "_id": 0,
                "emp_code": 1,
                "joined_on": 1,
            }
        },
        {
            "$group": {
                "_id": None,
                "employees": {
                    "$push": {
                        "emp_code": "$emp_code",
                        "joined_on": "$joined_on",
                    }
                },
            }
        },
        {
            "$set": {
                "calendar": {
                    "$map": {
                        "input": {"$range": [0, day_count]},
                        "as": "day_offset",
                        "in": {
                            "$dateAdd": {
                                "startDate": start_dt,
                                "unit": "day",
                                "amount": "$$day_offset",
                            }
                        },
                    }
                }
            }
        },
        {"$unwind": "$calendar"},
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$calendar",
                    }
                }
            }
        },
        {
            "$set": {
                "eligible_employees": {
                    "$filter": {
                        "input": "$employees",
                        "as": "employee",
                        "cond": {
                            "$lte": ["$$employee.joined_on", "$date"]
                        },
                    }
                }
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$eligible_employees"},
                "eligible_codes": {
                    "$map": {
                        "input": "$eligible_employees",
                        "as": "employee",
                        "in": "$$employee.emp_code",
                    }
                },
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "codes": "$eligible_codes",
                    "day": "$date",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$emp_code", "$$codes"]},
                                    {"$eq": ["$date", "$$day"]},
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": ["$late_minutes", 0]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "daily_stats",
            }
        },
        {
            "$set": {
                "present_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily_stats.present_count", 0]},
                        0,
                    ]
                },
                "late_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily_stats.late_count", 0]},
                        0,
                    ]
                },
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$calendar"},
                                [1, 7],
                            ]
                        }
                    ]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$multiply": [
                                        {
                                            "$divide": [
                                                "$present_count",
                                                "$headcount",
                                            ]
                                        },
                                        100,
                                    ]
                                },
                                4,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$densify": {
                "field": "calendar",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt],
                },
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"calendar": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$calendar",
                    }
                },
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]},
            }
        },
        {"$sort": {"date": 1}},
    ]

    try:
        return list(db.employees.aggregate(pipeline))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Department trend aggregation failed: {str(exc)}",
        )


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    month: Optional[str] = None,
    emp_code: Optional[str] = None,
    department: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    import calendar

    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(422, "Unsupported endpoint")

    if endpoint in {
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
    } and not month:
        raise HTTPException(422, "month is required")

    if endpoint == "employee_monthly" and not emp_code:
        raise HTTPException(422, "emp_code is required")

    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                422, "department, from_date and to_date are required"
            )

    if month:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
            if parsed.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "month must use YYYY-MM format")

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if from_date or to_date:
            query["date"] = {}
            if from_date:
                query["date"]["$gte"] = from_date
            if to_date:
                query["date"]["$lte"] = to_date

        if from_date:
            try:
                datetime.strptime(from_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(422, "from_date must use YYYY-MM-DD")
        if to_date:
            try:
                datetime.strptime(to_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(422, "to_date must use YYYY-MM-DD")
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "from_date cannot be after to_date")

        explanation = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "limit": 20,
            },
            verbosity="executionStats",
        )
        collection = "attendance_logs"

    else:
        start = f"{month}-01" if month else None
        end = (
            f"{month}-{calendar.monthrange(int(month[:4]), int(month[5:7]))[1]:02d}"
            if month else None
        )

        if endpoint == "employee_monthly":
            pipeline = [
                {"$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": start, "$lte": end},
                }},
                {"$group": {
                    "_id": "$emp_code",
                    "record_count": {"$sum": 1},
                }},
            ]
            collection = "attendance_logs"

        elif endpoint == "department_summary":
            pipeline = [
                {"$match": {
                    "date": {"$gte": start, "$lte": end},
                }},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee",
                }},
                {"$unwind": "$employee"},
                {"$match": {
                    "employee.joined_on": {"$lte": end},
                    **(
                        {"employee.department": department}
                        if department else {}
                    ),
                }},
                {"$group": {
                    "_id": "$employee.department",
                    "record_count": {"$sum": 1},
                }},
            ]
            collection = "attendance_logs"

        elif endpoint == "late_leaderboard":
            match = {
                "date": {"$gte": start, "$lte": end},
                "late_minutes": {"$gt": 0},
            }

            if department:
                employees = list(db.employees.find(
                    {"department": department},
                    {"_id": 0, "emp_code": 1},
                ))
                match["emp_code"] = {
                    "$in": [e["emp_code"] for e in employees]
                }

            pipeline = [
                {"$match": match},
                {"$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }},
                {"$sort": {"total_late_minutes": -1, "_id": 1}},
            ]
            collection = "attendance_logs"

        else:
            try:
                datetime.strptime(from_date, "%Y-%m-%d")
                datetime.strptime(to_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(422, "Dates must use YYYY-MM-DD")

            if from_date > to_date:
                raise HTTPException(422, "from_date cannot be after to_date")

            if (
                datetime.strptime(to_date, "%Y-%m-%d")
                - datetime.strptime(from_date, "%Y-%m-%d")
            ).days > 91:
                raise HTTPException(422, "Date range cannot exceed 92 days")

            pipeline = [
                {"$match": {
                    "date": {"$gte": from_date, "$lte": to_date},
                }},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee",
                }},
                {"$unwind": "$employee"},
                {"$match": {"employee.department": department}},
                {"$group": {
                    "_id": "$date",
                    "record_count": {"$sum": 1},
                }},
                {"$sort": {"_id": 1}},
            ]
            collection = "attendance_logs"

        explanation = db.command(
            "explain",
            {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": explanation,
    }