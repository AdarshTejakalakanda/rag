"""Tests for RequirementParser."""

import pytest
from src.parsers.requirement_parser import RequirementParser

SAMPLE_DOC_TEXT = """# Authentication Module

## REQ-001: Secure Login Flow
Users must be able to log in with their credentials.

Acceptance Criteria:
- Must accept valid email and password
- Display error on invalid password
- Lock out after 5 consecutive failures

## REQ-002: Password Reset
Users can reset password via email OTP.

Acceptance Criteria:
1. Send 6 digit token
2. Token expires after 10 minutes
"""


def test_parse_requirements():
    reqs = RequirementParser.parse_markdown_or_text(SAMPLE_DOC_TEXT, source_file="docs/auth.md")
    assert len(reqs) == 2

    r1 = reqs[0]
    assert r1.req_id == "REQ-001"
    assert "Secure Login Flow" in r1.title
    assert r1.category == "Authentication Module"
    assert len(r1.acceptance_criteria) == 3
    assert r1.source_file == "docs/auth.md"

    r2 = reqs[1]
    assert r2.req_id == "REQ-002"
    assert len(r2.acceptance_criteria) == 2
