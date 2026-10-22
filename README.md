# MediCare HMS – Hospital Management System

Python · Flask · MySQL · HTML/CSS. Patient registration, doctor scheduling, appointment booking with **time-slot conflict detection**, consultations/prescriptions, and billing, with **role-based access** for Admin, Doctor and Receptionist.

See [docs/DESIGN.md](docs/DESIGN.md) for architecture, ERD, RBAC matrix and the conflict-detection algorithm.

## Quick start (MySQL)

```bash
pip install -r requirements.txt

# 1. create DB + user
mysql -u root -p -e "CREATE DATABASE hospital_db CHARACTER SET utf8mb4;
  CREATE USER 'hms_user'@'localhost' IDENTIFIED BY 'hms_pass';
  GRANT ALL ON hospital_db.* TO 'hms_user'@'localhost';"

# 2. tables + demo data (or: mysql -u hms_user -p hospital_db < schema.sql)
flask --app run seed

# 3. run
python run.py          # http://127.0.0.1:5000
```
Or with Docker: `docker compose up --build` then `docker compose exec web flask --app run seed`.

**No MySQL handy?** `export DATABASE_URL=sqlite:///hospital.db` works for a quick look.

Set `SECRET_KEY` (and `SESSION_COOKIE_SECURE=1` behind HTTPS) in production; run with gunicorn: `gunicorn -w 2 run:app`.

## Demo logins
| Role | Username | Password |
|---|---|---|
| Receptionist | `reception` | `Reception@123` |
| Doctor | `dr.sharma`, `dr.iyer`, `dr.khan`, `dr.rao` | `Doctor@123` |

The seed script creates an admin account too. Set `HMS_ADMIN_PASSWORD` to a strong, private value before seeding a deployed database. Do not publish or share administrator credentials. Demo accounts are for synthetic portfolio data only.

## Try this flow
1. Sign in as **reception** → *Book appointment* → pick a doctor and a future weekday → choose a slot. Try booking the same slot again – it is rejected.
2. Sign in as **dr.sharma** → open a past/today appointment → *Consult* → diagnosis + prescription → saves and completes it.
3. Back as **reception** → *Appointments* → **Bill** → record a partial then full payment.
4. Sign in as **admin** → Reports, Users, Doctors → Hours.

## Project layout
```
app/            models, services (slot logic), blueprints, templates, static
config.py       Config / TestConfig
schema.sql      MySQL DDL (python scripts/export_schema.py to regenerate)
tests/          pytest suite (python -m pytest)
docs/DESIGN.md  system design
```

## Tests
```bash
python -m pytest -q      # 52 tests, in-memory SQLite
```
