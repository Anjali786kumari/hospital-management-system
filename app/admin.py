from datetime import date, datetime, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func

from . import db
from .auth import login_required, roles_required
from .models import (Appointment, Department, Doctor, DoctorSchedule, Invoice, Patient,
                     Payment, Role, User)
from .utils import (USERNAME_RE, ValidationError, check_email, check_password, clean_phone,
                    flash_error, optional, parse_money, parse_time, required)

bp = Blueprint("admin", __name__)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ------------------------------------------------------------------ helpers
def _user_fields(f, user=None, need_password=True):
    full_name = required(f, "full_name", "Full name")
    email = check_email(f.get("email", "").strip())
    clash = User.query.filter(func.lower(User.email) == email)
    if user:
        clash = clash.filter(User.id != user.id)
    if clash.first():
        raise ValidationError("That email is already used by another account.")
    pw = f.get("password", "")
    if pw or need_password:
        check_password(pw)
    return full_name, email, pw


def _new_username(f):
    u = f.get("username", "").strip().lower()
    if not USERNAME_RE.match(u):
        raise ValidationError("Username: 3–30 chars, lowercase letters, digits, '.' or '_'.")
    if User.query.filter(func.lower(User.username) == u).first():
        raise ValidationError("That username is taken.")
    return u


def _future_appts(doctor):
    return Appointment.query.filter(Appointment.doctor_id == doctor.id, Appointment.status == "scheduled",
                                    Appointment.start_at > datetime.now()).count()


# -------------------------------------------------------------------- users
@bp.route("/admin/users")
@roles_required("admin")
def users():
    return render_template("admin/users.html", users=User.query.order_by(User.role_id, User.full_name).all())


@bp.route("/admin/users/new", methods=["GET", "POST"])
@roles_required("admin")
def user_new():
    f = request.form
    if request.method == "POST":
        try:
            role = Role.query.filter_by(name=f.get("role")).first()
            if not role or role.name == "doctor":
                raise ValidationError("Choose Admin or Receptionist. Doctors are created from the Doctors page.")
            username = _new_username(f)
            name, email, pw = _user_fields(f)
            u = User(username=username, full_name=name, email=email, role=role)
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            flash("User created.", "success")
            return redirect(url_for("admin.users"))
        except ValidationError as e:
            flash_error(e)
            return render_template("admin/user_form.html", u=None, f=f), 400
    return render_template("admin/user_form.html", u=None, f={})


@bp.route("/admin/users/<int:uid>/edit", methods=["GET", "POST"])
@roles_required("admin")
def user_edit(uid):
    u = db.get_or_404(User, uid)
    f = request.form
    if request.method == "POST":
        try:
            name, email, pw = _user_fields(f, u, need_password=False)
            active = f.get("is_active") == "on"
            if u.id == g.user.id and not active:
                raise ValidationError("You cannot deactivate your own account.")
            if u.doctor and not active and _future_appts(u.doctor):
                raise ValidationError("This doctor has upcoming appointments. Reschedule or cancel them first.")
            u.full_name, u.email, u.is_active = name, email, active
            if pw:
                u.set_password(pw)
            db.session.commit()
            flash("User updated.", "success")
            return redirect(url_for("admin.users"))
        except ValidationError as e:
            flash_error(e)
            return render_template("admin/user_form.html", u=u, f=f), 400
    return render_template("admin/user_form.html", u=u,
                           f={"full_name": u.full_name, "email": u.email, "is_active": "on" if u.is_active else ""})


# -------------------------------------------------------------- departments
@bp.route("/admin/departments", methods=["GET", "POST"])
@roles_required("admin")
def departments():
    if request.method == "POST":
        try:
            name = required(request.form, "name", "Department name", 80)
            if Department.query.filter(func.lower(Department.name) == name.lower()).first():
                raise ValidationError("Department already exists.")
            db.session.add(Department(name=name, description=optional(request.form, "description")))
            db.session.commit()
            flash("Department added.", "success")
        except ValidationError as e:
            flash_error(e)
        return redirect(url_for("admin.departments"))
    return render_template("admin/departments.html", depts=Department.query.order_by(Department.name).all())


@bp.route("/admin/departments/<int:did>/delete", methods=["POST"])
@roles_required("admin")
def department_delete(did):
    d = db.get_or_404(Department, did)
    if d.doctors:
        flash("Cannot delete a department that still has doctors.", "danger")
    else:
        db.session.delete(d)
        db.session.commit()
        flash("Department deleted.", "info")
    return redirect(url_for("admin.departments"))


# ------------------------------------------------------------------ doctors
@bp.route("/doctors")
@login_required
def doctors():
    return render_template("admin/doctors.html", doctors=Doctor.query.join(User).order_by(User.full_name).all(),
                           weekdays=WEEKDAYS)


def _doctor_fields(f, doc=None):
    dept = db.session.get(Department, f.get("department_id", type=int) or 0)
    if not dept:
        raise ValidationError("Select a department.")
    lic = required(f, "license_no", "Licence number", 40)
    clash = Doctor.query.filter_by(license_no=lic)
    if doc:
        clash = clash.filter(Doctor.id != doc.id)
    if clash.first():
        raise ValidationError("That licence number is already registered.")
    phone = f.get("phone", "").strip()
    return dict(department=dept, license_no=lic, specialization=required(f, "specialization", "Specialization", 100),
                phone=clean_phone(phone) if phone else None,
                consultation_fee=parse_money(f.get("consultation_fee"), "Consultation fee", allow_zero=True))


@bp.route("/admin/doctors/new", methods=["GET", "POST"])
@roles_required("admin")
def doctor_new():
    f = request.form
    if request.method == "POST":
        try:
            username = _new_username(f)
            name, email, pw = _user_fields(f)
            fields = _doctor_fields(f)
            u = User(username=username, full_name=name, email=email, role=Role.query.filter_by(name="doctor").one())
            u.set_password(pw)
            db.session.add(Doctor(user=u, **fields))
            db.session.commit()
            flash("Doctor created. Add working hours next.", "success")
            return redirect(url_for("admin.doctor_schedule", did=u.doctor.id))
        except ValidationError as e:
            flash_error(e)
            return render_template("admin/doctor_form.html", doc=None, f=f, depts=Department.query.all()), 400
    return render_template("admin/doctor_form.html", doc=None, f={}, depts=Department.query.all())


@bp.route("/admin/doctors/<int:did>/edit", methods=["GET", "POST"])
@roles_required("admin")
def doctor_edit(did):
    doc = db.get_or_404(Doctor, did)
    f = request.form
    if request.method == "POST":
        try:
            name, email, pw = _user_fields(f, doc.user, need_password=False)
            fields = _doctor_fields(f, doc)
            doc.user.full_name, doc.user.email = name, email
            if pw:
                doc.user.set_password(pw)
            for k, v in fields.items():
                setattr(doc, k, v)
            db.session.commit()
            flash("Doctor updated.", "success")
            return redirect(url_for("admin.doctors"))
        except ValidationError as e:
            flash_error(e)
            return render_template("admin/doctor_form.html", doc=doc, f=f, depts=Department.query.all()), 400
    vals = {"full_name": doc.user.full_name, "email": doc.user.email, "department_id": doc.department_id,
            "specialization": doc.specialization, "license_no": doc.license_no, "phone": doc.phone or "",
            "consultation_fee": doc.consultation_fee}
    return render_template("admin/doctor_form.html", doc=doc, f=vals, depts=Department.query.all())


@bp.route("/admin/doctors/<int:did>/schedule", methods=["GET", "POST"])
@roles_required("admin")
def doctor_schedule(did):
    doc = db.get_or_404(Doctor, did)
    if request.method == "POST":
        try:
            days = request.form.getlist("weekday", type=int)
            if not days or any(d not in range(7) for d in days):
                raise ValidationError("Select at least one weekday.")
            start, end = parse_time(request.form.get("start_time"), "Start time"), parse_time(request.form.get("end_time"), "End time")
            if end <= start:
                raise ValidationError("End time must be after start time.")
            for d in days:
                for w in doc.schedules:
                    if w.weekday == d and start < w.end_time and end > w.start_time:
                        raise ValidationError(f"Overlaps an existing {WEEKDAYS[d]} window "
                                              f"({w.start_time:%H:%M}–{w.end_time:%H:%M}).")
            for d in days:
                doc.schedules.append(DoctorSchedule(weekday=d, start_time=start, end_time=end))
            db.session.commit()
            flash("Working hours added.", "success")
        except ValidationError as e:
            db.session.rollback()
            flash_error(e)
        return redirect(url_for("admin.doctor_schedule", did=did))
    return render_template("admin/schedule.html", doc=doc, weekdays=WEEKDAYS)


@bp.route("/admin/schedule/<int:sid>/delete", methods=["POST"])
@roles_required("admin")
def schedule_delete(sid):
    w = db.get_or_404(DoctorSchedule, sid)
    did = w.doctor_id
    # Refuse if it would strand upcoming appointments that fall inside this window.
    for a in Appointment.query.filter(Appointment.doctor_id == did, Appointment.status == "scheduled",
                                      Appointment.start_at > datetime.now()):
        if a.start_at.weekday() == w.weekday and w.start_time <= a.start_at.time() < w.end_time:
            flash(f"Cannot remove: appointment on {a.start_at:%d %b %Y %H:%M} falls in this window.", "danger")
            return redirect(url_for("admin.doctor_schedule", did=did))
    db.session.delete(w)
    db.session.commit()
    flash("Window removed.", "info")
    return redirect(url_for("admin.doctor_schedule", did=did))


# ------------------------------------------------------------------ reports
@bp.route("/admin/reports")
@roles_required("admin")
def reports():
    days = 30
    since = date.today() - timedelta(days=days - 1)
    since_dt = datetime.combine(since, datetime.min.time())
    rows = db.session.query(func.date(Payment.paid_at), func.sum(Payment.amount)) \
        .filter(Payment.paid_at >= since_dt).group_by(func.date(Payment.paid_at)).all()
    by_day = {str(d): float(v) for d, v in rows}
    series = [((since + timedelta(days=i)).isoformat(), by_day.get((since + timedelta(days=i)).isoformat(), 0.0))
              for i in range(days)]
    top = max((v for _, v in series), default=0) or 1

    by_doctor = db.session.query(User.full_name, func.sum(Payment.amount)) \
        .join(Doctor, Doctor.user_id == User.id).join(Appointment, Appointment.doctor_id == Doctor.id) \
        .join(Invoice, Invoice.appointment_id == Appointment.id).join(Payment, Payment.invoice_id == Invoice.id) \
        .group_by(User.full_name).order_by(func.sum(Payment.amount).desc()).all()
    by_status = db.session.query(Appointment.status, func.count()).group_by(Appointment.status).all()
    by_dept = db.session.query(Department.name, func.count(Appointment.id)) \
        .join(Doctor, Doctor.department_id == Department.id).join(Appointment, Appointment.doctor_id == Doctor.id) \
        .group_by(Department.name).order_by(func.count(Appointment.id).desc()).all()
    outstanding = sum((i.balance for i in Invoice.query.filter(Invoice.status.in_(("unpaid", "partial")))), 0)
    return render_template("admin/reports.html", series=series, top=top, by_doctor=by_doctor,
                           by_status=by_status, by_dept=by_dept, outstanding=outstanding,
                           total=sum(v for _, v in series), days=days)
