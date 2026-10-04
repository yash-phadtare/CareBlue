from careblue.routes.blueprints import AccessBlueprint as Blueprint
from flask import request, session, jsonify, abort
from database import get_db_connection
from careblue.services import time_display

bp = Blueprint("lookups", __name__, roles=("admin", "doctor"))

@bp.get("/admin/lookup/<kind>")
def lookup(kind):
    if session["user_type"] == "doctor" and kind != "medicines":
        abort(403)
    if session['user_type'] == 'admin':
        from careblue.tenancy import can
        permission = {'patients':'registry','doctors':'appointments','visits':'billing','medicines':'pharmacy'}.get(kind)
        if kind == 'patients' and can('billing'):
            pass
        elif not permission or not can(permission):
            abort(403)
    query = request.args.get("q", "").strip().lower()[:120]
    conn = get_db_connection()
    try:
        if kind in {"patients", "doctors"}:
            suffix = " AND active = 1" if kind == "doctors" else ""
            extra = "LOWER(COALESCE(contact,'')) LIKE ?" if kind=='patients' else "LOWER(specialization) LIKE ?"
            detail = "COALESCE(contact,'')" if kind=='patients' else "specialization"
            rows = conn.execute(f"SELECT id, name AS label, {detail} AS detail FROM {kind} WHERE hospital_id = ? AND (LOWER(name) LIKE ? OR {extra})" + suffix + " ORDER BY name, id LIMIT 50",
                                (session["hospital_id"], "%" + query + "%", "%" + query + "%")).fetchall()
        elif kind == "visits":
            patient = request.args.get("patient_id")
            if patient and not patient.isdigit():
                abort(400)
            sql = """SELECT a.id, a.patient_id, p.name, a.date, a.time_slot, d.consultation_fee_cents FROM appointments a
                     JOIN patients p ON p.id = a.patient_id
                     JOIN doctors d ON d.id = a.doctor_id
                     LEFT JOIN bills b ON b.appointment_id = a.id
                     WHERE a.hospital_id = ? AND a.status != 'Cancelled' AND b.id IS NULL AND LOWER(p.name) LIKE ?"""
            params = [session["hospital_id"], "%" + query + "%"]
            if patient:
                sql += " AND a.patient_id = ?"
                params.append(int(patient))
            rows = conn.execute(sql + " ORDER BY a.date DESC, a.id DESC LIMIT 50", params).fetchall()
            rows = [{"id": r["id"], "patient_id": r["patient_id"], "patient_name": r['name'], "fee_cents":r['consultation_fee_cents'], "label": f"#{r['id']} · {r['name']} · {r['date']} {time_display(r['time_slot'])}"} for r in rows]
        elif kind == "medicines":
            rows = conn.execute("SELECT id, name, strength, stock_qty FROM medicines WHERE hospital_id = ? AND archived=0 AND LOWER(name) LIKE ? ORDER BY name LIMIT 50",
                                (session["hospital_id"], "%" + query + "%")).fetchall()
        else:
            abort(404)
        return jsonify(rows)
    finally:
        conn.close()

