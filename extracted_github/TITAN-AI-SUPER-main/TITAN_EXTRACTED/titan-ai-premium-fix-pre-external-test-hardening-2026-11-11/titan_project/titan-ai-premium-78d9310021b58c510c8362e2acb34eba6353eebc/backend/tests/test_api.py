from __future__ import annotations


def test_primary_api_stack_is_importable_and_uses_starlette():
    from starlette.applications import Starlette
    from app.main import app, create_app, create_star_app

    assert isinstance(app, Starlette)
    assert create_app is create_star_app
    assert isinstance(create_app(), Starlette)
