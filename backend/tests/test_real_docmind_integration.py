import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from openpyxl import Workbook

from app.service.xlsx_service import parse_xlsx_with_docmind


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_DOCMIND_TEST") != "1",
    reason="Set RUN_REAL_DOCMIND_TEST=1 to run the real DocMind integration test.",
)


def test_real_docmind_parses_small_xlsx(tmp_path) -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if not os.getenv("DOCMIND_ACCESS_KEY_ID") or not os.getenv("DOCMIND_ACCESS_KEY_SECRET"):
        pytest.skip("DocMind access key is not configured.")

    file_path = tmp_path / "docmind_smoke.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["公司", "行业"])
    sheet.append(["贵州茅台", "白酒"])
    workbook.save(file_path)

    text = parse_xlsx_with_docmind(str(file_path), file_path.name)

    assert text.strip()
