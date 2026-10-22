"""Idempotent seeding: roles + admin account, optionally demo data."""
from datetime import date, datetime, time, timedelta

from .extensions import db
from .models import (Appointment, Department, Doctor, DoctorSchedule, Patient, Role,
                     Service, User)
from .security import ADMIN, DOCTOR, RECEPTIONIST

DEMO_PASSWORDS = {"admin": "admin123", "reception": "reception123", "doctor": "doctor123"}


def _user(username, password, full_name, role, email=None):
    u = User(username=username, full_name=full_name, email=email, role=role)
    u.set_password(password)
    db.session.add(u)
    return u


def next_working_day(offset=1):
    d = date.today() + timedelta(days=offset)
    while d.weekday() > 4:
        d += timedelta(days=1)
    return d


def seed(sample=True):
    roles = {}
    for name in (ADMIN, DOCTOR, RECEPTIONIST):
        r = Role.query.filter_by(name=name).first() or Role(name=name)
        db.session.add(r)
        roles[name] = r
    db.session.flush()

    if not User.query.filter_by(username="admin").first():
        _user("admin", DEMO_PASSWORDS["admin"], "System Administrator", roles[ADMIN], "admin@hospital.local")
    db.session.commit()
    if not sample or Department.query.first():
        return

    depts = {n: Department(name=n, description=d) for n, d in [
        ("General Medicine", "Primary care and internal medicine"),
        ("Cardiology", "Heart and vascular care"),
        ("Pediatrics", "Child health"),
        ("Orthopedics", "Bones and joints"),
        ("Dermatology", "Skin care")]}
    db.session.add_all(depts.values())
    db.session.flush()

    _user("reception", DEMO_PASSWORDS["reception"], "Riya Verma", roles[RECEPTIONIST], "reception@hospital.local")

    doctors = []
    for uname, name, dept, spec, qual, fee, phone in [
        ("dr.sharma", "Anil Sharma", "General Medicine", "Internal Medicine", "MBBS, MD", 500, "9810000001"),
        ("dr.iyer", "Meera Iyer", "Cardiology", "Interventional Cardiology", "MBBS, DM", 900, "9810000002"),
        ("dr.khan", "Imran Khan", "Pediatrics", "Neonatology", "MBBS, MD", 600, "9810000003"),
        ("dr.das", "Soma Das", "Orthopedics", "Joint Replacement", "MBBS, MS", 800, "9810000004")]:
        u = _user(uname, DEMO_PASSWORDS["doctor"], name, roles[DOCTOR], f"{uname}@hospital.local")
        db.session.flush()
        d = Doctor(user_id=u.id, department_id=depts[dept].id, specialization=spec,
                   qualification=qual, phone=phone, consultation_fee=fee)
        db.session.add(d)
        db.session.flush()
        for wd in range(5):  # Mon-Fri 09-13 and 14-17
            db.session.add(DoctorSchedule(doctor_id=d.id, weekday=wd, start_time=time(9), end_time=time(13)))
            db.session.add(DoctorSchedule(doctor_id=d.id, weekday=wd, start_time=time(14), end_time=time(17)))
        db.session.add(DoctorSchedule(doctor_id=d.id, weekday=5, start_time=time(9), end_time=time(13)))
        doctors.append(d)

    for name, price in [("Complete Blood Count", 350), ("X-Ray", 600), ("ECG", 400),
                        ("Ultrasound", 1200), ("Dressing", 150)]:
        db.session.add(Service(name=name, price=price))

    patients = []
    for i, (fn, ln, dob, g, ph, bg) in enumerate([
        ("Rahul", "Mehta", date(1985, 3, 12), "Male", "9820000001", "B+"),
        ("Priya", "Nair", date(1992, 7, 24), "Female", "9820000002", "O+"),
        ("Aarav", "Singh", date(2016, 1, 5), "Male", "9820000003", "A+"),
        ("Sunita", "Rao", date(1968, 11, 30), "Female", "9820000004", "AB+"),
        ("Vikram", "Joshi", date(1977, 9, 2), "Male", "9820000005", "O-"),
        ("Neha", "Gupta", date(2000, 5, 18), "Female", "9820000006", "A-")], start=1):
        p = Patient(patient_code=f"PAT{i:06d}", first_name=fn, last_name=ln, dob=dob, gender=g,
                    phone=ph, blood_group=bg, address="Bokaro, Jharkhand")
        db.session.add(p)
        patients.append(p)
    db.session.flush()

    day = next_working_day()
    for i, p in enumerate(patients[:3]):
        start = datetime.combine(day, time(9, 30 * i))
        db.session.add(Appointment(patient_id=p.id, doctor_id=doctors[0].id, start_at=start,
                                   end_at=start + timedelta(minutes=30), reason="General check-up"))
    db.session.commit()
