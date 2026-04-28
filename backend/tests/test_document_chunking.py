import pytest

from app.service.document_service import chunk_text, parse_document_to_chunks


def test_chunk_text_splits_long_pdf_text_with_overlap() -> None:
    text = " ".join(f"token{index}" for index in range(1200))

    chunks = chunk_text(text, max_tokens=100, overlap=20)

    assert len(chunks) > 1
    assert chunks[0]["source_type"] == "pdf"
    assert chunks[0]["chunk_index"] == 0
    assert chunks[1]["chunk_index"] == 1
    assert chunks[0]["content"] != chunks[1]["content"]


def test_chunk_text_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        chunk_text("   ")
    with pytest.raises(ValueError):
        chunk_text("valid", max_tokens=0)
    with pytest.raises(ValueError):
        chunk_text("valid", max_tokens=10, overlap=10)


def test_parse_document_to_chunks_rejects_unknown_extension(tmp_path) -> None:
    file_path = tmp_path / "demo.txt"
    file_path.write_text("hello", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported document type"):
        parse_document_to_chunks(str(file_path), "demo.txt")
