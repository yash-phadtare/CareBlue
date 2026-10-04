"""Blueprints declare access requirements independently of endpoint names."""
from flask import Blueprint


class AccessBlueprint(Blueprint):
    def __init__(self, *args, roles=(), public=False, permission=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.roles = frozenset(roles)
        self.public = public
        self.permission = permission

    def add_url_rule(self, rule, endpoint=None, view_func=None, **options):
        permission = options.pop("permission", self.permission)
        if view_func is not None:
            view_func.allowed_roles = getattr(view_func, "allowed_roles", self.roles)
            view_func.public = getattr(view_func, "public", self.public)
            view_func.permission = permission
        return super().add_url_rule(rule, endpoint, view_func, **options)


def access(*roles, public=False):
    def decorate(view):
        view.allowed_roles = frozenset(roles)
        view.public = public
        return view
    return decorate

