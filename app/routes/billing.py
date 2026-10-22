from decimal import Decimal

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from ..extensions import db
from ..models import (INV_PARTIAL, INV_PAID, INV_UNPAID, INV_VOID, PAYMENT_METHODS, Invoice,
                      InvoiceItem, Patient, Payment, Service, money)
from ..security import ADMIN, RECEPTIONIST, roles_required
from ..services import audit, create_invoice, refresh_invoice_status
from ..utils import to_decimal

bp = Blueprint("billing", __name__, url_prefix="/billing")
STATUSES = [INV_UNPAID, INV_PARTIAL, INV_PAID, INV_VOID]


def _back(inv):
    return redirect(url_for("billing.detail", iid=inv.id))


def _check_not_below_paid(inv):
    """After a change that lowers the total, make sure we haven't under-billed vs payments."""
    if inv.total < inv.paid:
        db.session.rollback()
        flash("Change rejected: the invoice total would drop below the amount already paid.", "danger")
        return False
    return True


@bp.route("/")
@roles_required(ADMIN, RECEPTIONIST)
def index():
    status = request.args.get("status", "")
    term = request.args.get("q", "").strip()
    q = Invoice.query.join(Invoice.patient)
    if status in STATUSES:
        q = q.filter(Invoice.status == status)
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Invoice.invoice_no.like(like), Patient.first_name.like(like),
                         Patient.last_name.like(like), Patient.patient_code.like(like)))
    pager = q.order_by(Invoice.id.desc()).paginate(page=request.args.get("page", 1, type=int),
                                                   per_page=20, error_out=False)
    return render_template("billing/list.html", pager=pager, status=status, q=term, statuses=STATUSES)


@bp.route("/new", methods=["GET", "POST"])
@roles_required(ADMIN, RECEPTIONIST)
def new():
    if request.method == "POST":
        p = db.session.get(Patient, request.form.get("patient_id", type=int) or 0)
        if not p:
            flash("Select a patient.", "danger")
        else:
            inv = create_invoice(p.id, g.user.id)
            db.session.commit()
            flash(f"Invoice {inv.invoice_no} created. Add items below.", "success")
            return _back(inv)
    patients = Patient.query.order_by(Patient.first_name).limit(1000).all()
    return render_template("billing/new.html", patients=patients,
                           selected=request.args.get("patient_id", ""))


@bp.route("/<int:iid>")
@roles_required(ADMIN, RECEPTIONIST)
def detail(iid):
    inv = db.get_or_404(Invoice, iid)
    services = Service.query.filter_by(active=True).order_by(Service.name).all()
    return render_template("billing/invoice.html", inv=inv, services=services, methods=PAYMENT_METHODS)


@bp.route("/<int:iid>/items", methods=["POST"])
@roles_required(ADMIN, RECEPTIONIST)
def add_item(iid):
    inv = db.get_or_404(Invoice, iid)
    if inv.status == INV_VOID:
        flash("Void invoices cannot be edited.", "danger")
        return _back(inv)
    qty = request.form.get("quantity", type=int) or 0
    service = db.session.get(Service, request.form.get("service_id", type=int) or 0)
    if service:
        desc, price = service.name, money(service.price)
    else:
        desc = request.form.get("description", "").strip()[:150]
        price = to_decimal(request.form.get("unit_price", ""))
    if qty < 1 or qty > 1000 or not desc or price is None or price < 0:
        flash("Provide a service or description, a price of 0 or more, and a quantity of 1-1000.", "danger")
        return _back(inv)
    inv.items.append(InvoiceItem(service_id=service.id if service else None, description=desc,
                                 quantity=qty, unit_price=money(price)))
    db.session.flush()
    refresh_invoice_status(inv)
    audit("invoice.add_item", f"{inv.invoice_no}: {desc} x{qty}")
    db.session.commit()
    flash("Item added.", "success")
    return _back(inv)


@bp.route("/<int:iid>/items/<int:item_id>/delete", methods=["POST"])
@roles_required(ADMIN, RECEPTIONIST)
def delete_item(iid, item_id):
    inv = db.get_or_404(Invoice, iid)
    item = next((i for i in inv.items if i.id == item_id), None)
    if inv.status == INV_VOID or not item:
        flash("Item not found or invoice is void.", "danger")
        return _back(inv)
    inv.items.remove(item)
    db.session.flush()
    if _check_not_below_paid(inv):
        refresh_invoice_status(inv)
        audit("invoice.remove_item", f"{inv.invoice_no}: {item.description}")
        db.session.commit()
        flash("Item removed.", "info")
    return _back(inv)


@bp.route("/<int:iid>/adjust", methods=["POST"])
@roles_required(ADMIN, RECEPTIONIST)
def adjust(iid):
    inv = db.get_or_404(Invoice, iid)
    discount, tax = to_decimal(request.form.get("discount", "0")), to_decimal(request.form.get("tax_percent", "0"))
    if inv.status == INV_VOID:
        flash("Void invoices cannot be edited.", "danger")
    elif discount is None or tax is None or discount < 0 or not (0 <= tax <= 100):
        flash("Discount must be 0 or more and tax between 0 and 100%.", "danger")
    elif discount > inv.subtotal:
        flash("Discount cannot exceed the invoice subtotal.", "danger")
    else:
        inv.discount, inv.tax_percent = money(discount), tax.quantize(Decimal("0.01"))
        db.session.flush()
        if _check_not_below_paid(inv):
            refresh_invoice_status(inv)
            audit("invoice.adjust", f"{inv.invoice_no}: discount={discount} tax={tax}")
            db.session.commit()
            flash("Discount / tax updated.", "success")
    return _back(inv)


@bp.route("/<int:iid>/pay", methods=["POST"])
@roles_required(ADMIN, RECEPTIONIST)
def pay(iid):
    inv = db.get_or_404(Invoice, iid)
    amount = to_decimal(request.form.get("amount", ""))
    method = request.form.get("method", "")
    if inv.status == INV_VOID:
        flash("Cannot take payment on a void invoice.", "danger")
    elif method not in PAYMENT_METHODS:
        flash("Choose a payment method.", "danger")
    elif amount is None or amount <= 0:
        flash("Enter a payment amount greater than zero.", "danger")
    elif money(amount) > inv.balance:
        flash(f"Payment exceeds the outstanding balance ({inv.balance}).", "danger")
    else:
        db.session.add(Payment(invoice_id=inv.id, amount=money(amount), method=method,
                               reference=request.form.get("reference", "").strip()[:60] or None,
                               received_by=g.user.id))
        db.session.flush()
        db.session.refresh(inv)
        refresh_invoice_status(inv)
        audit("payment.receive", f"{inv.invoice_no}: {money(amount)} via {method}")
        db.session.commit()
        flash("Payment recorded." + (" Invoice fully paid." if inv.status == INV_PAID else ""), "success")
    return _back(inv)


@bp.route("/<int:iid>/void", methods=["POST"])
@roles_required(ADMIN)
def void(iid):
    inv = db.get_or_404(Invoice, iid)
    if inv.payments:
        flash("An invoice with payments cannot be voided.", "danger")
    else:
        inv.status = INV_VOID
        audit("invoice.void", inv.invoice_no)
        db.session.commit()
        flash("Invoice voided.", "info")
    return _back(inv)
