from datetime import datetime, time, timedelta

from app import db
from app.models import Appointment
from app.services import SlotError, book
from .conftest import next_weekday


def post_book(c, d, p, day, slot, reason="x"):
    return c.post("/appointments/new", data={"doctor_id": d, "patient_id": p, "date": day.isoformat(),
                                             "slot": slot, "reason": reason})


def count(app, **kw):
    with app.app_context():
        return Appointment.query.filter_by(**kw).count()


def test_book_success(app, as_role, ids):
    day = next_weekday(0)
    r = post_book(as_role("reception"), ids["d1"], ids["p1"], day, "09:00")
    assert r.status_code == 302 and count(app) == 1


def test_double_booking_same_doctor_rejected(app, as_role, ids):
    c, day = as_role("reception"), next_weekday(0)
    post_book(c, ids["d1"], ids["p1"], day, "09:30")
    r = post_book(c, ids["d1"], ids["p2"], day, "09:30")
    assert r.status_code == 200 and b"already booked" in r.data
    assert count(app) == 1


def test_same_patient_cannot_overlap_with_other_doctor(app, as_role, ids):
    c, day = as_role("reception"), next_weekday(0)
    post_book(c, ids["d1"], ids["p1"], day, "10:00")
    r = post_book(c, ids["d2"], ids["p1"], day, "10:00")
    assert b"already has another appointment" in r.data and count(app) == 1


def test_different_doctor_same_time_ok(app, as_role, ids):
    c, day = as_role("reception"), next_weekday(0)
    post_book(c, ids["d1"], ids["p1"], day, "10:00")
    post_book(c, ids["d2"], ids["p2"], day, "10:00")
    assert count(app) == 2


def test_outside_working_hours_rejected(app, as_role, ids):
    r = post_book(as_role("reception"), ids["d1"], ids["p1"], next_weekday(0), "14:00")
    assert b"outside the doctor" in r.data and count(app) == 0


def test_misaligned_slot_rejected(app, as_role, ids):
    r = post_book(as_role("reception"), ids["d1"], ids["p1"], next_weekday(0), "09:10")
    assert b"outside the doctor" in r.data and count(app) == 0


def test_weekend_rejected(app, as_role, ids):
    r = post_book(as_role("reception"), ids["d1"], ids["p1"], next_weekday(6), "09:00")
    assert count(app) == 0


def test_past_booking_rejected(app, ids):
    with app.app_context():
        try:
            book(ids["d1"], ids["p1"], datetime(2020, 1, 6, 9, 0), "x", 1)
            assert False, "should raise"
        except SlotError as e:
            assert "past" in str(e)


def test_cancel_frees_slot(app, as_role, ids):
    c, day = as_role("reception"), next_weekday(1)
    post_book(c, ids["d1"], ids["p1"], day, "09:00")
    with app.app_context():
        aid = Appointment.query.one().id
    assert c.post(f"/appointments/{aid}/cancel").status_code == 302
    post_book(c, ids["d1"], ids["p2"], day, "09:00")  # same slot, other patient
    assert count(app, status="scheduled") == 1 and count(app, status="cancelled") == 1


def test_reschedule_conflict_and_success(app, as_role, ids):
    c, day = as_role("reception"), next_weekday(2)
    post_book(c, ids["d1"], ids["p1"], day, "09:00")
    post_book(c, ids["d1"], ids["p2"], day, "09:30")
    with app.app_context():
        a = Appointment.query.filter_by(patient_id=ids["p1"]).one().id
    r = c.post(f"/appointments/{a}/reschedule", data={"date": day.isoformat(), "slot": "09:30"})
    assert b"already booked" in r.data
    r = c.post(f"/appointments/{a}/reschedule", data={"date": day.isoformat(), "slot": "10:00"})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Appointment, a).start_at.time() == time(10, 0)


def test_reschedule_to_own_current_slot_is_allowed(app, as_role, ids):
    c, day = as_role("reception"), next_weekday(2)
    post_book(c, ids["d1"], ids["p1"], day, "09:00")
    with app.app_context():
        a = Appointment.query.one().id
    assert c.post(f"/appointments/{a}/reschedule", data={"date": day.isoformat(), "slot": "09:00"}).status_code == 302


def test_db_unique_backstop_blocks_duplicate_slot_key(app, ids):
    from sqlalchemy.exc import IntegrityError
    start = datetime.combine(next_weekday(0), time(9))
    with app.app_context():
        for pid in (ids["p1"], ids["p2"]):
            a = Appointment(patient_id=pid, doctor_id=ids["d1"], start_at=start, end_at=start + timedelta(minutes=30), status="scheduled")
            a.sync_slot_key()
            db.session.add(a)
        try:
            db.session.commit()
            assert False, "unique slot_key should have rejected the second row"
        except IntegrityError:
            db.session.rollback()


def test_doctor_cannot_touch_other_doctors_appointment(app, as_role, ids):
    day = next_weekday(0)
    post_book(as_role("reception"), ids["d2"], ids["p1"], day, "09:00")
    with app.app_context():
        aid = Appointment.query.one().id
    d1 = as_role("doc1")
    assert d1.post(f"/appointments/{aid}/cancel").status_code == 403
    assert d1.get(f"/appointments/{aid}/record").status_code == 403


def test_cannot_complete_future_appointment(app, as_role, ids):
    post_book(as_role("reception"), ids["d1"], ids["p1"], next_weekday(0), "09:00")
    with app.app_context():
        aid = Appointment.query.one().id
    c = as_role("doc1")
    c.post(f"/appointments/{aid}/complete")
    assert count(app, status="completed") == 0
    assert as_role("reception").post(f"/appointments/{aid}/complete").status_code == 403


def test_consultation_record_completes_appointment(app, as_role, ids):
    start = datetime.now().replace(second=0, microsecond=0) - timedelta(hours=1)
    with app.app_context():
        a = Appointment(patient_id=ids["p1"], doctor_id=ids["d1"], start_at=start, end_at=start + timedelta(minutes=30))
        a.sync_slot_key()
        db.session.add(a)
        db.session.commit()
        aid = a.id
    c = as_role("doc1")
    bad = c.post(f"/appointments/{aid}/record", data={"diagnosis": "", "medicine": "Aspirin"})
    assert bad.status_code == 400
    ok = c.post(f"/appointments/{aid}/record", data={"diagnosis": "Flu", "medicine": ["Paracetamol", "", ""],
                                                      "dosage": ["1 tab", "", ""], "frequency": ["BD", "", ""],
                                                      "duration_days": ["3", "", ""]})
    assert ok.status_code == 302
    assert count(app, status="completed") == 1
    # other doctor cannot read it
    with app.app_context():
        from app.models import MedicalRecord
        rid = MedicalRecord.query.one().id
    assert as_role("doc2").get(f"/records/{rid}").status_code == 403
    assert c.get(f"/records/{rid}").status_code == 200
