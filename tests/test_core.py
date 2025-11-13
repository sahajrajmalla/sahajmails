import pytest
from sahajmails.app import build_personalized_body, render_email_body

def test_placeholder_replacement():
    template = "Hello {{ name }}! Welcome to {{ company }}."
    row = {"name": "Alice", "company": "Acme"}
    columns = ["name", "company"]
    result = build_personalized_body(template, row, columns)
    assert result == "Hello Alice! Welcome to Acme."

def test_placeholder_case_insensitive():
    template = "Hi {{ FirstName }} and {{ COMPANY }}!"
    row = {"firstname": "Bob", "Company": "XYZ"}
    columns = ["firstname", "Company"]
    result = build_personalized_body(template, row, columns)
    assert result == "Hi Bob and XYZ!"

def test_render_email_body():
    body = "# Hello\nThis is **bold**."
    html = render_email_body(body)
    assert "<h1>Hello</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "background:#ffffff" in html