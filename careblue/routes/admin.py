from flask import render_template, request, session
from careblue.services import facility_now, page_query
from database import get_db_connection
from datetime import datetime, timedelta, timezone


from careblue.routes.admin_blueprint import bp

# Admin routes
@bp.route('/admin/dashboard')
def admin_dashboard():
    conn = get_db_connection()
    hospital_id=session['hospital_id']
    current=facility_now()
    today=current.date().isoformat()
    period='month' if request.args.get('flow')=='month' else 'week'
    days=30 if period=='month' else 7
    first=current.date()-timedelta(days=days-1)
    since=current.replace(year=first.year,month=first.month,day=first.day,hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    try:
        patients_count=conn.execute('SELECT COUNT(*) FROM patients WHERE hospital_id=?',(hospital_id,)).fetchone()[0]
        doctors_count=conn.execute('SELECT COUNT(*) FROM doctors WHERE hospital_id=? AND active=1',(hospital_id,)).fetchone()[0]
        care_team=conn.execute('SELECT id,name,specialization FROM doctors WHERE hospital_id=? AND active=1 ORDER BY name,id LIMIT 3',(hospital_id,)).fetchall()
        todays_appointments=conn.execute('''SELECT a.id,a.patient_id,p.name AS patient_name,d.name AS doctor_name,d.specialization,a.time_slot,a.status
            FROM appointments a JOIN patients p ON a.patient_id=p.id JOIN doctors d ON a.doctor_id=d.id
            WHERE a.hospital_id=? AND a.date=?
            ORDER BY CASE WHEN a.time_slot IS NULL THEN 0 ELSE 1 END,a.time_slot,a.id''',(hospital_id,today)).fetchall()
        wards=conn.execute('''SELECT w.id,w.name,COUNT(b.id) AS total,
            COALESCE(SUM(CASE WHEN b.status='Available' THEN 1 ELSE 0 END),0) AS available,
            COALESCE(SUM(CASE WHEN b.status='Occupied' THEN 1 ELSE 0 END),0) AS occupied
            FROM wards w LEFT JOIN beds b ON b.ward_id=w.id AND b.hospital_id=w.hospital_id AND b.archived=0
            WHERE w.hospital_id=? GROUP BY w.id,w.name ORDER BY w.name,w.id''',(hospital_id,)).fetchall()
        beds={key:sum(row[key] for row in wards) for key in ('total','available','occupied')}
        beds['occupancy']=round(100*beds['occupied']/beds['total']) if beds['total'] else 0
        operations={
            'low_stock':conn.execute('SELECT COUNT(*) FROM medicines WHERE hospital_id=? AND archived=0 AND stock_qty<=reorder_level',(hospital_id,)).fetchone()[0],
            'occupied_beds':beds['occupied'],
            'overdue_visits':conn.execute("SELECT COUNT(*) FROM appointments WHERE hospital_id=? AND status='Scheduled' AND date<?",(hospital_id,today)).fetchone()[0],
        }
        events=conn.execute('SELECT admitted_at,discharged_at FROM admissions WHERE hospital_id=? AND (admitted_at>=? OR discharged_at>=?)',(hospital_id,since,since)).fetchall()
    finally:
        conn.close()
    flow=[{'date':(first+timedelta(days=i)).isoformat(),'label':(first+timedelta(days=i)).strftime('%d %b' if days==30 else '%a'),'admissions':0,'discharges':0} for i in range(days)]
    dates={day['date']:day for day in flow}
    for event in events:
        for field,key in [('admitted_at','admissions'),('discharged_at','discharges')]:
            if event[field]:
                moment=datetime.fromisoformat(str(event[field]))
                if moment.tzinfo is None:moment=moment.replace(tzinfo=timezone.utc)
                day=moment.astimezone(current.tzinfo).date().isoformat()
                if day in dates:dates[day][key]+=1
    ceiling=max(1,max(max(day['admissions'],day['discharges']) for day in flow))
    points={key:' '.join(f'{25+i*650/(days-1):.1f},{145-day[key]*115/ceiling:.1f}' for i,day in enumerate(flow)) for key in ('admissions','discharges')}
    status=request.args.get('schedule','Scheduled')
    if status not in ('all','Scheduled','Completed','Cancelled'):status='Scheduled'
    visible=[visit for visit in todays_appointments if status=='all' or visit['status']==status]
    counts={state:sum(visit['status']==state for visit in todays_appointments) for state in ('Scheduled','Completed','Cancelled')}
    return render_template('admin/dashboard.html',patients_count=patients_count,doctors_count=doctors_count,care_team=care_team,
        todays_appointments=todays_appointments,schedule=visible[:5],schedule_total=len(visible),schedule_status=status,counts=counts,
        wards=wards,beds=beds,operations=operations,today=today,flow=flow,flow_period=period,flow_points=points,flow_ceiling=ceiling,
        flow_totals={key:sum(day[key] for day in flow) for key in ('admissions','discharges')})

@bp.route('/admin/audit_log')
def audit_log_view():

    action_filter = request.args.get('action', '').strip()
    entity_filter = request.args.get('entity', '').strip()

    conn = get_db_connection()
    try:
        query = '''
            SELECT a.*, COALESCE(s.name, d.name) AS actor_name
            FROM audit_log a
            LEFT JOIN staff s ON a.actor_id = s.id AND a.actor_type = 'admin'
            LEFT JOIN doctors d ON a.actor_id = d.id AND a.actor_type = 'doctor'
            WHERE a.hospital_id = ?
        '''
        params = [session['hospital_id']]
        if action_filter:
            query += ' AND a.action = ?'
            params.append(action_filter)
        if entity_filter:
            query += ' AND a.entity = ?'
            params.append(entity_filter)
        query += ' ORDER BY a.id DESC'
        entries = page_query(conn, query, params)
        actions = [r['action'] for r in conn.execute(
            'SELECT DISTINCT action FROM audit_log WHERE hospital_id = ? ORDER BY action',
            (session['hospital_id'],)).fetchall()]
        entities = [r['entity'] for r in conn.execute(
            'SELECT DISTINCT entity FROM audit_log WHERE hospital_id = ? ORDER BY entity',
            (session['hospital_id'],)).fetchall()]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

    return render_template('admin/audit_log.html',
                           entries=entries, actions=actions, entities=entities,
                           action_filter=action_filter, entity_filter=entity_filter)


# Attach domain routes to one administrative authorization boundary.

