from datetime import date, datetime, time, timedelta

import pytest

from app import create_app, db
from app.auth import _failed
from app.models import (Department, Doctor, DoctorSchedule, Patient, Role, User)


def next_weekday(wd=0, weeks_ahead=1):
    d = date.today() + timedelta(days=1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d + timedelta(weeks=weeks_ahead - 1)


@pytest.fixture
def app():
    app = create_app("config.TestConfig")
    with app.app_context():
        db.create_all()
        for n in ("admin", "doctor", "receptionist"):
            db.session.add(Role(name=n))
        db.session.flush()
        role = {r.name: r for r in Role.query}

        def mk(username, name, r, pw):
            u = User(username=username, full_name=name, email=f"{username}@x.test", role=role[r])
            u.set_password(pw)
            db.session.add(u)
            return u
        mk("admin", "Admin", "admin", "Admin@123")
        mk("reception", "Rita", "receptionist", "Reception@1")
        dept = Department(name="General")
        db.session.add(dept)
        for i in (1, 2):
            u = mk(f"doc{i}", f"Dr. Doc{i}", "doctor", "Doctor@123")
            d = Doctor(user=u, department=dept, specialization="GP", license_no=f"L{i}", consultation_fee=500)
            for wd in range(5):
                d.schedules.append(DoctorSchedule(weekday=wd, start_time=time(9), end_time=time(12)))
            db.session.add(d)
        for i, (f, l) in enumerate([("Asha", "Rao"), ("Ben", "Cole")], 1):
            db.session.add(Patient(patient_code=f"PT-{i}", first_name=f, last_name=l, dob=date(1990, 1, 1),
                                   gender="Female", phone=f"99999000{i}"))
        db.session.commit()
        _failed.clear()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


@pytest.fixture
def as_role(app):
    """Returns a function giving a logged-in client for a role/username."""
    creds = {"admin": ("admin", "Admin@123"), "reception": ("reception", "Reception@1"),
             "doc1": ("doc1", "Doctor@123"), "doc2": ("doc2", "Doctor@123")}

    def _make(who):
        c = app.test_client()
        r = login(c, *creds[who])
        assert r.status_code == 302
        return c
    return _make


@pytest.fixture
def ids(app):
    with app.app_context():
        return {"d1": Doctor.query.join(User).filter(User.username == "doc1").one().id,
                "d2": Doctor.query.join(User).filter(User.username == "doc2").one().id,
                "p1": Patient.query.filter_by(patient_code="PT-1").one().id,
                "p2": Patient.query.filter_by(patient_code="PT-2").one().id}
