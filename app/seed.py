"""Demo data: roles, users, departments, doctors + weekly hours, patients, appointments, invoices."""
import os
import random
from datetime import date, datetime, time, timedelta

from . import db
from .models import (Appointment, Department, Doctor, DoctorSchedule, Invoice, InvoiceItem,
                     MedicalRecord, Patient, Payment, Prescription, Role, User)

ROLES = {"admin": "Full system access", "doctor": "Clinical access to own patients",
         "receptionist": "Front desk: patients, appointments, billing"}


def _user(username, name, role, pw):
    u = User(username=username, full_name=name, email=f"{username}@hospital.example", role=Role.query.filter_by(name=role).one())
    u.set_password(pw)
    db.session.add(u)
    return u


def seed():
    db.create_all()
    if Role.query.first():
        print("Database already seeded; skipping.")
        return
    random.seed(7)
    for n, d in ROLES.items():
        db.session.add(Role(name=n, description=d))
    db.session.flush()
    _user("admin", "System Admin", "admin", os.environ.get("HMS_ADMIN_PASSWORD", "Admin@123"))
    _user("reception", "Riya Front Desk", "receptionist", "Reception@123")

    depts = {n: Department(name=n) for n in ("General Medicine", "Cardiology", "Pediatrics", "Orthopedics")}
    db.session.add_all(depts.values())
    docs = []
    for uname, name, dept, spec, fee in [
        ("dr.sharma", "Anil Sharma", "General Medicine", "Physician", 500),
        ("dr.iyer", "Meera Iyer", "Cardiology", "Cardiologist", 1200),
        ("dr.khan", "Imran Khan", "Pediatrics", "Pediatrician", 700),
        ("dr.rao", "Suresh Rao", "Orthopedics", "Orthopedic Surgeon", 900),
    ]:
        u = _user(uname, f"Dr. {name}", "doctor", "Doctor@123")
        doc = Doctor(user=u, department=depts[dept], specialization=spec, license_no=f"MCI-{random.randint(10000, 99999)}",
                     phone="98" + "".join(random.choices("0123456789", k=8)), consultation_fee=fee)
        for wd in range(5):
            doc.schedules.append(DoctorSchedule(weekday=wd, start_time=time(9), end_time=time(13)))
            doc.schedules.append(DoctorSchedule(weekday=wd, start_time=time(15), end_time=time(18)))
        doc.schedules.append(DoctorSchedule(weekday=5, start_time=time(10), end_time=time(13)))
        db.session.add(doc)
        docs.append(doc)
    db.session.flush()

    names = ["Aarav Patel", "Diya Nair", "Vihaan Gupta", "Ananya Reddy", "Kabir Singh", "Isha Mehta",
             "Rohan Das", "Sara Ali", "Arjun Verma", "Neha Joshi", "Karan Malhotra", "Pooja Menon"]
    pats = []
    for i, n in enumerate(names):
        f, l = n.split()
        p = Patient(patient_code=f"TMP{i}", first_name=f, last_name=l, dob=date(random.randint(1950, 2018), random.randint(1, 12), random.randint(1, 28)),
                    gender=random.choice(["Male", "Female"]), phone=f"9{random.randint(100000000, 999999999)}",
                    blood_group=random.choice(["A+", "B+", "O+", "AB+", "O-"]), address="Bengaluru, India")
        db.session.add(p)
        pats.append(p)
    db.session.flush()
    for p in pats:
        p.patient_code = f"PT-{p.id:06d}"

    # past, completed consultations with invoices (last week) + future bookings
    today = date.today()
    n_inv = 0
    for i in range(8):
        doc, pat = docs[i % 4], pats[i]
        day = today - timedelta(days=random.randint(1, 6))
        while day.weekday() > 4:
            day -= timedelta(days=1)
        start = datetime.combine(day, time(9, 0)) + timedelta(minutes=30 * random.randint(0, 5))
        if Appointment.query.filter_by(doctor_id=doc.id, start_at=start).first():
            continue
        a = Appointment(patient=pat, doctor=doc, start_at=start, end_at=start + timedelta(minutes=30), status="completed", reason="Check-up")
        db.session.add(a)
        db.session.flush()
        a.sync_slot_key()
        rec = MedicalRecord(appointment_id=a.id, patient_id=pat.id, doctor_id=doc.id, diagnosis="Routine review; stable.",
                            vitals="BP 120/80, Pulse 72", notes="Advised rest and hydration.")
        rec.prescriptions.append(Prescription(medicine="Paracetamol 500mg", dosage="1 tablet", frequency="Twice daily", duration_days=3))
        db.session.add(rec)
        n_inv += 1
        inv = Invoice(invoice_no=f"INV-SEED-{n_inv}", patient_id=pat.id, appointment_id=a.id, created_by=1)
        inv.items.append(InvoiceItem(description=f"Consultation – {doc.name}", quantity=1, unit_price=doc.consultation_fee))
        db.session.add(inv)
        db.session.flush()
        inv.invoice_no = f"INV-{inv.created_at:%Y}-{inv.id:06d}"
        if i % 3 != 2:
            amt = inv.total if i % 3 == 0 else inv.total / 2
            inv.payments.append(Payment(amount=amt, method=random.choice(["cash", "upi", "card"]), paid_at=start + timedelta(hours=1), received_by=2))
        db.session.flush()
        inv.refresh_status()

    from .services import book
    booked = 0
    day = today
    while booked < 6:
        day += timedelta(days=1)
        if day.weekday() > 4:
            continue
        book(docs[booked % 4].id, pats[8 + booked % 4].id, datetime.combine(day, time(10, 0)), "Follow-up visit", 2)
        booked += 1
    db.session.commit()
