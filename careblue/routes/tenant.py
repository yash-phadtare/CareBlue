from careblue.presentation import back_url,context_link
"""Hospital-owned settings and team administration."""
from pathlib import Path
from flask import g, abort, current_app, flash, redirect, render_template, request, session, url_for, send_file
from werkzeug.security import generate_password_hash
from database import get_db_connection, IntegrityError, lock_record
from careblue.routes.blueprints import AccessBlueprint
from careblue.tenancy import validated_settings, STAFF_ROLES, require_last_admin
from careblue.accounts import email_valid, password_error
from careblue.security import log_audit
from careblue.services import page_query

bp = AccessBlueprint('tenant', __name__, roles=('admin',), permission='settings')


@bp.route('/hospital/settings', methods=['GET','POST'])
def settings():
    conn = get_db_connection()
    if request.method == 'POST':
        try:
            values = validated_settings(request.form)
            lock_record(conn,'hospitals',g.hospital['id'])
            current = conn.execute('SELECT * FROM hospitals WHERE id=?',(g.hospital['id'],)).fetchone()
            if int(request.form.get('version','0')) != current['settings_version']:
                return render_template('errors/http.html',status=409,message='Settings changed in another session. Reload before saving.'),409
            # Existing bookings retain their timezone semantics.
            if values['timezone'] != current['timezone'] and conn.execute("SELECT id FROM appointments WHERE hospital_id=? AND status='Scheduled' LIMIT 1",(current['id'],)).fetchone():
                raise ValueError('Reschedule or cancel scheduled visits before changing the timezone.')
            logo = request.files.get('logo')
            if logo and logo.filename:
                from careblue.storage import save_hospital_logo
                path = save_hospital_logo(logo, current['id'])
                if not path:
                    raise ValueError('Choose a valid JPG or PNG logo of at most 5 MB.')
                values['logo_path'] = path
            elif request.form.get('remove_logo'):
                values['logo_path'] = None
            columns = ','.join(key+'=?' for key in values)
            conn.execute(f'UPDATE hospitals SET {columns},settings_version=settings_version+1 WHERE id=?',(*values.values(),current['id']))
            log_audit('settings','hospital',current['id'],conn=conn)
            conn.commit()
            flash('Hospital settings saved.','success')
            return redirect(url_for('tenant.settings'))
        except (ValueError,TypeError) as error:
            conn.rollback()
            flash(str(error),'danger')
            return render_template('tenant/settings.html',values=request.form),422
    return render_template('tenant/settings.html',values=g.hospital)


@bp.route('/hospital/team',methods=['GET','POST'],permission='users')
def team():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        role = request.form.get('role','')
        try:
            if not name or len(name)>120 or not email_valid(email) or role not in STAFF_ROLES:
                raise ValueError('Enter a valid name, email and staff role.')
            error=password_error(password)
            if error:
                raise ValueError(error)
            actor = conn.insert('INSERT INTO staff(name,email,password,hospital_id,role) VALUES(?,?,?,?,?)',
                                (name,email,generate_password_hash(password),g.hospital['id'],role)).lastrowid
            log_audit('create_user','staff',actor,role,conn=conn)
            conn.commit()
            flash('Staff account created. Share the initial password securely.','success')
        except IntegrityError:
            conn.rollback(); flash('This email already belongs to a member of your hospital.','danger')
        except ValueError as error:
            conn.rollback(); flash(str(error),'danger')
        return redirect(back_url('tenant.team'))
    search=request.args.get('search','').strip()[:120]
    pattern='%'+search.lower()+'%'
    rows=page_query(conn,'SELECT id,name,email,role,active FROM staff WHERE hospital_id=? AND (LOWER(name) LIKE ? OR LOWER(email) LIKE ?) ORDER BY id',(g.hospital['id'],pattern,pattern))
    doctors=page_query(conn,"SELECT id,name,username,active FROM doctors WHERE hospital_id=? AND (LOWER(name) LIKE ? OR LOWER(COALESCE(username,'')) LIKE ?) ORDER BY name,id",(g.hospital['id'],pattern,pattern),key='doctors_page')
    return render_template('tenant/team.html',members=rows,doctors=doctors)


@bp.post('/hospital/team/<int:actor_id>',permission='users')
def update_user(actor_id):
    role=request.form.get('role','')
    active=1 if request.form.get('active') else 0
    conn=get_db_connection()
    try:
        current=conn.execute('SELECT role,active FROM staff WHERE id=? AND hospital_id=?',(actor_id,g.hospital['id'])).fetchone()
        if not current:
            abort(404)
        if role not in STAFF_ROLES:
            raise ValueError('Choose a valid staff role.')
        require_last_admin(conn,g.hospital['id'],actor_id,role,active)
        password=request.form.get('password','')
        if password and password_error(password):
            raise ValueError(password_error(password))
        if current['role']==role and current['active']==active and not password:
            flash('No access changes to save.','info')
            return redirect(back_url('tenant.team'))
        conn.execute('UPDATE staff SET role=?,active=?,session_version=session_version+1 WHERE id=? AND hospital_id=?',
                     (role,active,actor_id,g.hospital['id']))
        if password:
            conn.execute('UPDATE staff SET password=? WHERE id=?',(generate_password_hash(password),actor_id))
        log_audit('update_user','staff',actor_id,f'role={role} active={active}',conn=conn)
        conn.commit()
        flash('Staff access updated; previous sessions revoked.','success')
    except ValueError as error:
        conn.rollback(); flash(str(error),'danger')
    return redirect(back_url('tenant.team'))


@bp.post('/hospital/doctors/<int:doctor_id>/access',permission='users')
def doctor_access(doctor_id):
    conn=get_db_connection()
    lock_record(conn,'doctors',doctor_id)
    current=conn.execute('SELECT active FROM doctors WHERE id=? AND hospital_id=?',(doctor_id,g.hospital['id'])).fetchone()
    if not current:
        abort(404)
    active=1 if request.form.get('active') else 0
    if current['active']==active:
        flash('No access changes to save.','info')
        return redirect(back_url('tenant.team'))
    cur=conn.execute('UPDATE doctors SET active=?,session_version=session_version+1 WHERE id=? AND hospital_id=?',
                     (active,doctor_id,g.hospital['id']))
    if cur.rowcount!=1:
        abort(404)
    log_audit('update_access','doctor',doctor_id,conn=conn)
    conn.commit()
    flash('Doctor access updated.','success')
    return redirect(back_url('tenant.team'))


def asset_response(path):
    if path.startswith('s3:'):
        from careblue.storage import s3_client
        return redirect(s3_client().generate_presigned_url('get_object',Params={'Bucket':current_app.config['S3_BUCKET'],'Key':path[3:]},ExpiresIn=120))
    if path.startswith('private:'):
        root=Path(current_app.config['UPLOAD_FOLDER']).resolve()
        target=(root/path[8:]).resolve()
    else:
        root=Path(current_app.static_folder).resolve()
        target=(root/path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        abort(404)
    return send_file(target)


@bp.get('/hospital/assets/<path:path>',permission=None)
def asset(path):
    conn=get_db_connection()
    hospital_id=session['hospital_id']
    own=conn.execute('SELECT id FROM doctors WHERE hospital_id=? AND image_path=?',(hospital_id,path)).fetchone()
    logo=g.hospital['logo_path']==path
    if not own and not logo:
        abort(404)
    return asset_response(path)


@bp.get('/h/<slug>/logo',permission=None)
def public_logo(slug):
    hospital=get_db_connection().execute('SELECT logo_path FROM hospitals WHERE slug=?',(slug,)).fetchone()
    if not hospital or not hospital['logo_path']:
        abort(404)
    return asset_response(hospital['logo_path'])

# Logos are intentionally public branding; clinical and staff images require a tenant session.
public_logo.public=True
asset.allowed_roles=frozenset({'admin','doctor'})
