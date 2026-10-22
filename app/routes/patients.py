from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from ..extensions import db
from ..models import Appointment, Patient
from ..security import ADMIN, DOCTOR, RECEPTIONIST, login_required, roles_required
from ..services import add_with_code, audit
from ..utils import parse_date, valid_email, valid_phone

bp = Blueprint("patients", __name__, url_prefix="/patients")
GENDERS = ["Male", "Female", "Other"]
BLOOD = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
FIELDS = ("first_name", "last_name", "dob", "gender", "phone", "email", "address",
          "blood_group", "emergency_contact")


def _my_doctor():
    doc = g.user.doctor_profile
    if not doc:
        abort(403)
    return doc


def _visible():
    q = Patient.query
    if g.user.role_name == DOCTOR:
        ids = db.session.query(Appointment.patient_id).filter(Appointment.doctor_id == _my_doctor().id)
        q = q.filter(Patient.id.in_(ids))
    return q


def _validate(form, existing_id=None):
    data = {k: form.get(k, "").strip() for k in FIELDS}
    errors = []
    for k in ("first_name", "last_name", "gender", "phone"):
        if not data[k]:
            errors.append(f"{k.replace('_', ' ').title()} is required.")
    dob = parse_date(data["dob"])
    if not dob:
        errors.append("A valid date of birth is required.")
    elif dob > date.today():
        errors.append("Date of birth cannot be in the future.")
    if data["gender"] and data["gender"] not in GENDERS:
        errors.append("Invalid gender.")
    if data["phone"] and not valid_phone(data["phone"]):
        errors.append("Enter a valid phone number (7-15 digits).")
    if data["email"] and not valid_email(data["email"]):
        errors.append("Enter a valid email address.")
    if data["blood_group"] and data["blood_group"] not in BLOOD:
        errors.append("Invalid blood group.")
    if not errors:
        dup = Patient.query.filter_by(phone=data["phone"], dob=dob).filter(
            db.func.lower(Patient.first_name) == data["first_name"].lower())
        if existing_id:
            dup = dup.filter(Patient.id != existing_id)
        if dup.first():
            errors.append("A patient with the same name, phone and date of birth already exists.")
    data["dob"] = dob
    for k in ("email", "address", "blood_group", "emergency_contact"):
        data[k] = data[k] or None
    return data, errors


@bp.route("/")
@login_required
def index():
    term = request.args.get("q", "").strip()
    q = _visible()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Patient.first_name.like(like), Patient.last_name.like(like),
                         (Patient.first_name + " " + Patient.last_name).like(like),
                         Patient.phone.like(like), Patient.patient_code.like(like)))
    page = request.args.get("page", 1, type=int)
    pager = q.order_by(Patient.id.desc()).paginate(page=page, per_page=15, error_out=False)
    return render_template("patients/list.html", pager=pager, q=term)


@bp.route("/new", methods=["GET", "POST"])
@roles_required(ADMIN, RECEPTIONIST)
def new():
    if request.method == "POST":
        data, errors = _validate(request.form)
        if not errors:
            p = Patient(created_by=g.user.id, **data)
            add_with_code(p, "patient_code", "PAT{id:06d}")
            audit("patient.create", p.patient_code)
            db.session.commit()
            flash(f"Patient {p.full_name} registered ({p.patient_code}).", "success")
            return redirect(url_for("patients.detail", pid=p.id))
        for e in errors:
            flash(e, "danger")
    return render_template("patients/form.html", f=request.form, patient=None,
                           genders=GENDERS, blood=BLOOD)


@bp.route("/<int:pid>")
@login_required
def detail(pid):
    p = _visible().filter(Patient.id == pid).first()
    if not p:
        if g.user.role_name == DOCTOR and db.session.get(Patient, pid):
            abort(403)
        abort(404)
    appts = Appointment.query.filter_by(patient_id=pid)
    if g.user.role_name == DOCTOR:
        appts = appts.filter_by(doctor_id=_my_doctor().id)
    appts = appts.order_by(Appointment.start_at.desc()).all()
    return render_template("patients/detail.html", p=p, appts=appts)


@bp.route("/<int:pid>/edit", methods=["GET", "POST"])
@roles_required(ADMIN, RECEPTIONIST)
def edit(pid):
    p = db.get_or_404(Patient, pid)
    if request.method == "POST":
        data, errors = _validate(request.form, existing_id=pid)
        if not errors:
            for k, v in data.items():
                setattr(p, k, v)
            audit("patient.update", p.patient_code)
            db.session.commit()
            flash("Patient details updated.", "success")
            return redirect(url_for("patients.detail", pid=p.id))
        for e in errors:
            flash(e, "danger")
        f = request.form
    else:
        f = {k: (getattr(p, k) or "") for k in FIELDS}
        f["dob"] = p.dob.isoformat()
    return render_template("patients/form.html", f=f, patient=p, genders=GENDERS, blood=BLOOD)


@bp.route("/<int:pid>/delete", methods=["POST"])
@roles_required(ADMIN)
def delete(pid):
    p = db.get_or_404(Patient, pid)
    if p.appointments or p.invoices:
        flash("Cannot delete a patient who has appointments or invoices (medical/financial records).", "danger")
        return redirect(url_for("patients.detail", pid=pid))
    audit("patient.delete", p.patient_code)
    db.session.delete(p)
    db.session.commit()
    flash("Patient deleted.", "info")
    return redirect(url_for("patients.index"))
