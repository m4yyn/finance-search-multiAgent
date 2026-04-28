from openpyxl import Workbook

from app.service import xlsx_service
from app.service.xlsx_service import (
    MAX_EXCEL_CHUNK_TOKENS,
    chunk_excel_text,
    parse_xlsx,
    parse_xlsx_to_chunks,
)


def create_workbook(path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "资产负债表"
    sheet.append(["项目", "金额"])
    for index in range(1, 36):
        sheet.append([f"科目{index}", index])
    empty = workbook.create_sheet("空表")
    empty.append([None, None])
    workbook.save(path)


def test_parse_xlsx_fallback_reads_sheets_and_chunks_rows(tmp_path) -> None:
    file_path = tmp_path / "demo.xlsx"
    create_workbook(file_path)

    chunks = parse_xlsx(str(file_path))

    assert len(chunks) == 2
    assert chunks[0]["sheet_name"] == "资产负债表"
    assert chunks[0]["row_start"] == 2
    assert chunks[0]["row_end"] == 31
    assert "表头: 项目 | 金额" in chunks[0]["content"]
    assert "Row 2: 科目1 | 1" in chunks[0]["content"]
    assert chunks[1]["row_start"] == 32
    assert "空表" not in "\n".join(chunk["content"] for chunk in chunks)


def test_parse_xlsx_to_chunks_falls_back_when_docmind_fails(monkeypatch, tmp_path) -> None:
    file_path = tmp_path / "demo.xlsx"
    create_workbook(file_path)
    monkeypatch.setattr(
        xlsx_service,
        "parse_xlsx_with_docmind",
        lambda *_: (_ for _ in ()).throw(RuntimeError("DocMind down")),
    )

    chunks = parse_xlsx_to_chunks(str(file_path), "demo.xlsx")

    assert chunks
    assert chunks[0]["source_type"] == "xlsx"


def test_parse_xlsx_to_chunks_uses_docmind_when_available(monkeypatch, tmp_path) -> None:
    file_path = tmp_path / "demo.xlsx"
    create_workbook(file_path)
    monkeypatch.setattr(
        xlsx_service,
        "parse_xlsx_with_docmind",
        lambda *_: "\n".join(f"row {index}" for index in range(35)),
    )

    chunks = parse_xlsx_to_chunks(str(file_path), "demo.xlsx")

    assert len(chunks) == 2
    assert chunks[0]["source_type"] == "xlsx_docmind"
    assert chunks[0]["row_start"] == 1
    assert chunks[0]["row_end"] == 30


def test_excel_text_chunks_are_token_bounded() -> None:
    chunks = chunk_excel_text("贵州茅台 " * 1200, source_type="xlsx_docmind")
    encoding = xlsx_service._encoding()

    assert len(chunks) > 1
    assert all(
        len(encoding.encode(chunk["content"])) <= MAX_EXCEL_CHUNK_TOKENS
        for chunk in chunks
    )
