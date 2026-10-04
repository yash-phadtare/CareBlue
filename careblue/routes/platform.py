from careblue.presentation import back_url,context_link
"""Platform operations expose hospital metadata, never clinical records."""
from datetime import datetime, timezone
from flask import abort, flash, render_template, request, redirect, url_for
from database import get_db_connection, lock_record
from careblue.routes.blueprints import AccessBlueprint
from careblue.tenancy import PLANS, STATUSES
from careblue.services import page_query
from careblue.security import log_audit

bp=AccessBlueprint('platform',__name__,roles=('platform',))


@bp.get('/platform')
def dashboard():
    conn=get_db_connection()
    search=request.args.get('search','').strip()[:120]
    rows=page_query(conn,'SELECT * FROM hospitals WHERE LOWER(name) LIKE ? OR slug LIKE ? ORDER BY id DESC',
                    ('%'+search.lower()+'%','%'+search.lower()+'%'))
    summary=conn.execute('SELECT status,COUNT(*) AS count FROM hospitals GROUP BY status').fetchall()
    return render_template('platform/dashboard.html',hospitals=rows,summary=summary,search=search)


@bp.route('/platform/hospitals/<int:hospital_id>',methods=['GET','POST'])
def hospital_detail(hospital_id):
    conn=get_db_connection()
    if request.method=='POST':
        lock_record(conn,'hospitals',hospital_id)
    hospital=conn.execute('SELECT * FROM hospitals WHERE id=?',(hospital_id,)).fetchone()
    if not hospital:
        abort(404)
    if request.method=='POST':
        try:
            if int(request.form.get('version','0'))!=hospital['settings_version']:
                abort(409,description='Workspace changed. Reload before saving.')
            status=request.form.get('status','')
            plan=request.form.get('plan','')
            reason=request.form.get('reason','').strip()
            end=request.form.get('trial_ends_at','').strip() or None
            if status not in STATUSES or plan not in PLANS or not reason or len(reason)>500:
                raise ValueError('Choose a status and plan, and give a reason of at most 500 characters.')
            if status=='Trial':
                if not end or datetime.strptime(end,'%Y-%m-%d').date()<datetime.now(timezone.utc).date():
                    raise ValueError('Trial end date must be today or later.')
            else:
                end=None
            conn.execute('UPDATE hospitals SET status=?,plan=?,trial_ends_at=?,settings_version=settings_version+1 WHERE id=?',(status,plan,end,hospital_id))
            detail = f"{hospital['plan']}/{hospital['status']} -> {plan}/{status}; trial ends {end or 'none'}. {reason}"
            log_audit('subscription_update','hospital',hospital_id,detail,hospital_id=hospital_id,conn=conn)
            conn.commit()
            flash('Workspace subscription and access updated.','success')
            return redirect(context_link('platform.hospital_detail',hospital_id=hospital_id))
        except ValueError as error:
            conn.rollback(); flash(str(error),'danger')
            return render_template('platform/hospital.html',workspace=hospital,plans=PLANS,statuses=STATUSES),422
    events=page_query(conn,"SELECT id,created_at,action,detail FROM audit_log WHERE hospital_id=? AND actor_type='platform' ORDER BY id DESC",(hospital_id,))
    return render_template('platform/hospital.html',workspace=hospital,plans=PLANS,statuses=STATUSES,events=events)
