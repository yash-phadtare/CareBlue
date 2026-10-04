from database import IntegrityError, get_db_connection
from flask import current_app as app, flash, redirect, render_template, request, session, url_for
from careblue.security import log_audit
from database import lock_record

from careblue.routes.admin_blueprint import bp

@bp.route('/admin/beds', permission="beds")
def beds():

    conn = get_db_connection()
    try:
        wards = conn.execute('SELECT * FROM wards WHERE hospital_id = ? ORDER BY name',
                             (session['hospital_id'],)).fetchall()
        beds = conn.execute('''
            SELECT b.*, p.name AS patient_name
            FROM beds b LEFT JOIN patients p ON b.patient_id = p.id
            WHERE b.hospital_id = ? AND b.archived=0 ORDER BY b.bed_number
        ''', (session['hospital_id'],)).fetchall()
        patients = conn.execute('SELECT id, name FROM patients WHERE hospital_id = ? ORDER BY name,id LIMIT 50',
                                (session['hospital_id'],)).fetchall()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

    by_ward = {}
    for w in wards:
        by_ward[w['id']] = {'ward': w, 'beds': []}
    for b in beds:
        if b['ward_id'] in by_ward:
            by_ward[b['ward_id']]['beds'].append(b)
    counts = {
        'total': len(beds),
        'free': sum(1 for b in beds if b['status'] == 'Available'),
        'occupied': sum(1 for b in beds if b['status'] == 'Occupied'),
    }
    return render_template('admin/beds.html', ward_groups=list(by_ward.values()),
                           patients=patients, counts=counts)

@bp.route('/admin/wards/add', methods=['POST'], permission="beds")
def add_ward():
    name = request.form.get('name', '').strip()
    ward_type = request.form.get('ward_type', '').strip()
    if not name or len(name) > 80:
        flash('Ward name is required (max 80 chars)', 'danger')
        return redirect(url_for('admin.beds'))
    conn = get_db_connection()
    try:
        cur = conn.insert('INSERT INTO wards (name, ward_type, hospital_id) VALUES (?, ?, ?)',
                           (name, ward_type[:80], session['hospital_id']))
        log_audit('create', 'ward', cur.lastrowid, name, conn=conn)
        conn.commit()
        flash('Ward added', 'success')
    except Exception as e:
        conn.rollback()
        flash('Error adding ward', 'danger')
        app.logger.error(f"Add ward error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('admin.beds'))

@bp.route('/admin/wards/<int:ward_id>/delete', methods=['POST'], permission="beds")
def delete_ward(ward_id):
    conn = get_db_connection()
    try:
        n = conn.execute('SELECT COUNT(*) FROM beds WHERE ward_id = ? AND hospital_id = ?',
                         (ward_id, session['hospital_id'])).fetchone()[0]
        if n > 0:
            flash('Cannot delete a ward that still has beds', 'danger')
            return redirect(url_for('admin.beds'))
        cur = conn.execute('DELETE FROM wards WHERE id = ? AND hospital_id = ?',
                           (ward_id, session['hospital_id']))
        if cur.rowcount:
            log_audit('delete', 'ward', ward_id, conn=conn)
            conn.commit()
            flash('Ward deleted', 'success')
        else:
            flash('Ward not found', 'warning')
    except Exception as e:
        conn.rollback()
        flash('Error deleting ward', 'danger')
        app.logger.error(f"Delete ward error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('admin.beds'))

@bp.route('/admin/beds/add', methods=['POST'], permission="beds")
def add_bed():
    ward_id = request.form.get('ward_id', '').strip()
    bed_number = request.form.get('bed_number', '').strip()
    if not ward_id or not bed_number or len(bed_number) > 20:
        flash('Ward and bed number are required', 'danger')
        return redirect(url_for('admin.beds'))
    conn = get_db_connection()
    try:
        ward = conn.execute('SELECT id FROM wards WHERE id = ? AND hospital_id = ?',
                            (ward_id, session['hospital_id'])).fetchone()
        if not ward:
            flash('Ward not found', 'danger')
            return redirect(url_for('admin.beds'))
        cur = conn.insert('INSERT INTO beds (ward_id, bed_number, hospital_id) VALUES (?, ?, ?)',
                           (ward_id, bed_number, session['hospital_id']))
        log_audit('create', 'bed', cur.lastrowid, f"ward={ward_id} bed={bed_number}", conn=conn)
        conn.commit()
        flash('Bed added', 'success')
    except IntegrityError:
        conn.rollback()
        flash('That bed number already exists in this ward', 'warning')
    except Exception as e:
        conn.rollback()
        flash('Error adding bed', 'danger')
        app.logger.error(f"Add bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('admin.beds'))

@bp.route('/admin/beds/<int:bed_id>/delete', methods=['POST'], permission="beds")
def delete_bed(bed_id):
    conn = get_db_connection()
    try:
        lock_record(conn, 'beds', bed_id)
        bed = conn.execute('SELECT * FROM beds WHERE id = ? AND hospital_id = ? AND archived=0',
                           (bed_id, session['hospital_id'])).fetchone()
        if not bed:
            flash('Bed not found', 'warning')
            return redirect(url_for('admin.beds'))
        if bed['status'] != 'Available' or bed['patient_id']:
            flash('Only free beds can be removed', 'danger')
            return redirect(url_for('admin.beds'))
        conn.execute('UPDATE beds SET archived=1 WHERE id = ? AND hospital_id = ?', (bed_id, session['hospital_id']))
        log_audit('archive', 'bed', bed_id, bed['bed_number'], conn=conn)
        conn.commit()
        flash('Bed archived; admission history retained.', 'success')
    except Exception as e:
        conn.rollback()
        flash('Error removing bed', 'danger')
        app.logger.error(f"Delete bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('admin.beds'))

@bp.route('/admin/beds/<int:bed_id>/assign', methods=['POST'], permission="beds")
def assign_bed(bed_id):
    patient_id = request.form.get('patient_id', '').strip()
    if not patient_id:
        flash('Select a patient', 'danger')
        return redirect(url_for('admin.beds'))
    conn = get_db_connection()
    try:
        from careblue.workflows import assign_bed
        assign_bed(conn, bed_id, patient_id, session['hospital_id'])
        conn.commit()
        flash('Bed assigned.', 'success')
    except ValueError as error:
        conn.rollback()
        flash(str(error), 'danger')
    except Exception as e:
        conn.rollback()
        flash('Error assigning bed', 'danger')
        app.logger.error(f"Assign bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('admin.beds'))

@bp.route('/admin/beds/<int:bed_id>/discharge', methods=['POST'], permission="beds")
def discharge_bed(bed_id):
    conn = get_db_connection()
    try:
        from careblue.workflows import discharge_bed
        discharge_bed(conn, bed_id, session['hospital_id'])
        conn.commit()
        flash('Patient discharged; bed freed.', 'success')
    except ValueError as error:
        conn.rollback()
        flash(str(error), 'danger')
    except Exception as e:
        conn.rollback()
        flash('Error freeing bed', 'danger')
        app.logger.error(f"Discharge bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('admin.beds'))

@bp.route('/admin/beds/<int:bed_id>/maintenance', methods=['POST'], permission="beds")
def maintenance_bed(bed_id):
    conn = get_db_connection()
    try:
        lock_record(conn, 'beds', bed_id)
        bed = conn.execute('SELECT * FROM beds WHERE id = ? AND hospital_id = ? AND archived=0',
                           (bed_id, session['hospital_id'])).fetchone()
        if not bed:
            flash('Bed not found', 'warning')
            return redirect(url_for('admin.beds'))
        if bed['status'] == 'Occupied':
            flash('Occupied beds cannot go into maintenance', 'danger')
            return redirect(url_for('admin.beds'))
        new_status = 'Maintenance' if bed['status'] == 'Available' else 'Available'
        conn.execute('UPDATE beds SET status = ? WHERE id = ? AND hospital_id = ?',
                     (new_status, bed_id, session['hospital_id']))
        log_audit('maintenance' if new_status == 'Maintenance' else 'reopen', 'bed', bed_id, bed['bed_number'], conn=conn)
        conn.commit()
        flash(f"Bed {bed['bed_number']} marked {new_status.lower()}", 'success')
    except Exception as e:
        conn.rollback()
        flash('Error updating bed', 'danger')
        app.logger.error(f"Bed maintenance error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('admin.beds'))
