from careblue.routes.blueprints import AccessBlueprint

bp = AccessBlueprint("admin", __name__, roles=("admin",), permission="audit")
