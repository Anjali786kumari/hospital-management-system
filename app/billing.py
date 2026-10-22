from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from . import db
from .auth import roles_required
from .models import Appointment, Invoice, InvoiceItem, Patient, Payment
from .utils import ValidationError, flash_error, optional, parse_int, parse_money, required

bp = Blueprint("billing", __name__, url_prefix="/billing")
STAFF = ("admin", "receptionist")
METHODS = ("cash", "card", "upi", "insurance")


def _open_invoice(iid):
    inv = db.get_or_404(Invoice, iid)
    if inv.status == "void":
        flash("This invoice is void and can no longer be changed.", "danger")
        return None
    return inv


@bp.route("/")
@roles_required(*STAFF)
def index():
    status, q = request.args.get("status", ""), request.args.get("q", "").strip()
    query = Invoice.query.join(Patient)
    if status:
        query = query.filter(Invoice.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Invoice.invoice_no.like(like), Patient.first_name.like(like),
                                 Patient.last_name.like(like), Patient.patient_code.like(like)))
    page = max(request.args.get("page", 1, type=int), 1)
    pg = query.order_by(Invoice.id.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("billing/list.html", pg=pg, status=status, q=q)


@bp.route("/new", methods=["GET", "POST"])
@roles_required(*STAFF)
def new():
    src = request.form if request.method == "POST" else request.args
    appt_id = src.get("appointment_id", type=int)
    patient_id = src.get("patient_id", type=int)
    appt = db.session.get(Appointment, appt_id) if appt_id else None
    if appt:
        patient_id = appt.patient_id
    if request.method == "POST":
        patient = db.session.get(Patient, patient_id) if patient_id else None
        if not patient:
            flash("Select a patient.", "danger")
        elif appt and appt.status in ("cancelled", "no_show"):
            flash("Cannot bill a cancelled / no-show appointment.", "danger")
        elif appt and any(i.status != "void" for i in appt.invoices):
            flash("This appointment already has an invoice.", "danger")
        else:
            inv = Invoice(invoice_no=f"TMP-{id(request)}", patient_id=patient.id, created_by=g.user.id,
                          appointment_id=appt.id if appt else None, notes=optional(request.form, "notes"))
            db.session.add(inv)
            db.session.flush()
            inv.invoice_no = f"INV-{inv.created_at:%Y}-{inv.id:06d}"
            if appt:
                inv.items.append(InvoiceItem(description=f"Consultation – Dr. {appt.doctor.name}",
                                             quantity=1, unit_price=appt.doctor.consultation_fee))
            db.session.commit()
            flash("Invoice created.", "success")
            return redirect(url_for("billing.view", iid=inv.id))
    return render_template("billing/new.html", appt=appt, patient_id=patient_id,
                           patients=Patient.query.filter_by(is_active=True).order_by(Patient.first_name).limit(500).all())


@bp.route("/<int:iid>")
@roles_required(*STAFF)
def view(iid):
    return render_template("billing/view.html", inv=db.get_or_404(Invoice, iid), methods=METHODS)


@bp.route("/<int:iid>/items", methods=["POST"])
@roles_required(*STAFF)
def add_item(iid):
    inv = _open_invoice(iid)
    if inv:
        try:
            item = InvoiceItem(description=required(request.form, "description", "Description", 200),
                               quantity=parse_int(request.form.get("quantity"), "Quantity", 1, 1000),
                               unit_price=parse_money(request.form.get("unit_price"), "Unit price", allow_zero=True))
            inv.items.append(item)
            inv.refresh_status()
            db.session.commit()
            flash("Item added.", "success")
        except ValidationError as e:
            flash_error(e)
    return redirect(url_for("billing.view", iid=iid))


@bp.route("/<int:iid>/items/<int:item_id>/delete", methods=["POST"])
@roles_required(*STAFF)
def delete_item(iid, item_id):
    inv = _open_invoice(iid)
    if inv:
        item = next((i for i in inv.items if i.id == item_id), None) or abort(404)
        if inv.total - item.line_total < inv.paid:
            flash("Cannot remove this item: the invoice would be left over-paid.", "danger")
        else:
            inv.items.remove(item)
            db.session.flush()
            inv.refresh_status()
            db.session.commit()
            flash("Item removed.", "info")
    return redirect(url_for("billing.view", iid=iid))


@bp.route("/<int:iid>/discount", methods=["POST"])
@roles_required(*STAFF)
def discount(iid):
    inv = _open_invoice(iid)
    if inv:
        try:
            amt = parse_money(request.form.get("discount"), "Discount", allow_zero=True)
            if amt > inv.subtotal:
                raise ValidationError("Discount cannot exceed the subtotal.")
            if inv.subtotal - amt < inv.paid:
                raise ValidationError("Discount would leave the invoice over-paid.")
            inv.discount = amt
            inv.refresh_status()
            db.session.commit()
            flash("Discount updated.", "success")
        except ValidationError as e:
            flash_error(e)
    return redirect(url_for("billing.view", iid=iid))


@bp.route("/<int:iid>/pay", methods=["POST"])
@roles_required(*STAFF)
def pay(iid):
    inv = _open_invoice(iid)
    if inv:
        try:
            amt = parse_money(request.form.get("amount"), "Payment amount")
            method = request.form.get("method")
            if method not in METHODS:
                raise ValidationError("Select a payment method.")
            if amt > inv.balance:
                raise ValidationError(f"Payment exceeds the outstanding balance of {inv.balance:,.2f}.")
            inv.payments.append(Payment(amount=amt, method=method, reference=optional(request.form, "reference", 60),
                                        received_by=g.user.id))
            db.session.flush()
            inv.refresh_status()
            db.session.commit()
            flash("Payment recorded.", "success")
        except ValidationError as e:
            flash_error(e)
    return redirect(url_for("billing.view", iid=iid))


@bp.route("/<int:iid>/void", methods=["POST"])
@roles_required("admin")
def void(iid):
    inv = _open_invoice(iid)
    if inv:
        if inv.paid > 0:
            flash("Invoices with payments cannot be voided.", "danger")
        else:
            inv.status = "void"
            db.session.commit()
            flash("Invoice voided.", "info")
    return redirect(url_for("billing.view", iid=iid))
