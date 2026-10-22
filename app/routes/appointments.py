from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (APPT_CANCELLED, APPT_COMPLETED, APPT_NO_SHOW, APPT_SCHEDULED,
                      APPT_STATUSES, Appointment, Department, Doctor, Patient)
from ..security import ADMIN, DOCTOR, RECEPTIONIST, login_required, roles_required
from ..services import (BookingError, audit, book_appointment, create_invoice,
                        free_slots, reschedule_appointment)
from ..utils import parse_date, parse_time

bp = Blueprint("appointments", __name__, url_prefix="/appointments")


def _get_appt(aid):
    appt = db.get_or_404(Appointment, aid)
    if g.user.role_name == DOCTOR and (not g.user.doctor_profile or appt.doctor_id != g.user.doctor_profile.id):
        abort(403)
    return appt


def _booking_context():
    doctors = [d for d in Doctor.query.all() if d.user.active]
    patients = Patient.query.order_by(Patient.first_name, Patient.last_name).limit(1000).all()
    return dict(doctors=doctors, patients=patients, today=datetime.now().date())


@bp.route("/")
@login_required
def index():
    when = request.args.get("when", "today")
    status = request.args.get("status", "")
    doctor_id = request.args.get("doctor_id", type=int)
    day = parse_date(request.args.get("date", ""))
    q = Appointment.query.join(Appointment.patient)
    now = datetime.now()
    midnight = datetime.combine(now.date(), datetime.min.time())
    if g.user.role_name == DOCTOR:
        if not g.user.doctor_profile:
            abort(403)
        q = q.filter(Appointment.doctor_id == g.user.doctor_profile.id)
    elif doctor_id:
        q = q.filter(Appointment.doctor_id == doctor_id)
    if day:
        q = q.filter(Appointment.start_at >= datetime.combine(day, datetime.min.time()),
                     Appointment.start_at < datetime.combine(day, datetime.min.time()) + timedelta(days=1))
        when = "date"
    elif when == "today":
        q = q.filter(Appointment.start_at >= midnight, Appointment.start_at < midnight + timedelta(days=1))
    elif when == "upcoming":
        q = q.filter(Appointment.start_at >= midnight + timedelta(days=1))
    elif when == "past":
        q = q.filter(Appointment.start_at < midnight)
    if status in APPT_STATUSES:
        q = q.filter(Appointment.status == status)
    order = Appointment.start_at.desc() if when == "past" else Appointment.start_at
    pager = q.order_by(order).paginate(page=request.args.get("page", 1, type=int),
                                       per_page=20, error_out=False)
    doctors = Doctor.query.all() if g.user.role_name != DOCTOR else []
    return render_template("appointments/list.html", pager=pager, when=when, status=status,
                           statuses=APPT_STATUSES, doctors=doctors, doctor_id=doctor_id,
                           day=request.args.get("date", ""))


@bp.route("/slots")
@roles_required(ADMIN, RECEPTIONIST)
def slots():
    doctor_id = request.args.get("doctor_id", type=int)
    day = parse_date(request.args.get("date", ""))
    if not doctor_id or not day:
        return jsonify(slots=[], error="doctor_id and date are required"), 400
    exclude = request.args.get("exclude", type=int)
    return jsonify(slots=[s.strftime("%H:%M") for s in free_slots(doctor_id, day, exclude)])


def _parse_start(form):
    day, tm = parse_date(form.get("date")), parse_time(form.get("slot"))
    if not day or not tm:
        raise BookingError("Choose a date and an available time slot.")
    return datetime.combine(day, tm)


@bp.route("/book", methods=["GET", "POST"])
@roles_required(ADMIN, RECEPTIONIST)
def book():
    f = request.form if request.method == "POST" else {
        "patient_id": request.args.get("patient_id", ""), "doctor_id": request.args.get("doctor_id", "")}
    if request.method == "POST":
        try:
            patient_id, doctor_id = int(f.get("patient_id", "")), int(f.get("doctor_id", ""))
            if not db.session.get(Patient, patient_id):
                raise BookingError("Select a valid patient.")
            appt = book_appointment(patient_id, doctor_id, _parse_start(f),
                                    f.get("reason", "").strip()[:255], g.user.id)
            flash("Appointment booked.", "success")
            return redirect(url_for("appointments.detail", aid=appt.id))
        except ValueError:
            db.session.rollback()
            flash("Select a patient and a doctor.", "danger")
        except BookingError as e:
            db.session.rollback()
            flash(str(e), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Could not save the appointment. Please try again.", "danger")
    return render_template("appointments/book.html", f=f, appt=None, **_booking_context())


@bp.route("/<int:aid>")
@login_required
def detail(aid):
    return render_template("appointments/detail.html", a=_get_appt(aid))


@bp.route("/<int:aid>/reschedule", methods=["GET", "POST"])
@roles_required(ADMIN, RECEPTIONIST)
def reschedule(aid):
    appt = db.get_or_404(Appointment, aid)
    if appt.status != APPT_SCHEDULED:
        flash("Only scheduled appointments can be rescheduled.", "danger")
        return redirect(url_for("appointments.detail", aid=aid))
    f = request.form if request.method == "POST" else {
        "date": appt.start_at.strftime("%Y-%m-%d"), "slot": appt.start_at.strftime("%H:%M"),
        "reason": appt.reason or ""}
    if request.method == "POST":
        try:
            reschedule_appointment(appt, _parse_start(f))
            flash("Appointment rescheduled.", "success")
            return redirect(url_for("appointments.detail", aid=aid))
        except BookingError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("appointments/book.html", f=f, appt=appt, **_booking_context())


@bp.route("/<int:aid>/cancel", methods=["POST"])
@login_required
def cancel(aid):
    appt = _get_appt(aid)
    if appt.status != APPT_SCHEDULED:
        flash("Only scheduled appointments can be cancelled.", "danger")
    else:
        appt.status = APPT_CANCELLED
        appt.cancel_reason = request.form.get("cancel_reason", "").strip()[:255] or None
        audit("appointment.cancel", f"appt={appt.id}")
        db.session.commit()
        flash("Appointment cancelled. The slot is free again.", "info")
    return redirect(url_for("appointments.detail", aid=aid))


@bp.route("/<int:aid>/no-show", methods=["POST"])
@login_required
def no_show(aid):
    appt = _get_appt(aid)
    if appt.status != APPT_SCHEDULED:
        flash("Only scheduled appointments can be marked no-show.", "danger")
    elif appt.start_at > datetime.now():
        flash("You cannot mark a future appointment as no-show.", "danger")
    else:
        appt.status = APPT_NO_SHOW
        audit("appointment.no_show", f"appt={appt.id}")
        db.session.commit()
        flash("Marked as no-show.", "info")
    return redirect(url_for("appointments.detail", aid=aid))


@bp.route("/<int:aid>/complete", methods=["GET", "POST"])
@roles_required(ADMIN, DOCTOR)
def complete(aid):
    appt = _get_appt(aid)
    if appt.status != APPT_SCHEDULED:
        flash("Only scheduled appointments can be completed.", "danger")
        return redirect(url_for("appointments.detail", aid=aid))
    if appt.start_at.date() > datetime.now().date():
        flash("This appointment is in the future and cannot be completed yet.", "danger")
        return redirect(url_for("appointments.detail", aid=aid))
    if request.method == "POST":
        diagnosis = request.form.get("diagnosis", "").strip()
        if not diagnosis:
            flash("Diagnosis is required to complete a consultation.", "danger")
        else:
            appt.diagnosis = diagnosis
            appt.prescription = request.form.get("prescription", "").strip() or None
            appt.notes = request.form.get("notes", "").strip() or None
            appt.status = APPT_COMPLETED
            if not appt.invoice:
                create_invoice(appt.patient_id, g.user.id, appt)
            audit("appointment.complete", f"appt={appt.id}")
            db.session.commit()
            flash("Consultation recorded and invoice generated.", "success")
            return redirect(url_for("appointments.detail", aid=aid))
    return render_template("appointments/complete.html", a=appt, f=request.form)
