from careblue.presentation import back_url,context_link
from flask import current_app as app, flash, redirect, render_template, request, session, url_for
from database import get_db_connection
from careblue.security import log_audit
from careblue.services import page_query, parse_money, to_minor
from database import lock_record

from careblue.routes.admin_blueprint import bp

@bp.route('/admin/pharmacy', permission="pharmacy")
def pharmacy():
    search_query = request.args.get('search', '').strip()
    low_only = request.args.get('low') == '1'
    conn = get_db_connection()
    try:
        query = "SELECT * FROM medicines WHERE hospital_id = ? AND archived=0"
        params = [session['hospital_id']]
        if search_query:
            query += " AND LOWER(name) LIKE ?"
            params.append(f"%{search_query.lower()}%")
        if low_only:
            query += " AND stock_qty <= reorder_level"
        medicines = page_query(conn, query + " ORDER BY name, id", params)
        total = conn.execute("SELECT COUNT(*) FROM medicines WHERE hospital_id = ? AND archived=0", (session['hospital_id'],)).fetchone()[0]
        low_count = conn.execute("SELECT COUNT(*) FROM medicines WHERE hospital_id = ? AND archived=0 AND stock_qty <= reorder_level", (session['hospital_id'],)).fetchone()[0]
    finally:
        conn.close()
    return render_template('admin/pharmacy.html', medicines=medicines, total=total,
        low_count=low_count, search_query=search_query, low_only=low_only)


@bp.route('/admin/medicines/add', methods=['POST'], permission="pharmacy")
def add_medicine():
    name = request.form.get('name', '').strip()
    strength = request.form.get('strength', '').strip()
    unit = request.form.get('unit', '').strip()
    stock = request.form.get('stock_qty', '').strip()
    reorder = request.form.get('reorder_level', '').strip()
    price = request.form.get('unit_price', '').strip()
    if not name or len(name) > 120:
        flash('Medicine name is required (max 120 chars)', 'danger')
        return redirect(back_url('admin.pharmacy'))
    try:
        stock_n = int(stock or 0)
        reorder_n = int(reorder or 10)
    except (TypeError, ValueError):
        flash('Stock and reorder level must be whole numbers', 'danger')
        return redirect(back_url('admin.pharmacy'))
    unit_price = parse_money(price or 0)
    if stock_n < 0 or stock_n > 1000000 or reorder_n < 0 or reorder_n > 1000000 or unit_price is None:
        flash('Stock, reorder level or price is out of range', 'danger')
        return redirect(back_url('admin.pharmacy'))
    conn = get_db_connection()
    try:
        cur = conn.insert('''
            INSERT INTO medicines (name, strength, unit, stock_qty, reorder_level, unit_price_cents, hospital_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, strength[:60], unit[:30], stock_n, reorder_n, to_minor(unit_price), session['hospital_id']))
        log_audit('create', 'medicine', cur.lastrowid, f"{name} stock={stock_n}", conn=conn)
        conn.commit()
        flash('Medicine added to inventory', 'success')
    except Exception as e:
        flash('Error adding medicine', 'danger')
        app.logger.error(f"Add medicine error: {str(e)}")
    finally:
        conn.close()
    return redirect(back_url('admin.pharmacy'))

@bp.route('/admin/medicines/<int:medicine_id>/update', methods=['POST'], permission="pharmacy")
def update_medicine(medicine_id):
    name = request.form.get('name', '').strip()
    strength = request.form.get('strength', '').strip()
    unit = request.form.get('unit', '').strip()
    reorder = request.form.get('reorder_level', '').strip()
    price = request.form.get('unit_price', '').strip()
    if not name or len(name) > 120:
        flash('Medicine name is required (max 120 chars)', 'danger')
        return redirect(back_url('admin.pharmacy'))
    try:
        reorder_n = int(reorder or 0)
    except (TypeError, ValueError):
        flash('Reorder level must be a whole number', 'danger')
        return redirect(back_url('admin.pharmacy'))
    unit_price = parse_money(price or 0)
    if reorder_n < 0 or reorder_n > 1000000 or unit_price is None:
        flash('Reorder level or price is out of range', 'danger')
        return redirect(back_url('admin.pharmacy'))
    conn = get_db_connection()
    try:
        cur = conn.execute('''
            UPDATE medicines SET name = ?, strength = ?, unit = ?, reorder_level = ?, unit_price_cents = ?
            WHERE id = ? AND hospital_id = ? AND archived=0
        ''', (name, strength[:60], unit[:30], reorder_n, to_minor(unit_price), medicine_id, session['hospital_id']))
        if cur.rowcount:
            log_audit('update', 'medicine', medicine_id, name, conn=conn)
            conn.commit()
            flash('Medicine updated', 'success')
        else:
            flash('Medicine not found', 'warning')
    except Exception as e:
        flash('Error updating medicine', 'danger')
        app.logger.error(f"Update medicine error: {str(e)}")
    finally:
        conn.close()
    return redirect(back_url('admin.pharmacy'))

@bp.route('/admin/medicines/<int:medicine_id>/adjust', methods=['POST'], permission="pharmacy")
def adjust_stock(medicine_id):
    try:
        delta = int(request.form.get('delta', '0').strip())
    except (TypeError, ValueError, AttributeError):
        flash('Adjustment must be a whole number', 'danger')
        return redirect(back_url('admin.pharmacy'))
    if abs(delta) > 100000 or delta == 0:
        flash('Adjustment is out of range', 'danger')
        return redirect(back_url('admin.pharmacy'))
    conn = get_db_connection()
    try:
        lock_record(conn, 'medicines', medicine_id)
        med = conn.execute('SELECT * FROM medicines WHERE id = ? AND hospital_id = ? AND archived=0',
                           (medicine_id, session['hospital_id'])).fetchone()
        if not med:
            flash('Medicine not found', 'warning')
            return redirect(back_url('admin.pharmacy'))
        new_qty = (med['stock_qty'] or 0) + delta
        if new_qty < 0:
            flash('Adjustment would take stock below zero', 'danger')
            return redirect(back_url('admin.pharmacy'))
        reason = request.form.get('reason', '').strip()
        if not reason or len(reason) > 500:
            flash('Enter a stock adjustment reason (up to 500 characters).', 'danger')
            return redirect(back_url('admin.pharmacy'))
        conn.execute('INSERT INTO stock_movements (medicine_id, delta, balance, reason, actor_id) VALUES (?, ?, ?, ?, ?)',
                     (medicine_id, delta, new_qty, reason, session['user_id']))
        conn.execute('UPDATE medicines SET stock_qty = ? WHERE id = ? AND hospital_id = ? AND archived=0',
                     (new_qty, medicine_id, session['hospital_id']))
        log_audit('adjust_stock', 'medicine', medicine_id, f"{'+' if delta > 0 else ''}{delta} -> {new_qty}", conn=conn)
        conn.commit()
        flash(f"Stock updated ({med['name']}: {new_qty})", 'success')
    except Exception as e:
        flash('Error adjusting stock', 'danger')
        app.logger.error(f"Adjust stock error: {str(e)}")
    finally:
        conn.close()
    return redirect(back_url('admin.pharmacy'))

@bp.route('/admin/medicines/<int:medicine_id>/delete', methods=['POST'], permission="pharmacy")
def delete_medicine(medicine_id):
    conn = get_db_connection()
    try:
        lock_record(conn, 'medicines', medicine_id)
        cur = conn.execute('UPDATE medicines SET archived=1 WHERE id = ? AND hospital_id = ? AND archived=0',
                           (medicine_id, session['hospital_id']))
        if cur.rowcount:
            log_audit('archive', 'medicine', medicine_id, conn=conn)
            conn.commit()
            flash('Medicine archived; stock history retained.', 'success')
        else:
            flash('Medicine not found', 'warning')
    except Exception as e:
        flash('Error removing medicine', 'danger')
        app.logger.error(f"Delete medicine error: {str(e)}")
    finally:
        conn.close()
    return redirect(back_url('admin.pharmacy'))

# ---------------- Bed & ward management ----------------
