"""Appointment scheduling rules, including time-slot conflict detection.

A booking is accepted only when ALL of these hold:
  1. start time lies on a bookable slot inside one of the doctor's weekly windows
  2. start time is in the future
  3. the doctor is active
  4. no active appointment of the same doctor overlaps  [start, end)
  5. no active appointment of the same patient overlaps (a patient cannot be in
     two places at once)
Concurrency: the doctor row is locked (SELECT ... FOR UPDATE) for the duration
of the transaction, and `appointments.slot_key` is UNIQUE as a DB backstop.
"""
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from . import db
from .models import ACTIVE_APPT, Appointment, Doctor, DoctorSchedule, Patient


class SlotError(Exception):
    pass


def slot_len():
    return timedelta(minutes=current_app.config["SLOT_MINUTES"])


def _overlaps(query, start, end, exclude_id):
    q = query.filter(Appointment.status.in_(ACTIVE_APPT),
                     Appointment.start_at < end, Appointment.end_at > start)
    if exclude_id:
        q = q.filter(Appointment.id != exclude_id)
    return q


def generate_slots(doctor_id, day, exclude_id=None):
    """All slots for a doctor on `day`, each flagged available or not."""
    step = slot_len()
    windows = DoctorSchedule.query.filter_by(doctor_id=doctor_id, weekday=day.weekday()) \
        .order_by(DoctorSchedule.start_time).all()
    day_start = datetime.combine(day, datetime.min.time())
    booked = {a.start_at for a in Appointment.query.filter(
        Appointment.doctor_id == doctor_id, Appointment.status.in_(ACTIVE_APPT),
        Appointment.start_at >= day_start, Appointment.start_at < day_start + timedelta(days=1))
        if a.id != exclude_id}
    now = datetime.now()
    slots = []
    for w in windows:
        cur, end = datetime.combine(day, w.start_time), datetime.combine(day, w.end_time)
        while cur + step <= end:
            slots.append({"start": cur, "end": cur + step,
                          "available": cur > now and cur not in booked})
            cur += step
    return slots


def book(doctor_id, patient_id, start_at, reason, user_id, appt=None):
    """Create a new appointment, or move `appt` to a new slot. Raises SlotError."""
    # Lock the doctor row so concurrent bookings for the same doctor serialise.
    doctor = db.session.query(Doctor).filter_by(id=doctor_id).with_for_update().first()
    if doctor is None or not doctor.user.is_active:
        raise SlotError("This doctor is not available for booking.")
    patient = db.session.get(Patient, patient_id)
    if patient is None or not patient.is_active:
        raise SlotError("Select an active patient.")

    end_at = start_at + slot_len()
    exclude = appt.id if appt else None

    if start_at <= datetime.now():
        raise SlotError("Appointments cannot be booked in the past.")
    valid = {s["start"] for s in generate_slots(doctor_id, start_at.date(), exclude)}
    if start_at not in valid:
        raise SlotError("That time is outside the doctor's working hours or not on a slot boundary.")
    if _overlaps(Appointment.query.filter_by(doctor_id=doctor_id), start_at, end_at, exclude).first():
        raise SlotError(f"Dr. {doctor.name} is already booked at {start_at:%d %b %Y %I:%M %p}.")
    if _overlaps(Appointment.query.filter_by(patient_id=patient_id), start_at, end_at, exclude).first():
        raise SlotError(f"{patient.name} already has another appointment at that time.")

    if appt is None:
        appt = Appointment(patient_id=patient_id, doctor_id=doctor_id, created_by=user_id)
        db.session.add(appt)
    appt.start_at, appt.end_at, appt.status = start_at, end_at, "scheduled"
    if reason is not None:
        appt.reason = reason
    appt.sync_slot_key()
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise SlotError("That slot was just taken by someone else. Please pick another.")
    return appt
