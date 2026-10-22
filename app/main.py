from datetime import date, datetime, timedelta

from flask import Blueprint, g, render_template
from sqlalchemy import func

from . import db
from .auth import login_required
from .models import Appointment, Doctor, Invoice, Patient, Payment

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def dashboard():
    today = date.today()
    t0 = datetime.combine(today, datetime.min.time())
    t1 = t0 + timedelta(days=1)
    role = g.user.role_name
    ctx = {"role": role}

    day_q = Appointment.query.filter(Appointment.start_at >= t0, Appointment.start_at < t1)
    if role == "doctor":
        d = g.user.doctor
        mine = day_q.filter_by(doctor_id=d.id)
        ctx["today_appts"] = mine.order_by(Appointment.start_at).all()
        ctx["upcoming"] = Appointment.query.filter(
            Appointment.doctor_id == d.id, Appointment.status == "scheduled",
            Appointment.start_at >= t1).order_by(Appointment.start_at).limit(8).all()
        ctx["done_today"] = mine.filter_by(status="completed").count()
        return render_template("dashboard.html", **ctx)

    ctx["today_appts"] = day_q.order_by(Appointment.start_at).all()
    ctx["stats"] = {
        "Patients": Patient.query.filter_by(is_active=True).count(),
        "Doctors": Doctor.query.count(),
        "Appointments today": len(ctx["today_appts"]),
        "Open invoices": Invoice.query.filter(Invoice.status.in_(("unpaid", "partial"))).count(),
    }
    if role == "admin":
        rev_today = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.paid_at >= t0, Payment.paid_at < t1).scalar()
        rev_month = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.paid_at >= t0.replace(day=1)).scalar()
        ctx["stats"]["Revenue today"] = f"₹{rev_today:,.2f}"
        ctx["stats"]["Revenue this month"] = f"₹{rev_month:,.2f}"
    ctx["recent_invoices"] = Invoice.query.order_by(Invoice.id.desc()).limit(5).all()
    return render_template("dashboard.html", **ctx)
