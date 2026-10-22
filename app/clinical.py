from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db
from .auth import roles_required
from .models import Appointment, MedicalRecord, Prescription
from .utils import ValidationError, flash_error, optional, parse_date, parse_int, required

bp = Blueprint("clinical", __name__)


def _check_access(appt):
    if g.user.role_name == "doctor" and appt.doctor_id != g.user.doctor.id:
        abort(403, "This consultation belongs to another doctor.")


@bp.route("/appointments/<int:aid>/record", methods=["GET", "POST"])
@roles_required("admin", "doctor")
def write_record(aid):
    appt = db.get_or_404(Appointment, aid)
    _check_access(appt)
    if appt.status == "cancelled" or appt.status == "no_show":
        flash("No consultation record for a cancelled / no-show appointment.", "danger")
        return redirect(url_for("appointments.index"))
    if appt.record:
        return redirect(url_for("clinical.view_record", rid=appt.record.id))
    if appt.start_at > datetime.now():
        flash("This appointment has not started yet.", "danger")
        return redirect(url_for("appointments.index"))

    if request.method == "POST":
        f = request.form
        try:
            rec = MedicalRecord(appointment_id=appt.id, patient_id=appt.patient_id, doctor_id=appt.doctor_id,
                                diagnosis=required(f, "diagnosis", "Diagnosis", 5000),
                                vitals=optional(f, "vitals"), notes=optional(f, "notes", 5000))
            if f.get("follow_up_date"):
                rec.follow_up_date = parse_date(f["follow_up_date"], "Follow-up date")
                if rec.follow_up_date <= datetime.now().date():
                    raise ValidationError("Follow-up date must be in the future.")
            meds, doses, freqs, days = (f.getlist(k) for k in ("medicine", "dosage", "frequency", "duration_days"))
            for m, d, fr, du in zip(meds, doses, freqs, days):
                if not any(x.strip() for x in (m, d, fr, du)):
                    continue  # blank row
                if not (m.strip() and d.strip() and fr.strip()):
                    raise ValidationError("Each medicine needs a name, dosage and frequency.")
                rec.prescriptions.append(Prescription(
                    medicine=m.strip()[:120], dosage=d.strip()[:60], frequency=fr.strip()[:60],
                    duration_days=parse_int(du, "Duration (days)", 1, 365)))
        except ValidationError as e:
            flash_error(e)
            return render_template("clinical/record_form.html", appt=appt, f=f), 400
        db.session.add(rec)
        appt.status = "completed"
        appt.sync_slot_key()
        db.session.commit()
        flash("Consultation saved and appointment completed.", "success")
        return redirect(url_for("clinical.view_record", rid=rec.id))
    return render_template("clinical/record_form.html", appt=appt, f={})


@bp.route("/records/<int:rid>")
@roles_required("admin", "doctor")
def view_record(rid):
    rec = db.get_or_404(MedicalRecord, rid)
    if g.user.role_name == "doctor":
        # a doctor may read records they wrote, or those of a patient they have treated
        treated = MedicalRecord.query.filter_by(patient_id=rec.patient_id, doctor_id=g.user.doctor.id).first()
        if not treated:
            abort(403, "You have not treated this patient.")
    return render_template("clinical/record_view.html", rec=rec)
