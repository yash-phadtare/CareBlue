from careblue.routes.blueprints import AccessBlueprint as Blueprint
from database import get_db_connection
from careblue.services import page_query
from flask import render_template, session

bp = Blueprint("history", __name__, roles=("admin",), permission="beds")

@bp.get("/admin/admissions")
def admissions():
    conn = get_db_connection()
    try:
        rows = page_query(conn, """SELECT a.id, p.name AS patient_name, w.name AS ward_name, b.bed_number,
            a.admitted_at, a.discharged_at FROM admissions a JOIN patients p ON p.id=a.patient_id
            JOIN beds b ON b.id=a.bed_id JOIN wards w ON w.id=b.ward_id
            WHERE a.hospital_id=? ORDER BY a.id DESC""", (session["hospital_id"],))
    finally:
        conn.close()
    return render_template("admin/admissions.html", admissions=rows)
