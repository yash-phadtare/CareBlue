"""Presentation-only helpers. Stored hospital branding remains unchanged."""
import re
from urllib.parse import urlsplit

COLLECTION_ENDPOINTS={
    'admin.admin_dashboard':('audit','/admin/dashboard'),
    'admin.view_patients':('registry','/admin/view_patients'),
    'admin.view_doctors':('doctors','/admin/view_doctors'),
    'admin.view_appointments':('appointments','/admin/view_appointments'),
    'admin.billing':('billing','/admin/billing'),
    'admin.pharmacy':('pharmacy','/admin/pharmacy'),
    'admin.audit_log_view':('audit','/admin/audit_log'),
    'history.admissions':('beds','/admin/admissions'),
    'tenant.team':('users','/hospital/team'),
    'doctor.doctor_appointments':(None,'/doctor/appointments'),
    'doctor.all_patients_history':(None,'/doctor/patients'),
    'platform.dashboard':(None,'/platform'),
}


def return_context():
    """Only use permitted, same-origin collection routes as a return destination."""
    from flask import request,session
    from careblue.tenancy import can
    candidate=request.form.get('return_to') or request.args.get('return_to','')
    if not candidate or len(candidate)>2000 or any(c in candidate for c in ('\\','\r','\n')):
        return None
    try:
        parsed=urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    for endpoint,(permission,path) in COLLECTION_ENDPOINTS.items():
        if parsed.path!=path:
            continue
        role='admin' if permission else endpoint.split('.')[0]
        if session.get('user_type')!=role or (permission and not can(permission)):
            return None
        return parsed.path+('?' + parsed.query if parsed.query else '')
    return None


def back_url(endpoint):
    from flask import request,url_for
    context=return_context()
    if context:
        return context
    if endpoint==request.endpoint and endpoint in COLLECTION_ENDPOINTS:
        return request.full_path.rstrip('?')
    return url_for(endpoint)


def context_link(endpoint,**values):
    from flask import request,url_for
    context=return_context()
    if not context and request.endpoint in COLLECTION_ENDPOINTS:
        context=request.full_path.rstrip('?')
    if context:
        values.setdefault('return_to',context)
    return url_for(endpoint,**values)


def luminance(rgb):
    channels = [value / 255 for value in rgb]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def brand_palette(color):
    """Keep the brand hue while giving white control labels adequate contrast."""
    if not color or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        color = '#1677FF'
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    primary = rgb
    while 1.05 / (luminance(primary) + .05) < 5.5:
        primary = tuple(int(value * .94) for value in primary)
    encode = lambda values: '#' + ''.join(f'{value:02x}' for value in values)
    night_primary=tuple(round(255*.7+value*.3) for value in rgb)
    night_soft=tuple(round(base*.82+value*.18) for base,value in zip((48,42,56),rgb))
    return {'brand': color, 'primary': encode(primary),
            'dark': encode(tuple(int(value * .82) for value in primary)),
            'soft': encode(tuple(round(255 * .94 + value * .06) for value in rgb)),
            'night_primary':encode(night_primary),
            'night_dark':encode(tuple(round(255*.86+value*.14) for value in rgb)),
            'night_soft':encode(night_soft),
            'on_brand':'#ffffff' if 1.05/(luminance(rgb)+.05)>=4.5 else '#000000'}
