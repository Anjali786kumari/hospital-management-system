from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db
from .auth import roles_required
from .models import Appointment, Doctor, Patient
from .services import SlotError, book, generate_slots
from .utils import ValidationError, flash_error, optional, parse_date, parse_time

bp = Blueprint("appointments", __name__, url_prefix="/appointments")
ALL = ("admin", "receptionist", "doctor")


def _own_or_403(appt):
    """Doctors may only touch their own appointments."""
    if g.user.role_name == "doctor" and appt.doctor_id != g.user.doctor.id:
        abort(403, "This appointment belongs to another doctor.")


@bp.route("/")
@roles_required(*ALL)
def index():
    day = request.args.get("date", "")
    status = request.args.get("status", "")
    doctor_id = request.args.get("doctor_id", type=int)
    q = Appointment.query
    if g.user.role_name == "doctor":
        doctor_id = g.user.doctor.id
    if doctor_id:
        q = q.filter_by(doctor_id=doctor_id)
    if status:
        q = q.filter_by(status=status)
    if day:
        try:
            d = parse_date(day)
            q = q.filter(Appointment.start_at >= datetime.combine(d, datetime.min.time()),
                         Appointment.start_at < datetime.combine(d + timedelta(days=1), datetime.min.time()))
        except ValidationError as e:
            flash_error(e)
    page = max(request.args.get("page", 1, type=int), 1)
    pg = q.order_by(Appointment.start_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("appointments/list.html", pg=pg, day=day, status=status, doctor_id=doctor_id,
                           doctors=Doctor.query.all())


@bp.route("/new", methods=["GET", "POST"])
@roles_required("admin", "receptionist")
def new():
    src = request.form if request.method == "POST" else request.args
    doctor_id = src.get("doctor_id", type=int)
    patient_id = src.get("patient_id", type=int)
    day_s = src.get("date", "")
    slots, day = [], None
    if day_s:
        try:
            day = parse_date(day_s)
            if day < date.today():
                raise ValidationError("Choose today or a future date.")
        except ValidationError as e:
            flash_error(e)
            day = None
    if doctor_id and day:
        slots = generate_slots(doctor_id, day)

    if request.method == "POST":
        try:
            if not (doctor_id and patient_id and day):
                raise ValidationError("Select patient, doctor and date.")
            start = datetime.combine(day, parse_time(request.form.get("slot"), "Slot"))
            book(doctor_id, patient_id, start, optional(request.form, "reason"), g.user.id)
            db.session.commit()
            flash("Appointment booked.", "success")
            return redirect(url_for("appointments.index", date=day.isoformat()))
        except (ValidationError, SlotError) as e:
            db.session.rollback()
            flash_error(e)
            if doctor_id and day:
                slots = generate_slots(doctor_id, day)
    return render_template("appointments/new.html", doctors=Doctor.query.all(),
                           patients=Patient.query.filter_by(is_active=True).order_by(Patient.first_name).limit(500).all(),
                           doctor_id=doctor_id, patient_id=patient_id, day=day_s, slots=slots,
                           today=date.today().isoformat(), appt=None)


@bp.route("/<int:aid>/reschedule", methods=["GET", "POST"])
@roles_required("admin", "receptionist")
def reschedule(aid):
    appt = db.get_or_404(Appointment, aid)
    if appt.status != "scheduled":
        flash("Only scheduled appointments can be rescheduled.", "danger")
        return redirect(url_for("appointments.index"))
    src = request.form if request.method == "POST" else request.args
    day_s = src.get("date", appt.start_at.date().isoformat())
    slots, day = [], None
    try:
        day = parse_date(day_s)
        slots = generate_slots(appt.doctor_id, day, exclude_id=appt.id)
    except ValidationError as e:
        flash_error(e)
    if request.method == "POST":
        try:
            start = datetime.combine(day, parse_time(request.form.get("slot"), "Slot"))
            book(appt.doctor_id, appt.patient_id, start, None, g.user.id, appt=appt)
            db.session.commit()
            flash("Appointment rescheduled.", "success")
            return redirect(url_for("appointments.index", date=day.isoformat()))
        except (ValidationError, SlotError, TypeError) as e:
            db.session.rollback()
            flash_error(e if not isinstance(e, TypeError) else "Choose a date and slot.")
            appt = db.session.get(Appointment, aid)
            if day:
                slots = generate_slots(appt.doctor_id, day, exclude_id=appt.id)
    return render_template("appointments/new.html", doctors=[appt.doctor], patients=[appt.patient],
                           doctor_id=appt.doctor_id, patient_id=appt.patient_id, day=day_s, slots=slots,
                           today=date.today().isoformat(), appt=appt)


def _set_status(aid, new_status):
    appt = db.get_or_404(Appointment, aid)
    _own_or_403(appt)
    role = g.user.role_name
    if appt.status != "scheduled":
        flash(f"Appointment is already {appt.status.replace('_', ' ')}.", "danger")
    elif new_status == "completed" and role == "receptionist":
        abort(403, "Only a doctor can complete a consultation.")
    elif new_status == "completed" and appt.start_at > datetime.now():
        flash("Cannot complete an appointment before it starts.", "danger")
    elif new_status == "no_show" and appt.start_at > datetime.now():
        flash("Cannot mark a future appointment as no-show.", "danger")
    else:
        appt.status = new_status
        appt.sync_slot_key()  # cancelled / no_show free the slot immediately
        db.session.commit()
        flash(f"Appointment marked {new_status.replace('_', ' ')}.", "success")
    return redirect(request.referrer or url_for("appointments.index"))


@bp.route("/<int:aid>/cancel", methods=["POST"])
@roles_required(*ALL)
def cancel(aid):
    return _set_status(aid, "cancelled")


@bp.route("/<int:aid>/no-show", methods=["POST"])
@roles_required(*ALL)
def no_show(aid):
    return _set_status(aid, "no_show")


@bp.route("/<int:aid>/complete", methods=["POST"])
@roles_required("admin", "doctor")
def complete(aid):
    return _set_status(aid, "completed")
