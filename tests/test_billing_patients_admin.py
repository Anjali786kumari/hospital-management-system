from datetime import date, datetime, timedelta
from decimal import Decimal

from app import db
from app.models import Appointment, Doctor, DoctorSchedule, Invoice, Patient, User
from .conftest import next_weekday


def new_invoice(c, pid):
    r = c.post("/billing/new", data={"patient_id": pid})
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def add_item(c, iid, price="1000", qty="1"):
    return c.post(f"/billing/{iid}/items", data={"description": "Lab", "quantity": qty, "unit_price": price})


def inv(app, iid):
    with app.app_context():
        i = db.session.get(Invoice, iid)
        return i.status, i.total, i.paid, i.balance


# ---------------------------------------------------------------- billing
def test_invoice_lifecycle_partial_then_paid(app, as_role, ids):
    c = as_role("reception")
    iid = new_invoice(c, ids["p1"])
    add_item(c, iid, "1000", "2")
    assert inv(app, iid)[:2] == ("unpaid", Decimal("2000"))
    c.post(f"/billing/{iid}/pay", data={"amount": "500", "method": "cash"})
    assert inv(app, iid)[0] == "partial"
    c.post(f"/billing/{iid}/pay", data={"amount": "1500", "method": "upi"})
    assert inv(app, iid) == ("paid", Decimal("2000"), Decimal("2000"), Decimal("0"))


def test_overpayment_and_bad_amounts_rejected(app, as_role, ids):
    c = as_role("reception")
    iid = new_invoice(c, ids["p1"])
    add_item(c, iid, "100")
    for amt in ("100.01", "0", "-5", "abc", "NaN", "Infinity", ""):
        c.post(f"/billing/{iid}/pay", data={"amount": amt, "method": "cash"})
    assert inv(app, iid)[2] == 0
    c.post(f"/billing/{iid}/pay", data={"amount": "10", "method": "bitcoin"})
    assert inv(app, iid)[2] == 0


def test_discount_rules(app, as_role, ids):
    c = as_role("reception")
    iid = new_invoice(c, ids["p1"])
    add_item(c, iid, "1000")
    c.post(f"/billing/{iid}/discount", data={"discount": "2000"})  # > subtotal
    assert inv(app, iid)[1] == 1000
    c.post(f"/billing/{iid}/discount", data={"discount": "250"})
    assert inv(app, iid)[1] == 750
    c.post(f"/billing/{iid}/pay", data={"amount": "750", "method": "card"})
    assert inv(app, iid)[0] == "paid"
    c.post(f"/billing/{iid}/discount", data={"discount": "900"})  # would be over-paid? no: total 100 < paid 750
    assert inv(app, iid)[1] == 750


def test_cannot_remove_item_if_overpaid(app, as_role, ids):
    c = as_role("reception")
    iid = new_invoice(c, ids["p1"])
    add_item(c, iid, "100")
    add_item(c, iid, "100")
    c.post(f"/billing/{iid}/pay", data={"amount": "200", "method": "cash"})
    with app.app_context():
        item_id = db.session.get(Invoice, iid).items[0].id
    c.post(f"/billing/{iid}/items/{item_id}/delete")
    assert inv(app, iid)[1] == 200


def test_void_rules(app, as_role, ids):
    rc, ad = as_role("reception"), as_role("admin")
    iid = new_invoice(rc, ids["p1"])
    add_item(rc, iid, "100")
    assert rc.post(f"/billing/{iid}/void").status_code == 403
    ad.post(f"/billing/{iid}/void")
    assert inv(app, iid)[0] == "void"
    rc.post(f"/billing/{iid}/pay", data={"amount": "10", "method": "cash"})  # blocked on void
    assert inv(app, iid)[2] == 0
    # paid invoice cannot be voided
    j = new_invoice(rc, ids["p2"])
    add_item(rc, j, "100")
    rc.post(f"/billing/{j}/pay", data={"amount": "100", "method": "cash"})
    ad.post(f"/billing/{j}/void")
    assert inv(app, j)[0] == "paid"


def test_appointment_invoice_autoadds_fee_and_blocks_duplicates(app, as_role, ids):
    start = datetime.now().replace(microsecond=0) - timedelta(hours=2)
    with app.app_context():
        a = Appointment(patient_id=ids["p1"], doctor_id=ids["d1"], start_at=start, end_at=start + timedelta(minutes=30),
                        status="completed")
        db.session.add(a)
        db.session.commit()
        aid = a.id
    c = as_role("reception")
    assert c.post("/billing/new", data={"appointment_id": aid}).status_code == 302
    c.post("/billing/new", data={"appointment_id": aid})  # duplicate
    with app.app_context():
        assert Invoice.query.count() == 1
        assert Invoice.query.one().total == 500


# --------------------------------------------------------------- patients
PAT = {"first_name": "Zed", "last_name": "Ito", "dob": "1985-05-05", "gender": "Male", "phone": "98765 43210"}


def test_register_patient_and_duplicate_detection(app, as_role):
    c = as_role("reception")
    assert c.post("/patients/new", data=PAT).status_code == 302
    r = c.post("/patients/new", data=PAT)
    assert r.status_code == 400 and b"already registered" in r.data
    with app.app_context():
        p = Patient.query.filter_by(first_name="Zed").one()
        assert p.patient_code.startswith("PT-") and p.phone == "9876543210"


def test_patient_validation(as_role):
    c = as_role("reception")
    future = (date.today() + timedelta(days=2)).isoformat()
    for bad in ({"dob": future}, {"phone": "12"}, {"gender": "x"}, {"email": "nope"}, {"first_name": " "}, {"dob": "31-02-2020"}):
        assert c.post("/patients/new", data={**PAT, **bad}).status_code == 400


def test_search_patients(as_role):
    c = as_role("doc1")
    assert b"Asha" in c.get("/patients/?q=Asha").data
    assert b"Asha" not in c.get("/patients/?q=zzzz").data


def test_cannot_deactivate_patient_with_upcoming_appointment(app, as_role, ids):
    as_role("reception").post("/appointments/new", data={"doctor_id": ids["d1"], "patient_id": ids["p1"],
                                                         "date": next_weekday(0).isoformat(), "slot": "09:00"})
    as_role("admin").post(f"/patients/{ids['p1']}/toggle")
    with app.app_context():
        assert db.session.get(Patient, ids["p1"]).is_active


# ------------------------------------------------------------------ admin
def test_admin_creates_doctor_with_login(app, as_role):
    c = as_role("admin")
    with app.app_context():
        from app.models import Department
        dept = Department.query.one().id
    r = c.post("/admin/doctors/new", data={"username": "dr.new", "full_name": "Dr. New", "email": "new@x.test",
               "password": "Passw0rdX", "department_id": dept, "specialization": "ENT", "license_no": "L99",
               "consultation_fee": "800"})
    assert r.status_code == 302
    nc = app.test_client()
    assert nc.post("/login", data={"username": "dr.new", "password": "Passw0rdX"}).status_code == 302
    assert nc.get("/billing/").status_code == 403


def test_user_validation_and_self_deactivation(app, as_role):
    c = as_role("admin")
    base = {"username": "rec2", "role": "receptionist", "full_name": "R2", "email": "r2@x.test", "password": "Passw0rd1"}
    assert c.post("/admin/users/new", data={**base, "password": "short"}).status_code == 400
    assert c.post("/admin/users/new", data={**base, "username": "Admin"}).status_code == 400   # duplicate, case-insens.
    assert c.post("/admin/users/new", data={**base, "role": "doctor"}).status_code == 400
    assert c.post("/admin/users/new", data=base).status_code == 302
    with app.app_context():
        admin_id = User.query.filter_by(username="admin").one().id
    r = c.post(f"/admin/users/{admin_id}/edit", data={"full_name": "Admin", "email": "admin@x.test"})  # is_active unchecked
    assert r.status_code == 400


def test_schedule_overlap_rejected_and_removal_guarded(app, as_role, ids):
    c = as_role("admin")
    c.post(f"/admin/doctors/{ids['d1']}/schedule", data={"weekday": "0", "start_time": "11:00", "end_time": "13:00"})
    with app.app_context():
        assert DoctorSchedule.query.filter_by(doctor_id=ids["d1"], weekday=0).count() == 1  # overlapped 09-12
    c.post(f"/admin/doctors/{ids['d1']}/schedule", data={"weekday": "0", "start_time": "14:00", "end_time": "13:00"})
    with app.app_context():
        assert DoctorSchedule.query.filter_by(doctor_id=ids["d1"], weekday=0).count() == 1
    # window with an upcoming booking cannot be removed
    as_role("reception").post("/appointments/new", data={"doctor_id": ids["d1"], "patient_id": ids["p1"],
                                                         "date": next_weekday(0).isoformat(), "slot": "09:00"})
    with app.app_context():
        sid = DoctorSchedule.query.filter_by(doctor_id=ids["d1"], weekday=0).one().id
    c.post(f"/admin/schedule/{sid}/delete")
    with app.app_context():
        assert db.session.get(DoctorSchedule, sid) is not None


def test_doctor_with_upcoming_appointments_cannot_be_deactivated(app, as_role, ids):
    as_role("reception").post("/appointments/new", data={"doctor_id": ids["d1"], "patient_id": ids["p1"],
                                                         "date": next_weekday(0).isoformat(), "slot": "09:00"})
    with app.app_context():
        uid = db.session.get(Doctor, ids["d1"]).user_id
    r = as_role("admin").post(f"/admin/users/{uid}/edit", data={"full_name": "Dr. Doc1", "email": "doc1@x.test"})
    assert r.status_code == 400


def test_department_delete_guard_and_reports_render(as_role):
    c = as_role("admin")
    assert c.get("/admin/reports").status_code == 200
    c.post("/admin/departments/1/delete")
    assert b"General" in c.get("/admin/departments").data  # still there: has doctors
