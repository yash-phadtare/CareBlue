from database import IntegrityError, get_db_connection
from careblue.constants import PAYMENT_METHODS
from flask import current_app as app, flash, redirect, render_template, request, session, url_for
from careblue.services import bill_totals, from_minor, page_query, parse_money
from careblue.security import log_audit
from database import lock_record
from careblue.presentation import context_link,back_url

from careblue.routes.admin_blueprint import bp

@bp.route('/admin/billing', permission="billing")
def billing():
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()
    conn = get_db_connection()
    try:
        query = """SELECT b.id, b.appointment_id, b.created_at, p.name AS patient_name,
          COALESCE(i.total, 0) AS total_cents, COALESCE(pay.paid, 0) AS paid_cents,
          COALESCE(i.total, 0) - COALESCE(pay.paid, 0) AS balance_cents,
          CASE WHEN COALESCE(i.total,0) > 0 AND COALESCE(pay.paid,0) >= i.total THEN 'Paid'
               WHEN COALESCE(pay.paid,0) > 0 THEN 'Partial' ELSE 'Unpaid' END AS status
          FROM bills b JOIN patients p ON p.id = b.patient_id
          LEFT JOIN (SELECT bill_id, SUM(amount_cents) total FROM bill_items GROUP BY bill_id) i ON i.bill_id = b.id
          LEFT JOIN (SELECT bill_id, SUM(amount_cents) paid FROM payments GROUP BY bill_id) pay ON pay.bill_id = b.id
          WHERE b.hospital_id = ?"""
        params = [session['hospital_id']]
        if search_query:
            query += " AND (LOWER(p.name) LIKE ? OR CAST(b.id AS TEXT) LIKE ?)"
            params += [f"%{search_query.lower()}%", f"%{search_query}%"]
        query = "SELECT * FROM (" + query + ") ledger"
        if status_filter in {'Paid', 'Unpaid', 'Partial'}:
            query += " WHERE status = ?"
            params.append(status_filter)
        summary = conn.execute("SELECT COUNT(*) AS count, COALESCE(SUM(paid_cents),0) AS paid_minor, COALESCE(SUM(balance_cents),0) AS balance_minor FROM (" + query + ") totals", params).fetchone()
        rows = page_query(conn, query + " ORDER BY id DESC", params)
    finally:
        conn.close()
    return render_template('admin/billing.html', bills=rows, total_bills=summary[0],
        collected=from_minor(summary[1]), outstanding=from_minor(summary[2]),
        search_query=search_query, status_filter=status_filter)


@bp.route('/admin/billing/new', permission="billing")
def new_bill():
    from flask import abort
    selected_visit=request.args.get('appointment_id','').strip()
    selected_patient=request.args.get('patient_id','').strip()
    def valid_id(value):
        return value if value.isascii() and value.isdigit() and len(value)<=19 and 0<int(value)<=2**63-1 else ''
    selected_visit=valid_id(selected_visit)
    selected_patient=valid_id(selected_patient)
    conn = get_db_connection()
    try:
        if selected_visit:
            existing_bill=conn.execute('SELECT id FROM bills WHERE appointment_id=? AND hospital_id=?',(selected_visit,session['hospital_id'])).fetchone()
            if existing_bill:
                return redirect(context_link('admin.bill_detail',bill_id=existing_bill['id']))
        patients = conn.execute('SELECT id, name FROM patients WHERE hospital_id = ? ORDER BY name,id LIMIT 50',
                                (session['hospital_id'],)).fetchall()
        visit_query='''
            SELECT a.id, a.date, a.time_slot, p.name AS patient_name, p.id AS patient_id,
                   d.name AS doctor_name, d.consultation_fee_cents
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            LEFT JOIN bills b ON b.appointment_id = a.id
            WHERE a.hospital_id = ? AND a.status != 'Cancelled' AND b.id IS NULL
        '''
        visits=conn.execute(visit_query+' ORDER BY a.date DESC,a.id DESC LIMIT 100',(session['hospital_id'],)).fetchall()
        selected=next((visit for visit in visits if str(visit['id'])==selected_visit),None)
        if selected_visit and not selected:
            selected=conn.execute(visit_query+' AND a.id=?',(session['hospital_id'],selected_visit)).fetchone()
            if not selected:
                abort(404)
            visits.append(selected)
        if selected:
            selected_patient=str(selected['patient_id'])
        if selected_patient and not any(str(patient['id'])==selected_patient for patient in patients):
            patient=conn.execute('SELECT id,name FROM patients WHERE hospital_id=? AND id=?',(session['hospital_id'],selected_patient)).fetchone()
            if patient:patients.append(patient)
            else:selected_patient=''
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    return render_template('admin/bill_form.html', patients=patients, visits=visits,
                           preselect_patient=selected_patient,
                           preselect_visit=selected_visit)

@bp.route('/admin/billing/create', methods=['POST'], permission="billing")
def create_bill():
    patient_id = request.form.get('patient_id', '').strip()
    appointment_id = request.form.get('appointment_id', '').strip() or None
    notes = request.form.get('notes', '').strip()
    idempotency_key = request.form.get('idempotency_key', '').strip()[:100] or None
    try:
        item_count = int(request.form.get('item_count', '0'))
    except (TypeError, ValueError):
        item_count = 0

    items = []
    if not 0 <= item_count <= 100:
        flash('Use at most 100 bill items.', 'danger')
        return redirect(url_for('admin.new_bill'))
    for i in range(1, item_count + 1):
        label = (request.form.get(f'item_label_{i}', '') or '').strip()
        amount = parse_money(request.form.get(f'item_amount_{i}', ''))
        raw_amount = request.form.get(f'item_amount_{i}', '').strip()
        if not label and not raw_amount:
            continue
        if not label or amount is None or amount <= 0:
            flash(f'Item {i}: enter a label and a positive amount.', 'danger')
            return redirect(url_for('admin.new_bill'))
        items.append((label[:120], amount))
    if not patient_id or (not items and not appointment_id):
        flash('A patient and at least one billed item are required', 'danger')
        return redirect(url_for('admin.new_bill'))

    conn = get_db_connection()
    try:
        from careblue.workflows import create_bill as create_bill_record
        bill_id = create_bill_record(conn, patient_id, appointment_id, session['hospital_id'], items, notes, idempotency_key)
        if request.form.get('pay_now'):
            from careblue.workflows import pay_bill
            from careblue.services import bill_totals
            amount = request.form.get('payment_amount','').strip()
            payment_key = idempotency_key+':payment' if idempotency_key else None
            old_payment = conn.execute('SELECT amount_cents FROM payments WHERE bill_id=? AND idempotency_key=?',(bill_id,payment_key)).fetchone() if payment_key else None
            amount = parse_money(amount) if amount else (from_minor(old_payment['amount_cents']) if old_payment else bill_totals(conn,bill_id)['balance'])
            pay_bill(conn,bill_id,session['hospital_id'],amount,request.form.get('payment_method','Cash'),
                payment_key)
        conn.commit()
        flash(f"Bill #{bill_id} created", 'success')
        return redirect(context_link('admin.bill_detail', bill_id=bill_id))
    except ValueError as error:
        conn.rollback()
        flash(str(error), 'danger')
        return redirect(url_for('admin.new_bill'))
    except IntegrityError:
        conn.rollback()
        flash('That visit is already billed', 'warning')
        return redirect(url_for('admin.new_bill'))
    except Exception as e:
        conn.rollback()
        flash('Error creating bill', 'danger')
        app.logger.error(f"Create bill error: {str(e)}")
        return redirect(url_for('admin.new_bill'))
    finally:
        conn.close()

def _load_bill(conn, bill_id, hospital_id):
    bill = conn.execute('''
        SELECT b.*, p.name AS patient_name, p.age, p.gender, p.contact
        FROM bills b JOIN patients p ON b.patient_id = p.id
        WHERE b.id = ? AND b.hospital_id = ?
    ''', (bill_id, hospital_id)).fetchone()
    if not bill:
        return None
    items = conn.execute('SELECT * FROM bill_items WHERE bill_id = ? ORDER BY id', (bill_id,)).fetchall()
    payments = conn.execute('SELECT * FROM payments WHERE bill_id = ? ORDER BY id', (bill_id,)).fetchall()
    data = dict(bill)
    data['items'] = [dict(i) for i in items]
    data['payments'] = [dict(p) for p in payments]
    data.update(bill_totals(conn, bill_id))
    return data

@bp.route('/admin/billing/<int:bill_id>', permission="billing")
def bill_detail(bill_id):
    conn = get_db_connection()
    try:
        bill = _load_bill(conn, bill_id, session['hospital_id'])
        if not bill:
            flash('Bill not found', 'warning')
            return redirect(url_for('admin.billing'))
    finally:
        conn.close()
    return render_template('admin/bill_detail.html', bill=bill,
                           methods=['Cash', 'Card', 'UPI', 'Insurance', 'Other'])

@bp.route('/admin/billing/<int:bill_id>/pay', methods=['POST'], permission="billing")
def record_payment(bill_id):
    amount = parse_money(request.form.get('amount', ''))
    method = request.form.get('method', '').strip()
    idempotency_key = request.form.get('idempotency_key', '').strip()[:100] or None
    if amount is None or amount <= 0:
        flash('Enter a valid payment amount', 'danger')
        return redirect(context_link('admin.bill_detail', bill_id=bill_id))
    if method not in PAYMENT_METHODS:
        flash('Select a valid payment method', 'danger')
        return redirect(context_link('admin.bill_detail', bill_id=bill_id))
    conn = get_db_connection()
    try:
        from careblue.workflows import pay_bill
        pay_bill(conn, bill_id, session['hospital_id'], amount, method, idempotency_key)
        conn.commit()
        flash(f"Payment of ₹{amount:.2f} recorded", 'success')
    except ValueError as error:
        conn.rollback()
        flash(str(error), 'danger')
    except Exception as e:
        conn.rollback()
        flash('Error recording payment', 'danger')
        app.logger.error(f"Record payment error: {str(e)}")
    finally:
        conn.close()
    return redirect(context_link('admin.bill_detail', bill_id=bill_id))

@bp.route('/admin/billing/<int:bill_id>/delete', methods=['POST'], permission="billing")
def delete_bill(bill_id):
    conn = get_db_connection()
    try:
        bill = conn.execute('SELECT id FROM bills WHERE id = ? AND hospital_id = ?',
                            (bill_id, session['hospital_id'])).fetchone()
        if not bill:
            flash('Bill not found', 'warning')
            return redirect(url_for('admin.billing'))
        lock_record(conn, 'bills', bill_id)
        n = conn.execute('SELECT COUNT(*) FROM payments WHERE bill_id = ?', (bill_id,)).fetchone()[0]
        if n > 0:
            flash('Cannot delete a bill with recorded payments', 'danger')
            return redirect(context_link('admin.bill_detail', bill_id=bill_id))
        conn.execute('DELETE FROM bill_items WHERE bill_id = ?', (bill_id,))
        conn.execute('DELETE FROM bills WHERE id = ?', (bill_id,))
        log_audit('delete', 'bill', bill_id, conn=conn)
        conn.commit()
        flash('Bill deleted', 'success')
        return redirect(back_url('admin.billing'))
    except Exception as e:
        conn.rollback()
        flash('Error deleting bill', 'danger')
        app.logger.error(f"Delete bill error: {str(e)}")
        return redirect(context_link('admin.bill_detail', bill_id=bill_id))
    finally:
        conn.close()

@bp.route('/admin/billing/<int:bill_id>/invoice', permission="billing")
def bill_invoice(bill_id):
    conn = get_db_connection()
    try:
        bill = _load_bill(conn, bill_id, session['hospital_id'])
        if not bill:
            flash('Bill not found', 'warning')
            return redirect(url_for('admin.billing'))
    finally:
        conn.close()
    return render_template('admin/invoice.html', bill=bill,
                           hospital_name=session.get('hospital_name', ''))

# ---------------- Pharmacy inventory ----------------
