from datetime import date

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (AuditLog, Appointment, Department, Doctor, DoctorSchedule, Invoice, Role,
                      Service, User, money)
from ..security import ADMIN, roles_required
from ..services import audit
from ..utils import parse_time, to_decimal, valid_email, valid_phone

bp = Blueprint("admin", __name__, url_prefix="/admin")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------- users
@bp.route("/users")
@roles_required(ADMIN)
def users():
    return render_template("admin/users.html", users=User.query.order_by(User.id).all())


@bp.route("/users/new", methods=["GET", "POST"])
@roles_required(ADMIN)
def user_new():
    roles = Role.query.order_by(Role.id).all()
    depts = Department.query.order_by(Department.name).all()
    if request.method == "POST":
        f = request.form
        username = f.get("username", "").strip().lower()
        role = db.session.get(Role, f.get("role_id", type=int) or 0)
        email = f.get("email", "").strip() or None
        errors = []
        if len(username) < 3 or not username.replace(".", "").replace("_", "").isalnum():
            errors.append("Username must be 3+ characters (letters, digits, . or _).")
        if not f.get("full_name", "").strip():
            errors.append("Full name is required.")
        if len(f.get("password", "")) < 8:
            errors.append("Password must be at least 8 characters.")
        if not role:
            errors.append("Select a role.")
        if email and not valid_email(email):
            errors.append("Invalid email address.")
        if User.query.filter(func.lower(User.username) == username).first():
            errors.append("That username is already taken.")
        if email and User.query.filter(func.lower(User.email) == email.lower()).first():
            errors.append("That email is already in use.")
        dept = fee = None
        if role and role.name == "Doctor":
            dept = db.session.get(Department, f.get("department_id", type=int) or 0)
            fee = to_decimal(f.get("consultation_fee", ""))
            if not dept:
                errors.append("Doctors need a department.")
            if fee is None or fee < 0:
                errors.append("Enter a valid consultation fee.")
            if f.get("phone") and not valid_phone(f["phone"].strip()):
                errors.append("Invalid doctor phone number.")
        if not errors:
            u = User(username=username, full_name=f["full_name"].strip(), email=email, role=role)
            u.set_password(f["password"])
            db.session.add(u)
            db.session.flush()
            if role.name == "Doctor":
                db.session.add(Doctor(user_id=u.id, department_id=dept.id, consultation_fee=money(fee),
                                      specialization=f.get("specialization", "").strip() or None,
                                      qualification=f.get("qualification", "").strip() or None,
                                      phone=f.get("phone", "").strip() or None))
            audit("user.create", f"{username} ({role.name})")
            db.session.commit()
            flash(f"User {username} created.", "success")
            return redirect(url_for("admin.users"))
        for e in errors:
            flash(e, "danger")
    return render_template("admin/user_form.html", roles=roles, depts=depts, f=request.form)


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@roles_required(ADMIN)
def user_toggle(uid):
    u = db.get_or_404(User, uid)
    if u.id == g.user.id:
        flash("You cannot deactivate your own account.", "danger")
    else:
        u.active = not u.active
        audit("user.toggle", f"{u.username} active={u.active}")
        db.session.commit()
        flash(f"{u.username} is now {'active' if u.active else 'deactivated'}.", "info")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:uid>/reset-password", methods=["POST"])
@roles_required(ADMIN)
def user_reset(uid):
    u = db.get_or_404(User, uid)
    pw = request.form.get("password", "")
    if len(pw) < 8:
        flash("Password must be at least 8 characters.", "danger")
    else:
        u.set_password(pw)
        audit("user.reset_password", u.username)
        db.session.commit()
        flash(f"Password reset for {u.username}.", "success")
    return redirect(url_for("admin.users"))


# ----------------------------------------------------------------- departments
@bp.route("/departments", methods=["GET", "POST"])
@roles_required(ADMIN)
def departments():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Department name is required.", "danger")
        elif Department.query.filter(func.lower(Department.name) == name.lower()).first():
            flash("That department already exists.", "danger")
        else:
            db.session.add(Department(name=name[:80], description=request.form.get("description", "").strip()[:255] or None))
            audit("department.create", name)
            db.session.commit()
            flash("Department added.", "success")
        return redirect(url_for("admin.departments"))
    return render_template("admin/departments.html", depts=Department.query.order_by(Department.name).all())


@bp.route("/departments/<int:did>/delete", methods=["POST"])
@roles_required(ADMIN)
def department_delete(did):
    d = db.get_or_404(Department, did)
    if d.doctors:
        flash("Cannot delete a department that still has doctors.", "danger")
    else:
        audit("department.delete", d.name)
        db.session.delete(d)
        db.session.commit()
        flash("Department deleted.", "info")
    return redirect(url_for("admin.departments"))


# --------------------------------------------------------------------- doctors
@bp.route("/doctors")
@roles_required(ADMIN)
def doctors():
    return render_template("admin/doctors.html", doctors=Doctor.query.all())


@bp.route("/doctors/<int:did>/schedule", methods=["GET", "POST"])
@roles_required(ADMIN)
def schedule(did):
    doc = db.get_or_404(Doctor, did)
    if request.method == "POST":
        wd = request.form.get("weekday", type=int)
        st, en = parse_time(request.form.get("start_time")), parse_time(request.form.get("end_time"))
        if wd is None or not 0 <= wd <= 6 or not st or not en:
            flash("Choose a weekday and valid start/end times.", "danger")
        elif en <= st:
            flash("End time must be after start time.", "danger")
        elif any(s.weekday == wd and st < s.end_time and en > s.start_time for s in doc.schedules):
            flash("That window overlaps an existing one on the same day.", "danger")
        else:
            db.session.add(DoctorSchedule(doctor_id=doc.id, weekday=wd, start_time=st, end_time=en))
            audit("schedule.add", f"{doc.name} {DAYS[wd]} {st:%H:%M}-{en:%H:%M}")
            db.session.commit()
            flash("Working window added.", "success")
        return redirect(url_for("admin.schedule", did=did))
    return render_template("admin/schedule.html", doc=doc, days=DAYS)


@bp.route("/doctors/<int:did>/schedule/<int:sid>/delete", methods=["POST"])
@roles_required(ADMIN)
def schedule_delete(did, sid):
    s = db.get_or_404(DoctorSchedule, sid)
    if s.doctor_id == did:
        db.session.delete(s)
        audit("schedule.delete", f"doctor={did} window={sid}")
        db.session.commit()
        flash("Working window removed. Existing appointments are unaffected.", "info")
    return redirect(url_for("admin.schedule", did=did))


# -------------------------------------------------------------------- services
@bp.route("/services", methods=["GET", "POST"])
@roles_required(ADMIN)
def services():
    if request.method == "POST":
        name, price = request.form.get("name", "").strip(), to_decimal(request.form.get("price", ""))
        if not name or price is None or price < 0:
            flash("Provide a service name and a price of 0 or more.", "danger")
        elif Service.query.filter(func.lower(Service.name) == name.lower()).first():
            flash("That service already exists.", "danger")
        else:
            db.session.add(Service(name=name[:100], price=money(price)))
            audit("service.create", name)
            db.session.commit()
            flash("Service added.", "success")
        return redirect(url_for("admin.services"))
    return render_template("admin/services.html", services=Service.query.order_by(Service.name).all())


@bp.route("/services/<int:sid>/toggle", methods=["POST"])
@roles_required(ADMIN)
def service_toggle(sid):
    s = db.get_or_404(Service, sid)
    s.active = not s.active
    db.session.commit()
    return redirect(url_for("admin.services"))


# --------------------------------------------------------------- reports / logs
@bp.route("/reports")
@roles_required(ADMIN)
def reports():
    by_status = dict(db.session.query(Appointment.status, func.count()).group_by(Appointment.status).all())
    by_doctor = (db.session.query(User.full_name, func.count(Appointment.id))
                 .select_from(Appointment).join(Doctor, Appointment.doctor_id == Doctor.id)
                 .join(User, Doctor.user_id == User.id).filter(Appointment.status == "Completed")
                 .group_by(User.full_name).order_by(func.count(Appointment.id).desc()).all())
    invoices = Invoice.query.filter(Invoice.status != "Void").all()
    billed = sum((i.total for i in invoices), money(0))
    collected = sum((i.paid for i in invoices), money(0))
    return render_template("admin/reports.html", by_status=by_status, by_doctor=by_doctor,
                           billed=billed, collected=collected, outstanding=billed - collected)


@bp.route("/audit")
@roles_required(ADMIN)
def audit_log():
    pager = AuditLog.query.order_by(AuditLog.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=30, error_out=False)
    return render_template("admin/audit.html", pager=pager)
