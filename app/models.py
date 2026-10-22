"""Normalised 12-table schema (3NF).

roles, users, departments, doctors, patients, doctor_schedules, appointments,
medical_records, prescriptions, invoices, invoice_items, payments
"""
from datetime import datetime
from decimal import Decimal

from werkzeug.security import check_password_hash, generate_password_hash

from . import db

ROLE_ADMIN, ROLE_DOCTOR, ROLE_RECEPTION = "admin", "doctor", "receptionist"
ACTIVE_APPT = ("scheduled", "completed")  # statuses that occupy a slot


class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)
    description = db.Column(db.String(200))


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    last_login = db.Column(db.DateTime)

    role = db.relationship("Role")
    doctor = db.relationship("Doctor", back_populates="user", uselist=False)

    @property
    def role_name(self):
        return self.role.name

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Department(db.Model):
    __tablename__ = "departments"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))
    doctors = db.relationship("Doctor", back_populates="department")


class Doctor(db.Model):
    __tablename__ = "doctors"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False, index=True)
    specialization = db.Column(db.String(100), nullable=False)
    license_no = db.Column(db.String(40), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    consultation_fee = db.Column(db.Numeric(10, 2), nullable=False, default=0)

    user = db.relationship("User", back_populates="doctor")
    department = db.relationship("Department", back_populates="doctors")
    schedules = db.relationship("DoctorSchedule", back_populates="doctor",
                                order_by="DoctorSchedule.weekday, DoctorSchedule.start_time",
                                cascade="all, delete-orphan")

    @property
    def name(self):
        return self.user.full_name


class Patient(db.Model):
    __tablename__ = "patients"
    __table_args__ = (db.Index("ix_patients_name", "last_name", "first_name"),)
    id = db.Column(db.Integer, primary_key=True)
    patient_code = db.Column(db.String(20), unique=True, nullable=False)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    dob = db.Column(db.Date, nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    phone = db.Column(db.String(20), nullable=False, index=True)
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    blood_group = db.Column(db.String(5))
    emergency_contact = db.Column(db.String(120))
    allergies = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self):
        from datetime import date
        t = date.today()
        return t.year - self.dob.year - ((t.month, t.day) < (self.dob.month, self.dob.day))


class DoctorSchedule(db.Model):
    """Recurring weekly availability window (weekday 0 = Monday)."""
    __tablename__ = "doctor_schedules"
    __table_args__ = (
        db.UniqueConstraint("doctor_id", "weekday", "start_time", name="uq_schedule_window"),
        db.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_schedule_weekday"),
        db.CheckConstraint("end_time > start_time", name="ck_schedule_times"),
    )
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    weekday = db.Column(db.SmallInteger, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    doctor = db.relationship("Doctor", back_populates="schedules")


class Appointment(db.Model):
    __tablename__ = "appointments"
    __table_args__ = (
        db.Index("ix_appt_doctor_start", "doctor_id", "start_at"),
        db.Index("ix_appt_patient_start", "patient_id", "start_at"),
        db.CheckConstraint("end_at > start_at", name="ck_appt_times"),
        db.CheckConstraint("status IN ('scheduled','completed','cancelled','no_show')", name="ck_appt_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False)
    start_at = db.Column(db.DateTime, nullable=False)
    end_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(12), nullable=False, default="scheduled")
    reason = db.Column(db.String(255))
    # DB-level backstop against double booking: "<doctor>:<start>" while the
    # slot is occupied, NULL once cancelled / no-show (UNIQUE ignores NULLs).
    slot_key = db.Column(db.String(40), unique=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    patient = db.relationship("Patient")
    doctor = db.relationship("Doctor")
    record = db.relationship("MedicalRecord", back_populates="appointment", uselist=False)
    invoices = db.relationship("Invoice", back_populates="appointment")

    def sync_slot_key(self):
        self.slot_key = (f"{self.doctor_id}:{self.start_at:%Y%m%d%H%M}"
                         if self.status in ACTIVE_APPT else None)


class MedicalRecord(db.Model):
    __tablename__ = "medical_records"
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id"), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False)
    vitals = db.Column(db.String(255))
    diagnosis = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text)
    follow_up_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    appointment = db.relationship("Appointment", back_populates="record")
    patient = db.relationship("Patient")
    doctor = db.relationship("Doctor")
    prescriptions = db.relationship("Prescription", back_populates="record", cascade="all, delete-orphan")


class Prescription(db.Model):
    __tablename__ = "prescriptions"
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey("medical_records.id", ondelete="CASCADE"), nullable=False, index=True)
    medicine = db.Column(db.String(120), nullable=False)
    dosage = db.Column(db.String(60), nullable=False)
    frequency = db.Column(db.String(60), nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    record = db.relationship("MedicalRecord", back_populates="prescriptions")


class Invoice(db.Model):
    __tablename__ = "invoices"
    __table_args__ = (
        db.CheckConstraint("status IN ('unpaid','partial','paid','void')", name="ck_invoice_status"),
        db.CheckConstraint("discount >= 0", name="ck_invoice_discount"),
    )
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(24), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id"), index=True)
    status = db.Column(db.String(10), nullable=False, default="unpaid")
    discount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    notes = db.Column(db.String(255))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    patient = db.relationship("Patient")
    appointment = db.relationship("Appointment", back_populates="invoices")
    items = db.relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    payments = db.relationship("Payment", back_populates="invoice", order_by="Payment.paid_at")

    @property
    def subtotal(self):
        return sum((i.line_total for i in self.items), Decimal("0.00"))

    @property
    def total(self):
        return self.subtotal - Decimal(self.discount or 0)

    @property
    def paid(self):
        return sum((p.amount for p in self.payments), Decimal("0.00"))

    @property
    def balance(self):
        return self.total - self.paid

    def refresh_status(self):
        if self.status == "void":
            return
        if self.items and self.balance <= 0:
            self.status = "paid"
        elif self.paid > 0:
            self.status = "partial"
        else:
            self.status = "unpaid"


class InvoiceItem(db.Model):
    __tablename__ = "invoice_items"
    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_item_qty"),
        db.CheckConstraint("unit_price >= 0", name="ck_item_price"),
    )
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    invoice = db.relationship("Invoice", back_populates="items")

    @property
    def line_total(self):
        return Decimal(self.quantity) * Decimal(self.unit_price)


class Payment(db.Model):
    __tablename__ = "payments"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_payment_amount"),
        db.CheckConstraint("method IN ('cash','card','upi','insurance')", name="ck_payment_method"),
    )
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    method = db.Column(db.String(12), nullable=False)
    reference = db.Column(db.String(60))
    paid_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    received_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    invoice = db.relationship("Invoice", back_populates="payments")
