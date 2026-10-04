from careblue.routes.blueprints import AccessBlueprint as Blueprint
from careblue.constants import VALID_STATUSES
from flask import current_app as app, flash, jsonify, redirect, render_template, request, session, url_for
from careblue.services import facility_now, page_query, parse_medicines,visit_period_filter
from database import get_db_connection, IntegrityError
from datetime import timedelta

from database import lock_record
from careblue.services import medicine_form
from careblue.workflows import change_appointment_status, save_prescription_version, StalePrescription
from careblue.security import log_audit
import json
from careblue.presentation import back_url,context_link

bp = Blueprint("doctor", __name__, roles=("doctor",))

# Doctor routes
@bp.route('/doctor/dashboard')
def doctor_dashboard():
    
    doctor_id = session['user_id']
    conn = get_db_connection()
    
    try:
        today = facility_now().strftime('%Y-%m-%d')
        appointments_today = conn.execute('''
            SELECT a.id, p.id as patient_id, p.name as patient_name, p.age, p.gender, a.time_slot, a.status
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            WHERE a.doctor_id = ? AND a.date = ? AND a.hospital_id = ?
            ORDER BY CASE WHEN a.time_slot IS NULL THEN 0 ELSE 1 END, a.time_slot, a.id
        ''', (doctor_id, today, session['hospital_id'])).fetchall()
        
        upcoming_appointments = conn.execute('''
            SELECT a.id, p.id as patient_id, p.name as patient_name, a.date, a.time_slot, a.status
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            WHERE a.doctor_id = ? AND a.date > ? AND a.hospital_id = ? AND a.status='Scheduled'
            ORDER BY a.date, CASE WHEN a.time_slot IS NULL THEN 0 ELSE 1 END, a.time_slot, a.id
            LIMIT 5
        ''', (doctor_id, today, session['hospital_id'])).fetchall()

        week_days = []
        for i in range(7):
            day = facility_now() + timedelta(days=i)
            week_days.append({'date': day.strftime('%Y-%m-%d'), 'dow': day.strftime('%a'), 'label': day.strftime('%d')})
        week_rows = conn.execute('''
            SELECT date, COUNT(*) as c FROM appointments
            WHERE doctor_id = ? AND hospital_id = ? AND date >= ? AND date <= ? AND status != 'Cancelled'
            GROUP BY date
        ''', (doctor_id, session['hospital_id'], today, (facility_now() + timedelta(days=6)).strftime('%Y-%m-%d'))).fetchall()
        week_counts = {r['date']: r['c'] for r in week_rows}
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

    return render_template('doctor/dashboard.html',
                         appointments_today=appointments_today,
                         upcoming_appointments=upcoming_appointments,
                         week_days=week_days,
                         week_counts=week_counts)

@bp.route('/doctor/appointments')
def doctor_appointments():
    
    doctor_id = session['user_id']
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()
    
    conn = get_db_connection()
    try:
        query = '''
            SELECT a.id, p.id as patient_id, p.name as patient_name, p.age, p.gender,
                   a.date, a.time_slot, a.status, a.notes
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            WHERE a.doctor_id = ? AND a.hospital_id = ?
        '''
        
        params = [doctor_id, session['hospital_id']]
        
        if search_query:
            query += ' AND p.name LIKE ?'
            params.append(f'%{search_query}%')
        
        if status_filter:
            query += ' AND a.status = ?'
            params.append(status_filter)
        
        period_sql,period_params=visit_period_filter()
        query+=period_sql
        params.extend(period_params)
        query += ' ORDER BY a.date DESC, a.time_slot DESC'
        
        appointments = page_query(conn, query, params)
        appointments_total = conn.execute('SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND hospital_id = ?',
                                          (doctor_id, session['hospital_id'])).fetchone()[0]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    return render_template('doctor/view_appointments.html',
                         appointments=appointments,
                         appointments_total=appointments_total,
                         search_query=search_query,
                         status_filter=status_filter)

@bp.route('/doctor/update_appointment_status/<int:appointment_id>', methods=['POST'])
def update_appointment_status(appointment_id):
    
    status = request.form.get('status', '').strip()
    
    if status not in VALID_STATUSES:
        flash('Invalid status value', 'danger')
        return redirect(url_for('doctor.doctor_appointments'))
    
    conn = get_db_connection()
    try:
        change_appointment_status(conn, appointment_id, session['user_id'], session['hospital_id'], status)
        conn.commit()
        flash('Appointment status updated successfully!', 'success')
    except ValueError as error:
        conn.rollback()
        flash(str(error), 'danger')
    except Exception as e:
        conn.rollback()
        flash('Error updating status', 'danger')
        app.logger.error(f"Update status error: {str(e)}")
    finally:
        conn.close()
    
    return redirect(back_url('doctor.doctor_appointments'))

@bp.route('/doctor/prescriptions/<int:appointment_id>', methods=['GET', 'POST'])
def prescriptions(appointment_id):
    conn = get_db_connection()
    try:
        lock_record(conn, 'doctors', session['user_id']) if request.method == 'POST' else None
        appointment = conn.execute("""
            SELECT a.*, p.name AS patient_name, p.age, p.gender, p.medical_history, p.allergies, p.clinical_version, p.date_of_birth, h.name AS hospital_name,
                   d.name AS doctor_name, d.specialization, h.address AS hospital_address,h.phone AS hospital_phone,
                   h.contact_email AS hospital_email,h.document_footer,h.document_style
            FROM appointments a JOIN patients p ON p.id = a.patient_id JOIN doctors d ON d.id = a.doctor_id JOIN hospitals h ON h.id=a.hospital_id
            WHERE a.id = ? AND a.doctor_id = ? AND a.hospital_id = ?""",
            (appointment_id, session['user_id'], session['hospital_id'])).fetchone()
        if not appointment:
            return render_template('errors/http.html', status=404, message='Appointment not found.'), 404
        existing = conn.execute("SELECT * FROM prescriptions WHERE appointment_id = ? AND hospital_id = ?",
                                (appointment_id, session['hospital_id'])).fetchone()
        prescription = dict(existing) if existing else None
        if prescription:
            prescription['medicines_parsed'] = parse_medicines(prescription['medicines'])
        versions = conn.execute("SELECT v.* FROM prescription_versions v JOIN prescriptions p ON p.id = v.prescription_id WHERE p.appointment_id = ? ORDER BY v.version DESC",
                                (appointment_id,)).fetchall()
        for revision in versions:
            revision['medicines_parsed'] = parse_medicines(revision['medicines'])
        saved_templates = [dict(item) for item in conn.execute('SELECT id,name,diagnosis,medicines,instructions FROM prescription_templates WHERE doctor_id=? AND hospital_id=? ORDER BY name',
            (session['user_id'],session['hospital_id'])).fetchall()]
        for item in saved_templates:
            item['medicines'] = parse_medicines(item['medicines'])
        previous_rx = conn.execute("SELECT pr.diagnosis,pr.medicines,pr.instructions FROM prescriptions pr JOIN appointments a ON a.id=pr.appointment_id WHERE a.patient_id=? AND a.doctor_id=? AND a.hospital_id=? AND a.id!=? AND pr.status IN ('Signed','Amended') ORDER BY pr.id DESC LIMIT 1",
            (appointment['patient_id'],session['user_id'],session['hospital_id'],appointment_id)).fetchone()
        if previous_rx:
            previous_rx=dict(previous_rx)
            previous_rx['medicines'] = parse_medicines(previous_rx['medicines'])
        if request.method == 'POST':
            try:
                diagnosis = request.form.get('diagnosis', '').strip()
                instructions = request.form.get('instructions', '').strip()
                medicines = medicine_form(request.form)
                version = int(request.form.get('version', '0'))
                action = request.form.get('action', 'save')
                reason = request.form.get('amendment_reason', '').strip()
                if 'patient_allergies' in request.form or 'patient_medical_history' in request.form:
                    lock_record(conn,'patients',appointment['patient_id'])
                    current_patient=conn.execute('SELECT clinical_version,allergies,medical_history FROM patients WHERE id=?',(appointment['patient_id'],)).fetchone()
                    if int(request.form.get('patient_version','0'))!=current_patient['clinical_version']:
                        raise StalePrescription('Patient history changed in another session. Reload before saving.')
                    allergies=request.form.get('patient_allergies',current_patient['allergies'] or '').strip()
                    history=request.form.get('patient_medical_history',current_patient['medical_history'] or '').strip()
                    if len(allergies)>2000 or len(history)>10000:
                        raise ValueError('Patient history exceeds the allowed length.')
                    if allergies!=(current_patient['allergies'] or '') or history!=(current_patient['medical_history'] or ''):
                        conn.execute('UPDATE patients SET allergies=?,medical_history=?,clinical_version=clinical_version+1 WHERE id=?',(allergies,history,appointment['patient_id']))
                        log_audit('update_clinical_history','patient',appointment['patient_id'],conn=conn)
                    appointment['allergies'],appointment['medical_history']=allergies,history
                version, status = save_prescription_version(conn, appointment, existing, diagnosis, medicines,
                    instructions, version, action, request.form.get('reviewed'), reason,
                    session['user_id'], session['hospital_id'])
                if action in {'sign','print','next'} and request.form.get('complete_visit') and appointment['status']=='Scheduled':
                    change_appointment_status(conn,appointment_id,session['user_id'],session['hospital_id'],'Completed')
                conn.commit()
                flash(f'Prescription version {version} saved ({status.lower()}).', 'success')
                if action=='next':
                    next_visit=conn.execute("SELECT id FROM appointments WHERE doctor_id=? AND hospital_id=? AND date=? AND status='Scheduled' AND id!=? ORDER BY CASE WHEN time_slot IS NULL THEN 0 ELSE 1 END,time_slot,id LIMIT 1",
                        (session['user_id'],session['hospital_id'],facility_now().strftime('%Y-%m-%d'),appointment_id)).fetchone()
                    return redirect(context_link('doctor.prescriptions',appointment_id=next_visit['id']) if next_visit else url_for('doctor.doctor_dashboard'))
                return redirect(context_link('doctor.print_prescription' if action == 'print' else 'doctor.prescriptions', appointment_id=appointment_id))
            except StalePrescription as error:
                conn.rollback()
                return jsonify(error=str(error)), 409
            except (ValueError, TypeError) as error:
                conn.rollback()
                flash(str(error), 'danger')
                prescription = {'diagnosis': request.form.get('diagnosis', ''), 'instructions': request.form.get('instructions', ''),
                    'medicines_parsed': locals().get('medicines', prescription['medicines_parsed'] if prescription else []),
                    'version': existing['version'] if existing else 0, 'status': existing['status'] if existing else 'Draft'}
                return render_template('doctor/prescriptions.html', appointment=appointment, prescription=prescription, versions=versions, saved_templates=saved_templates, previous_rx=previous_rx), 422
        return render_template('doctor/prescriptions.html', appointment=appointment, prescription=prescription, versions=versions, saved_templates=saved_templates, previous_rx=previous_rx)
    finally:
        conn.close()

@bp.post('/doctor/prescription-templates')
def save_template():
    conn = get_db_connection()
    try:
        name = request.form.get('template_name','').strip()
        diagnosis = request.form.get('diagnosis','').strip()
        instructions = request.form.get('instructions','').strip()
        if not name or len(name)>100 or not diagnosis or len(diagnosis)>10000 or len(instructions)>10000:
            return jsonify(error='Enter a template name and diagnosis within the allowed length.'),422
        medicines = medicine_form(request.form)
        if conn.execute('SELECT id FROM prescription_templates WHERE doctor_id=? AND hospital_id=? AND name=?',
            (session['user_id'],session['hospital_id'],name)).fetchone():
            return jsonify(error='A template with this name already exists. Choose another name.'),422
        template_id = conn.insert('INSERT INTO prescription_templates(doctor_id,hospital_id,name,diagnosis,medicines,instructions) VALUES(?,?,?,?,?,?)',
            (session['user_id'],session['hospital_id'],name,diagnosis,json.dumps(medicines,ensure_ascii=False),instructions)).lastrowid
        log_audit('create_template','prescription_template',template_id,name,conn=conn)
        conn.commit()
        return jsonify(id=template_id,name=name,diagnosis=diagnosis,medicines=medicines,instructions=instructions)
    except IntegrityError:
        conn.rollback()
        return jsonify(error='A template with this name already exists. Choose another name.'),422
    except ValueError as error:
        conn.rollback()
        return jsonify(error=str(error)),422
    finally:
        conn.close()


@bp.route('/doctor/print_prescription/<int:appointment_id>')
def print_prescription(appointment_id):
    
    conn = get_db_connection()
    try:
        prescription_data = conn.execute('''
            SELECT a.id as appointment_id, a.date, a.time_slot, 
                   p.name as patient_name, p.age, p.gender,
                   d.name as doctor_name, d.specialization,
                   pr.diagnosis, pr.medicines, pr.instructions, pr.status AS prescription_status, pr.version,
                   s.name AS hospital_name,s.address AS hospital_address,s.phone AS hospital_phone,
                   s.contact_email AS hospital_email,s.document_footer,s.document_style
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            JOIN hospitals s ON d.hospital_id = s.id
            LEFT JOIN prescriptions pr ON a.id = pr.appointment_id
            WHERE a.id = ? AND a.hospital_id = ? AND a.doctor_id = ?
        ''', (appointment_id, session['hospital_id'], session['user_id'])).fetchone()
        
        if not prescription_data:
            flash('Prescription not found!', 'danger')
            return redirect(url_for('doctor.doctor_appointments'))
        
        # Get next appointment if exists
        next_appointment = conn.execute('''
            SELECT date, time_slot FROM appointments
            WHERE patient_id = (
                SELECT patient_id FROM appointments WHERE id = ?
            )
            AND date > (
                SELECT date FROM appointments WHERE id = ?
            )
            AND status = 'Scheduled'
            AND hospital_id = ?
            ORDER BY date ASC
            LIMIT 1
        ''', (appointment_id, appointment_id, session['hospital_id'])).fetchone()
        
        medicines_parsed = parse_medicines(prescription_data['medicines'])

        # Create a dictionary with all prescription data
        prescription = dict(prescription_data)
        revision = conn.execute("SELECT identity_snapshot FROM prescription_versions WHERE prescription_id=(SELECT id FROM prescriptions WHERE appointment_id=?) AND version=?", (appointment_id, prescription['version'])).fetchone()
        if revision:
            prescription.update(json.loads(revision['identity_snapshot']))
        prescription['medicines_parsed'] = medicines_parsed
        
        return render_template('doctor/print_prescription.html',
                            prescription=prescription,
                            next_appointment=next_appointment)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

@bp.route('/doctor/patients')
def all_patients_history():
    
    search_query = request.args.get('search', '').strip()
    doctor_id = session['user_id']
    
    conn = get_db_connection()
    patients_total = conn.execute('''
        SELECT COUNT(DISTINCT p.id) FROM patients p
        JOIN appointments a ON p.id = a.patient_id
        WHERE a.doctor_id = ? AND p.hospital_id = ?
    ''', (doctor_id, session['hospital_id'])).fetchone()[0]
    try:
        if search_query:
            patients = page_query(conn, '''
                SELECT DISTINCT p.*, MAX(a.date) as last_visit
                FROM patients p
                JOIN appointments a ON p.id = a.patient_id
                WHERE a.doctor_id = ? 
                AND p.hospital_id = ?
                AND (p.name LIKE ? OR p.contact LIKE ?)
                GROUP BY p.id
                ORDER BY p.name
            ''', (doctor_id, session['hospital_id'], f'%{search_query}%', f'%{search_query}%'))
        else:
            patients = page_query(conn, '''
                SELECT DISTINCT p.*, MAX(a.date) as last_visit
                FROM patients p
                JOIN appointments a ON p.id = a.patient_id
                WHERE a.doctor_id = ? 
                AND p.hospital_id = ?
                GROUP BY p.id
                ORDER BY p.name
            ''', (doctor_id, session['hospital_id']))
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    try:
        return render_template('doctor/all_patients_history.html',
                             patients=patients,
                             patients_total=patients_total,
                             search_query=search_query)
    except Exception as e:
        conn.rollback()
        raise
@bp.route('/doctor/patient_history/<int:patient_id>')
def patient_history(patient_id):
    
    conn = get_db_connection()
    try:
        # Get patient details
        patient = conn.execute('''
            SELECT * FROM patients 
            WHERE id = ? AND hospital_id = ? AND EXISTS (SELECT 1 FROM appointments a WHERE a.patient_id = patients.id AND a.doctor_id = ? AND a.hospital_id = patients.hospital_id)
        ''', (patient_id, session['hospital_id'], session['user_id'])).fetchone()
        
        if not patient:
            flash('Patient not found', 'danger')
            return redirect(url_for('doctor.doctor_dashboard'))
        
        # Get all prescriptions
        prescriptions = conn.execute('''
            SELECT pr.*, a.date, a.time_slot, d.name as doctor_name
            FROM prescriptions pr
            JOIN appointments a ON pr.appointment_id = a.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.patient_id = ? AND a.hospital_id = ?
            ORDER BY a.date DESC
        ''', (patient_id, session['hospital_id'])).fetchall()
        
        # Process prescriptions for display
        prescriptions_list = []
        for prescription in prescriptions:
            pres_dict = dict(prescription)
            medicines_parsed = parse_medicines(prescription['medicines'])
            pres_dict['medicines_parsed'] = medicines_parsed
            prescriptions_list.append(pres_dict)
        
        # Get all appointments
        appointments = conn.execute('''
            SELECT a.*, d.name as doctor_name
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.patient_id = ? AND a.hospital_id = ?
            ORDER BY a.date DESC
        ''', (patient_id, session['hospital_id'])).fetchall()
        
        return render_template('doctor/patient_history.html',
                            patient=patient,
                            prescriptions=prescriptions_list,
                            appointments=appointments)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
