from careblue.presentation import back_url,context_link
from io import BytesIO
from database import IntegrityError, get_db_connection, lock_record
from flask import abort, current_app as app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from datetime import datetime
from careblue.services import facility_now, generate_time_slots, page_query,visit_period_filter
from careblue.security import log_audit

from careblue.routes.admin_blueprint import bp

@bp.route('/admin/get_doctor_slots/<int:doctor_id>/<date>', permission="appointments")
def get_doctor_slots(doctor_id, date):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        day = date_obj.strftime('%A')
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    
    conn = get_db_connection()
    try:
        if not conn.execute('SELECT id FROM doctors WHERE id=? AND hospital_id=? AND active=1',(doctor_id,session['hospital_id'])).fetchone():
            return jsonify(error='Doctor not found or unavailable.'),404
        absent = conn.execute("SELECT id FROM doctor_absences WHERE doctor_id=? AND hospital_id=? AND start_date<=? AND end_date>=?",
                              (doctor_id, session['hospital_id'], date, date)).fetchone()
        if absent:
            return jsonify(error="The doctor is away on this date.")
        slot_info = conn.execute('''
            SELECT * FROM doctor_slots 
            WHERE doctor_id = ? AND day_of_week = ? AND hospital_id = ?
        ''', (doctor_id, day, session['hospital_id'])).fetchone()
        
        if not slot_info:
            from flask import g
            slot_info = {'start_time':g.hospital['opening_time'],'end_time':g.hospital['closing_time'],'break_start':None,'break_end':None}
        
        slots = generate_time_slots(
            slot_info['start_time'],
            slot_info['end_time'],
            slot_info['break_start'],
            slot_info['break_end']
        )
        
        booked_slots = conn.execute('''
            SELECT time_slot FROM appointments
            WHERE doctor_id = ? AND date = ? AND status != 'Cancelled' AND hospital_id = ?
        ''', (doctor_id, date, session['hospital_id'])).fetchall()
        
        booked_slots = [slot['time_slot'] for slot in booked_slots]
        
        return jsonify({
            'slots': slots,
            'booked_slots': booked_slots
        })
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Get slots error: {str(e)}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        conn.close()

@bp.route('/admin/schedule_appointment', methods=['GET', 'POST'], permission="appointments")
def schedule_appointment():
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        patient_id = request.form.get('patient_id', '').strip()
        doctor_id = request.form.get('doctor_id', '').strip()
        date = request.form.get('date', '').strip()
        time_slot = request.form.get('time_slot', '').strip()
        notes = request.form.get('notes', '').strip()
        mode = request.form.get('booking_mode') or ('timed' if time_slot else 'walk_in')
        if mode not in {'timed','walk_in'}:
            flash('Choose walk-in or timed booking.', 'danger')
            return redirect(url_for('admin.schedule_appointment'))
        if mode == 'walk_in':
            date, time_slot = facility_now().strftime('%Y-%m-%d'), None
        else:
            date = date or facility_now().strftime('%Y-%m-%d')
        new_patient = request.form.get('patient_mode') == 'new'
        if not doctor_id.isdigit() or (not new_patient and not patient_id.isdigit()) or (mode == 'timed' and not time_slot):
            flash('Required fields are missing', 'danger')
            return redirect(url_for('admin.schedule_appointment'))

        try:
            appt_date = datetime.strptime(date, '%Y-%m-%d').date()
            if time_slot is not None:
                datetime.strptime(time_slot, '%H:%M')
        except ValueError:
            flash('Invalid date or time slot format', 'danger')
            return redirect(url_for('admin.schedule_appointment'))
        try:
            from careblue.workflows import book_appointment, register_patient
            key = request.form.get('idempotency_key','').strip()[:100] or None
            lock_record(conn,'doctors',doctor_id)
            previous = conn.execute('SELECT id FROM appointments WHERE hospital_id=? AND idempotency_key=?',(session['hospital_id'],key)).fetchone() if key else None
            if not previous:
                if new_patient:
                    from careblue.tenancy import can
                    if not can('registry'):
                        from flask import abort
                        abort(403)
                    fields = {field:request.form.get('patient_'+field,'') for field in ('name','age','gender','contact','allergies','address','medical_history')}
                    patient_id = register_patient(conn,fields,session['hospital_id'],session['user_id'])
                book_appointment(conn, patient_id, doctor_id, session['hospital_id'], date, time_slot, notes, key)
            conn.commit()
            flash('Visit added to the doctor’s queue.' if mode=='walk_in' else 'Appointment scheduled successfully!', 'success')
            return redirect(back_url('admin.view_appointments'))
        except ValueError as error:
            conn.rollback()
            flash(str(error), 'danger')
            return redirect(url_for('admin.schedule_appointment'))
        except IntegrityError:
            conn.rollback()
            flash('That slot was just booked. Please pick another time.', 'warning')
            return redirect(url_for('admin.schedule_appointment'))
        except Exception as e:
            conn.rollback()
            flash('Error scheduling appointment', 'danger')
            app.logger.exception('Schedule appointment failed')
            return redirect(url_for('admin.schedule_appointment'))
        finally:
            conn.close()
    
    try:
        patients = conn.execute('SELECT id, name FROM patients WHERE hospital_id = ? ORDER BY name LIMIT 50',
                              (session['hospital_id'],)).fetchall()
        selected_patient=request.args.get('patient_id','')
        if selected_patient.isdigit() and not any(str(p['id'])==selected_patient for p in patients):
            selected=conn.execute('SELECT id,name FROM patients WHERE id=? AND hospital_id=?',(int(selected_patient),session['hospital_id'])).fetchone()
            if selected:
                patients.append(selected)
        doctors = conn.execute('SELECT id, name, specialization FROM doctors WHERE hospital_id = ? AND active=1 ORDER BY name', 
                             (session['hospital_id'],)).fetchall()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    min_date = facility_now().strftime('%Y-%m-%d')
    
    return render_template('admin/schedule_appointment.html', 
                         patients=patients, 
                         doctors=doctors,
                         min_date=min_date)

@bp.route('/admin/visits/<int:appointment_id>', methods=['GET', 'POST'], permission="appointments")
def review_visit(appointment_id):
    from careblue.workflows import resolve_visit, visit_schedule_revision
    conn = get_db_connection()
    visit = conn.execute('''SELECT a.*,p.name AS patient_name,p.contact,d.name AS doctor_name
        FROM appointments a JOIN patients p ON p.id=a.patient_id JOIN doctors d ON d.id=a.doctor_id
        WHERE a.id=? AND a.hospital_id=?''', (appointment_id, session['hospital_id'])).fetchone()
    if not visit:
        abort(404)
    error = None
    if request.method == 'POST':
        try:
            resolve_visit(conn, appointment_id, session['hospital_id'], request.form.get('action', ''),
                          request.form.get('reason', '').strip(), request.form.get('revision', ''),
                          request.form.get('date', ''), request.form.get('time_slot', '').strip() or None)
            conn.commit()
            flash('Visit cancelled. The record is retained.' if request.form.get('action') == 'cancel' else 'Visit rescheduled.', 'success')
            return redirect(context_link('admin.review_visit', appointment_id=appointment_id))
        except ValueError as exc:
            conn.rollback()
            error = str(exc)
        except IntegrityError:
            conn.rollback()
            error = 'That time was just booked. Choose another time.'
    prescription = conn.execute('SELECT id,status FROM prescriptions WHERE appointment_id=? AND hospital_id=?', (appointment_id, session['hospital_id'])).fetchone()
    bill = conn.execute('SELECT id FROM bills WHERE appointment_id=? AND hospital_id=?', (appointment_id, session['hospital_id'])).fetchone()
    return render_template('admin/review_visit.html', visit=visit, bill=bill, prescription=prescription,
                           revision=visit_schedule_revision(visit), min_date=facility_now().date().isoformat(), error=error), 422 if error else 200


@bp.route('/admin/view_appointments', permission="appointments")
def view_appointments():
    
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()
    
    conn = get_db_connection()
    try:
        query = '''
            SELECT a.id, p.name as patient_name, d.name as doctor_name, 
                   a.date, a.time_slot, a.status, a.notes
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.hospital_id = ?
        '''
        
        params = [session['hospital_id']]
        
        if search_query:
            query += ' AND (p.name LIKE ? OR d.name LIKE ?)'
            params.extend([f'%{search_query}%', f'%{search_query}%'])
        
        if status_filter:
            query += ' AND a.status = ?'
            params.append(status_filter)
        
        period_sql,period_params=visit_period_filter()
        query+=period_sql
        params.extend(period_params)
        query += ' ORDER BY a.date DESC, a.time_slot DESC'
        
        appointments = page_query(conn, query, params)
        appointments_total = conn.execute('SELECT COUNT(*) FROM appointments WHERE hospital_id = ?',
                                          (session['hospital_id'],)).fetchone()[0]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    return render_template('admin/view_appointments.html',
                         appointments=appointments,
                         appointments_total=appointments_total,
                         search_query=search_query,
                         status_filter=status_filter)

@bp.route('/admin/delete_appointment/<int:appointment_id>', methods=['POST'], permission="appointments")
def delete_appointment(appointment_id):

    conn = get_db_connection()
    try:
        cur = conn.execute('DELETE FROM appointments WHERE id = ? AND hospital_id = ?',
                           (appointment_id, session['hospital_id']))
        if cur.rowcount:
            log_audit('delete', 'appointment', appointment_id, conn=conn)
            conn.commit()
            flash('Appointment deleted successfully!', 'success')
        else:
            flash('Appointment not found', 'warning')
    except IntegrityError:
        conn.rollback()
        flash('Cannot delete: related records exist', 'danger')
    except Exception as e:
        conn.rollback()
        flash('Error deleting appointment', 'danger')
        app.logger.error(f"Delete appointment error: {str(e)}")
    finally:
        conn.close()

    return redirect(back_url('admin.view_appointments'))

@bp.route('/admin/export_appointments', permission="appointments")
def export_appointments():
    
    conn = get_db_connection()
    try:
        query = '''
            SELECT p.name as patient_name, d.name as doctor_name, 
                   a.date, a.time_slot, a.status, a.notes
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.hospital_id = ?
        '''
        params=[session['hospital_id']]
        search=request.args.get('search','').strip()
        status=request.args.get('status','').strip()
        if search:
            query+=' AND (p.name LIKE ? OR d.name LIKE ?)'
            params.extend([f'%{search}%',f'%{search}%'])
        if status:
            query+=' AND a.status=?'
            params.append(status)
        period_sql,period_params=visit_period_filter()
        query+=period_sql+' ORDER BY a.date DESC,a.time_slot DESC,a.id DESC'
        params.extend(period_params)
        appointments=conn.execute(query,params).fetchall()

        try:
            import pandas as pd
        except ImportError:
            flash('Excel export is unavailable (export dependencies not installed)', 'danger')
            return redirect(back_url('admin.view_appointments'))
        columns=['patient_name','doctor_name','date','time_slot','status','notes']
        df = pd.DataFrame([[row[column] for column in columns] for row in appointments],columns=['Patient Name', 'Doctor Name', 'Date', 'Time Slot', 'Status', 'Notes'])
        
        output = BytesIO()
        writer = pd.ExcelWriter(output, engine='xlsxwriter',engine_kwargs={'options':{'strings_to_formulas':False,'strings_to_urls':False}})
        df.to_excel(writer, sheet_name='Appointments', index=False)
        writer.close()
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='appointments.xlsx'
        )
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
