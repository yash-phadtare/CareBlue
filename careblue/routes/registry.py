from database import IntegrityError, get_db_connection
from flask import current_app as app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from careblue.security import log_audit
import os
from careblue.services import page_query, to_minor, validate_doctor_input, validate_patient_input
from careblue.storage import save_doctor_photo
from careblue.accounts import password_error
from careblue.presentation import back_url,context_link

from careblue.routes.admin_blueprint import bp

@bp.route('/admin/add_patient', methods=['GET', 'POST'], permission="registry")
def add_patient():
    
    if request.method == 'POST':
        conn = get_db_connection()
        try:
            from careblue.workflows import register_patient
            patient_id = register_patient(conn, request.form, session['hospital_id'], session['user_id'],
                request.form.get('idempotency_key','').strip()[:100] or None)
            conn.commit()
            flash('Patient added successfully!', 'success')
            if request.form.get('next') == 'visit':
                return redirect(context_link('admin.schedule_appointment',patient_id=patient_id))
            return redirect(back_url('admin.view_patients'))
        except ValueError as error:
            conn.rollback()
            flash(str(error), 'danger')
            return redirect(url_for('admin.add_patient'))
        except Exception as e:
            conn.rollback()
            flash('Error adding patient', 'danger')
            app.logger.error(f"Add patient error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('admin/add_patient.html')

@bp.route('/admin/edit_patient/<int:patient_id>',methods=['GET','POST'],permission='registry')
def edit_patient(patient_id):
    from flask import abort
    from database import lock_record
    from careblue.services import patient_input
    conn=get_db_connection()
    try:
        if request.method=='POST':
            lock_record(conn,'patients',patient_id)
        patient=conn.execute('SELECT * FROM patients WHERE id=? AND hospital_id=?',(patient_id,session['hospital_id'])).fetchone()
        if not patient:
            abort(404)
        if request.method=='POST':
            try:
                if int(request.form.get('version','0'))!=patient['clinical_version']:
                    return render_template('errors/http.html',status=409,message='Patient details changed in another session. Reload before saving.'),409
                values=patient_input(request.form)
                fields=tuple(values)
                conn.execute('UPDATE patients SET '+','.join(field+'=?' for field in fields)+',clinical_version=clinical_version+1 WHERE id=?',(*values.values(),patient_id))
                log_audit('update','patient',patient_id,values['name'],conn=conn)
                conn.commit()
                flash('Patient details saved.','success')
                return redirect(back_url('admin.view_patients'))
            except ValueError as error:
                conn.rollback()
                flash(str(error),'danger')
                return render_template('admin/add_patient.html',patient=patient),422
        return render_template('admin/add_patient.html',patient=patient)
    finally:
        conn.close()


@bp.route('/admin/add_doctor', methods=['GET', 'POST'], permission="doctors")
def add_doctor():
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        specialization = request.form.get('specialization', '').strip()
        experience = request.form.get('experience', '').strip() or '0'
        consultation_fee = request.form.get('consultation_fee', '').strip() or '0'
        contact = request.form.get('contact', '').strip()
        bio = request.form.get('bio', '').strip()
        
        if not all([name, specialization]):
            flash('Required fields are missing', 'danger')
            return redirect(url_for('admin.add_doctor'))

        error = validate_doctor_input(name, specialization, experience, consultation_fee, contact)
        if error:
            flash(error, 'danger')
            return redirect(url_for('admin.add_doctor'))
        username = request.form.get('username','').strip() or None
        password = request.form.get('password','')
        if username or password:
            import re
            if not username or not re.fullmatch(r'[A-Za-z0-9_]{4,80}',username) or password_error(password):
                flash('Enter a username of at least 4 letters, numbers or underscores and a password of at least 12 characters.','danger')
                return redirect(url_for('admin.add_doctor'))

        image_path = save_doctor_photo(request.files.get('image'))
        if 'image' in request.files and request.files['image'].filename and image_path is None:
            flash('Uploaded file is not a valid JPG/PNG image', 'danger')
            return redirect(url_for('admin.add_doctor'))

        conn = get_db_connection()
        try:
            cur = conn.insert('''
                INSERT INTO doctors (name, specialization, experience, consultation_fee_cents, contact, bio, image_path, created_by, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, specialization, int(experience), to_minor(consultation_fee), contact, bio, image_path, session['user_id'], session['hospital_id']))
            if username:
                conn.execute('UPDATE doctors SET username=?,password=? WHERE id=?', (username,generate_password_hash(password),cur.lastrowid))
            log_audit('create', 'doctor', cur.lastrowid, f"{name} ({specialization})", conn=conn)
            conn.commit()
            flash('Doctor added successfully!', 'success')
            return redirect(back_url('admin.view_doctors'))
        except IntegrityError:
            conn.rollback()
            flash('This username is already used in your hospital.','danger')
        except Exception as e:
            conn.rollback()
            flash('Error adding doctor', 'danger')
            app.logger.error(f"Add doctor error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('admin/add_doctor.html')

@bp.route('/admin/view_patients', permission="registry")
def view_patients():
    
    search_query = request.args.get('search', '').strip()
    
    conn = get_db_connection()
    patients_total = conn.execute('SELECT COUNT(*) FROM patients WHERE hospital_id = ?',
                                  (session['hospital_id'],)).fetchone()[0]
    try:
        if search_query:
            patients = page_query(conn, '''
                SELECT * FROM patients 
                WHERE (name LIKE ? OR contact LIKE ?) AND hospital_id = ?
                ORDER BY name
            ''', (f'%{search_query}%', f'%{search_query}%', session['hospital_id']))
        else:
            patients = page_query(conn, 'SELECT * FROM patients WHERE hospital_id = ? ORDER BY name', 
                                   (session['hospital_id'],))
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    return render_template('admin/view_patients.html',
                         patients=patients,
                         patients_total=patients_total,
                         search_query=search_query)

@bp.route('/admin/view_doctors', permission="doctors")
def view_doctors():
    
    search_query = request.args.get('search', '').strip()
    
    conn = get_db_connection()
    doctors_total = conn.execute('SELECT COUNT(*) FROM doctors WHERE hospital_id = ?',
                                 (session['hospital_id'],)).fetchone()[0]
    try:
        if search_query:
            doctors = page_query(conn, '''
                SELECT * FROM doctors 
                WHERE (name LIKE ? OR specialization LIKE ?) AND hospital_id = ?
                ORDER BY name
            ''', (f'%{search_query}%', f'%{search_query}%', session['hospital_id']))
        else:
            doctors = page_query(conn, 'SELECT * FROM doctors WHERE hospital_id = ? ORDER BY name', 
                                  (session['hospital_id'],))
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    return render_template('admin/view_doctors.html',
                         doctors=doctors,
                         doctors_total=doctors_total,
                         search_query=search_query)

@bp.route('/admin/set_doctor_credentials/<int:doctor_id>', methods=['GET', 'POST'], permission="doctors")
def set_doctor_credentials(doctor_id):
    
    conn = get_db_connection()
    doctor = conn.execute('SELECT * FROM doctors WHERE id = ? AND hospital_id = ?', 
                         (doctor_id, session['hospital_id'])).fetchone()
    
    if not doctor:
        flash('Doctor not found', 'danger')
        return redirect(back_url('admin.view_doctors'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        import re
        if not re.fullmatch(r"[A-Za-z0-9_]{4,80}", username):
            flash('Username must be at least 4 characters', 'danger')
            return redirect(url_for('admin.set_doctor_credentials', doctor_id=doctor_id))
        
        if (password or not doctor['password']) and password_error(password):
            flash(password_error(password), 'danger')
            return redirect(url_for('admin.set_doctor_credentials', doctor_id=doctor_id))
        
        try:
            from database import lock_record
            lock_record(conn,'doctors',doctor_id)
            doctor=conn.execute('SELECT * FROM doctors WHERE id=? AND hospital_id=?',(doctor_id,session['hospital_id'])).fetchone()
            if not password and username==doctor['username']:
                flash('No sign-in changes to save.','info')
                return redirect(back_url('admin.view_doctors'))
            existing = conn.execute(
                'SELECT id FROM doctors WHERE username = ? AND id != ? AND hospital_id=?',
                (username, doctor_id,session['hospital_id'])
            ).fetchone()
            
            if existing:
                flash('Username already in use', 'danger')
                return redirect(url_for('admin.set_doctor_credentials', doctor_id=doctor_id))
            
            conn.execute(
                'UPDATE doctors SET username = ?, password = ?, session_version = session_version + 1 WHERE id = ? AND hospital_id = ?',
                (username, generate_password_hash(password) if password else doctor['password'], doctor_id, session['hospital_id'])
            )
            log_audit('update_credentials', 'doctor', doctor_id, username, conn=conn)
            conn.commit()
            flash('Doctor credentials updated successfully!', 'success')
            return redirect(back_url('admin.view_doctors'))
        except IntegrityError as e:
            conn.rollback()
            flash('Failed to update credentials', 'danger')
            app.logger.error(f"Credential update error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('admin/set_doctor_credentials.html', doctor=doctor)

@bp.route('/admin/set_doctor_slots/<int:doctor_id>', methods=['GET', 'POST'], permission="doctors")
def set_doctor_slots(doctor_id):
    
    conn = get_db_connection()
    doctor = conn.execute('SELECT * FROM doctors WHERE id=? AND hospital_id=?',
                          (doctor_id, session['hospital_id'])).fetchone()
    if not doctor:
        from flask import abort
        abort(404)
    if request.method == 'POST':
        from careblue.workflows import replace_doctor_hours
        day_off = bool(request.form.get('day_off'))
        try:
            if request.form.get('action') == 'save_week':
                for day in ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'):
                    prefix = day.lower() + '_'
                    off = bool(request.form.get(prefix+'day_off'))
                    replace_doctor_hours(conn, doctor_id, session['hospital_id'], day,
                        None if off else request.form.get(prefix+'start_time','').strip(),
                        None if off else request.form.get(prefix+'end_time','').strip(),
                        request.form.get(prefix+'break_start','').strip() or None,
                        request.form.get(prefix+'break_end','').strip() or None)
            else:
                replace_doctor_hours(conn, doctor_id, session['hospital_id'], request.form.get('day', '').strip(),
                    None if day_off else request.form.get('start_time', '').strip(),
                    None if day_off else request.form.get('end_time', '').strip(),
                    request.form.get('break_start', '').strip() or None,
                    request.form.get('break_end', '').strip() or None)
            conn.commit()
            flash('Doctor availability updated.', 'success')
        except ValueError as error:
            conn.rollback()
            flash(str(error), 'danger')
        finally:
            conn.close()
        return redirect(context_link('admin.set_doctor_slots', doctor_id=doctor_id))

    try:
        slots = conn.execute('''
            SELECT * FROM doctor_slots 
            WHERE doctor_id = ? AND hospital_id = ?
            ORDER BY 
                CASE day_of_week
                    WHEN 'Monday' THEN 1
                    WHEN 'Tuesday' THEN 2
                    WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4
                    WHEN 'Friday' THEN 5
                    WHEN 'Saturday' THEN 6
                    WHEN 'Sunday' THEN 7
                END
        ''', (doctor_id, session['hospital_id'])).fetchall()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    return render_template('admin/set_doctor_slots.html', 
                         doctor=doctor, 
                         slots=slots)

@bp.route('/admin/delete_doctor/<int:doctor_id>', methods=['POST'], permission="doctors")
def delete_doctor(doctor_id):

    conn = get_db_connection()
    try:
        # First check if doctor has any appointments
        appointments = conn.execute('SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND hospital_id = ?',
                                    (doctor_id, session['hospital_id'])).fetchone()[0]

        if appointments > 0:
            flash('Cannot delete doctor with existing appointments', 'danger')
            return redirect(back_url('admin.view_doctors'))

        # Delete doctor's image if exists
        doctor = conn.execute('SELECT image_path FROM doctors WHERE id = ? AND hospital_id = ?',
                              (doctor_id, session['hospital_id'])).fetchone()
        if doctor and doctor['image_path'] and not doctor['image_path'].startswith('s3:'):
            try:
                img_path = os.path.normpath(os.path.join('static', doctor['image_path']))
                if img_path.startswith(os.path.join('static', 'images', 'doctors')) and os.path.isfile(img_path):
                    os.remove(img_path)
            except OSError:
                pass
            try:
                # Vercel/ephemeral uploads live under UPLOAD_FOLDER (/tmp)
                tmp_path = os.path.normpath(os.path.join(app.config['UPLOAD_FOLDER'],
                                                          os.path.basename(doctor['image_path'])))
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

        # Delete doctor
        cur = conn.execute('DELETE FROM doctors WHERE id = ? AND hospital_id = ?',
                           (doctor_id, session['hospital_id']))
        if cur.rowcount:
            log_audit('delete', 'doctor', doctor_id, conn=conn)
            conn.commit()
            flash('Doctor deleted successfully', 'success')
        else:
            flash('Doctor not found', 'warning')
    except IntegrityError:
        conn.rollback()
        flash('Cannot delete doctor with existing records', 'danger')
    except Exception as e:
        conn.rollback()
        flash('Error deleting doctor', 'danger')
        app.logger.error(f"Error deleting doctor: {str(e)}")
    finally:
        conn.close()

    return redirect(back_url('admin.view_doctors'))

@bp.route('/admin/delete_patient/<int:patient_id>', methods=['POST'], permission="registry")
def delete_patient(patient_id):

    conn = get_db_connection()
    try:
        # First check if patient has any appointments
        appointments = conn.execute('SELECT COUNT(*) FROM appointments WHERE patient_id = ? AND hospital_id = ?',
                                    (patient_id, session['hospital_id'])).fetchone()[0]

        if appointments > 0:
            flash('Cannot delete patient with existing appointments', 'danger')
            return redirect(back_url('admin.view_patients'))

        # Delete patient
        cur = conn.execute('DELETE FROM patients WHERE id = ? AND hospital_id = ?',
                           (patient_id, session['hospital_id']))
        if cur.rowcount:
            log_audit('delete', 'patient', patient_id, conn=conn)
            conn.commit()
            flash('Patient deleted successfully', 'success')
        else:
            flash('Patient not found', 'warning')
    except IntegrityError:
        conn.rollback()
        flash('Cannot delete patient with existing records', 'danger')
    except Exception as e:
        conn.rollback()
        flash('Error deleting patient', 'danger')
        app.logger.error(f"Error deleting patient: {str(e)}")
    finally:
        conn.close()

    return redirect(back_url('admin.view_patients'))

# ---------------- Billing & invoices ----------------
