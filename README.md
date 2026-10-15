# Candidate kit

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.





## Implemented Features

- Employee creation and listing with duplicate employee-code protection.
- Attendance punch-in and punch-out with late-minute, work-hour, and overtime calculations.
- Attendance listing with employee, date, and status filters, plus pagination.
- Attendance regularization with correction history.
- Monthly employee analytics and department-level summaries.
- Late-arrival leaderboard and department attendance trends.
- MongoDB query execution statistics through the admin explain endpoint.

## Run the Application

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000/docs` to explore and test the API.

## Technology Stack

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo
- Uvicorn

## Notes

Configure `MONGO_URI` and `MONGO_DB` using environment variables before running the application. Do not commit `.env` files, credentials, or other secrets to the public repository.
