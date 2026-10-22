from datetime import datetime, timedelta

from flask import Blueprint, g, redirect, render_template, url_for
from sqlalchemy import func

from ..extensions import db
from ..models import (APPT_SCHEDULED, INV_PARTIAL, INV_UNPAID, Appointment, Doctor, Invoice,
                      Patient, Payment, money)
from ..security import ADMIN, DOCTOR, RECEPTIONIST, login_required

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    today = datetime.combine(datetime.now().date(), datetime.min.time())
    tomorrow = today + timedelta(days=1)
    todays = Appointment.query.filter(Appointment.start_at >= today, Appointment.start_at < tomorrow)
    stats = {}
    role = g.user.role_name

    if role == DOCTOR:
        doc = g.user.doctor_profile
        mine = todays.filter(Appointment.doctor_id == doc.id)
        stats["Today's appointments"] = mine.count()
        stats["Completed today"] = mine.filter(Appointment.status == "Completed").count()
        stats["Upcoming (all)"] = Appointment.query.filter(
            Appointment.doctor_id == doc.id, Appointment.status == APPT_SCHEDULED,
            Appointment.start_at >= tomorrow).count()
        stats["My patients"] = db.session.query(Appointment.patient_id).filter(
            Appointment.doctor_id == doc.id).distinct().count()
        queue = mine.order_by(Appointment.start_at).all()
        return render_template("dashboard/index.html", stats=stats, queue=queue, role=role)

    queue = todays.order_by(Appointment.start_at).all()
    stats["Today's appointments"] = len(queue)
    stats["Registered patients"] = Patient.query.count()
    open_inv = Invoice.query.filter(Invoice.status.in_([INV_UNPAID, INV_PARTIAL])).all()
    stats["Open invoices"] = len(open_inv)
    stats["Outstanding balance"] = sum((i.balance for i in open_inv), money(0))
    stats["Collected today"] = money(db.session.query(func.coalesce(func.sum(Payment.amount), 0))
                                     .filter(Payment.paid_at >= today).scalar())
    if role == ADMIN:
        stats["Active doctors"] = Doctor.query.join(Doctor.user).filter_by(active=True).count()
    return render_template("dashboard/index.html", stats=stats, queue=queue, role=role)
