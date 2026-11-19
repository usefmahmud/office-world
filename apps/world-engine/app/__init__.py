"""World Engine application package (ticket WORLD-4 owner: yassin).

Kept import-light on purpose: submodules (``app.routers.*``) are mounted by the
engine entrypoint, so this file must not import FastAPI itself.
"""
