from datetime import date, datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from . import db
from .auth import login_required, roles_required
from .models import Appointment, Invoice, MedicalRecord, Patient
from .utils import (BLOOD_GROUPS, GENDERS, ValidationError, check_email, clean_phone,
                    flash_error, optional, parse_date, required)

bp = Blueprint("patients", __name__, url_prefix="/patients")


def _read_form(form):
    dob = parse_date(form.get("dob"), "Date of birth")
    if dob > date.today():
        raise ValidationError("Date of birth cannot be in the future.")
    gender = form.get("gender")
    if gender not in GENDERS:
        raise ValidationError("Select a gender.")
    bg = form.get("blood_group", "")
    if bg not in BLOOD_GROUPS:
        raise ValidationError("Invalid blood group.")
    email = form.get("email", "").strip()
    return dict(
        first_name=required(form, "first_name", "First name", 60),
        last_name=required(form, "last_name", "Last name", 60),
        dob=dob, gender=gender, phone=clean_phone(form.get("phone")),
        email=check_email(email) if email else None,
        address=optional(form, "address"), blood_group=bg or None,
        emergency_contact=optional(form, "emergency_contact", 120),
        allergies=optional(form, "allergies"),
    )


def _duplicate(data, exclude_id=None):
    q = Patient.query.filter(Patient.phone == data["phone"], Patient.dob == data["dob"],
                             db.func.lower(Patient.first_name) == data["first_name"].lower())
    if exclude_id:
        q = q.filter(Patient.id != exclude_id)
    return q.first()


@bp.route("/")
@roles_required("admin", "receptionist", "doctor")
def index():
    q = request.args.get("q", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    query = Patient.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Patient.first_name.like(like), Patient.last_name.like(like),
                                 Patient.patient_code.like(like), Patient.phone.like(like)))
    pg = query.order_by(Patient.last_name, Patient.first_name).paginate(page=page, per_page=15, error_out=False)
    return render_template("patients/list.html", pg=pg, q=q)


@bp.route("/new", methods=["GET", "POST"])
@roles_required("admin", "receptionist")
def new():
    if request.method == "POST":
        try:
            data = _read_form(request.form)
            dup = _duplicate(data)
            if dup:
                raise ValidationError(f"Patient already registered as {dup.patient_code} ({dup.name}).")
        except ValidationError as e:
            flash_error(e)
            return render_template("patients/form.html", p=None, f=request.form, genders=GENDERS, groups=BLOOD_GROUPS), 400
        p = Patient(patient_code=f"TMP{id(data)}", created_by=g.user.id, **data)
        db.session.add(p)
        db.session.flush()
        p.patient_code = f"PT-{p.id:06d}"
        db.session.commit()
        flash(f"Patient registered with ID {p.patient_code}.", "success")
        return redirect(url_for("patients.detail", pid=p.id))
    return render_template("patients/form.html", p=None, f={}, genders=GENDERS, groups=BLOOD_GROUPS)


@bp.route("/<int:pid>/edit", methods=["GET", "POST"])
@roles_required("admin", "receptionist")
def edit(pid):
    p = db.get_or_404(Patient, pid)
    if request.method == "POST":
        try:
            data = _read_form(request.form)
            if _duplicate(data, p.id):
                raise ValidationError("Another patient with the same name, phone and date of birth exists.")
        except ValidationError as e:
            flash_error(e)
            return render_template("patients/form.html", p=p, f=request.form, genders=GENDERS, groups=BLOOD_GROUPS), 400
        for k, v in data.items():
            setattr(p, k, v)
        db.session.commit()
        flash("Patient updated.", "success")
        return redirect(url_for("patients.detail", pid=p.id))
    f = {c.name: getattr(p, c.name) or "" for c in Patient.__table__.columns}
    return render_template("patients/form.html", p=p, f=f, genders=GENDERS, groups=BLOOD_GROUPS)


@bp.route("/<int:pid>")
@roles_required("admin", "receptionist", "doctor")
def detail(pid):
    p = db.get_or_404(Patient, pid)
    appts = Appointment.query.filter_by(patient_id=pid).order_by(Appointment.start_at.desc()).limit(20).all()
    invoices = records = []
    if g.user.role_name != "doctor":
        invoices = Invoice.query.filter_by(patient_id=pid).order_by(Invoice.id.desc()).all()
    if g.user.role_name != "receptionist":  # clinical data is hidden from front desk
        records = MedicalRecord.query.filter_by(patient_id=pid).order_by(MedicalRecord.created_at.desc()).all()
    return render_template("patients/detail.html", p=p, appts=appts, invoices=invoices, records=records)


@bp.route("/<int:pid>/toggle", methods=["POST"])
@roles_required("admin")
def toggle(pid):
    p = db.get_or_404(Patient, pid)
    if p.is_active:
        future = Appointment.query.filter(Appointment.patient_id == pid, Appointment.status == "scheduled",
                                          Appointment.start_at > datetime.now()).count()
        if future:
            flash("Cancel this patient's upcoming appointments before deactivating.", "danger")
            return redirect(url_for("patients.detail", pid=pid))
    p.is_active = not p.is_active
    db.session.commit()
    flash("Patient " + ("activated." if p.is_active else "deactivated."), "info")
    return redirect(url_for("patients.detail", pid=pid))
