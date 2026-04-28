from types import SimpleNamespace

import pytest

from app.service import docmind_service
from app.service.docmind_service import DocMindService, parse_pdf


class FakeDocMindClient:
    def __init__(self) -> None:
        self.submitted = []
        self.statuses = []
        self.results = []

    def submit_doc_parser_job_advance(self, request, runtime):
        self.submitted.append((request, runtime))
        return SimpleNamespace(body={"Data": {"Id": "task-123"}})

    def query_doc_parser_status(self, request):
        self.statuses.append(request.id)
        return SimpleNamespace(body={"Data": {"Status": "success"}})

    def get_doc_parser_result(self, request):
        self.results.append((request.layout_num, request.layout_step_size))
        if request.layout_num == 0:
            return SimpleNamespace(
                body={
                    "Data": {
                        "Layouts": [
                            {"markdownContent": "# 标题"},
                            {"text": "正文"},
                        ]
                    }
                }
            )
        return SimpleNamespace(body={"Data": {"Layouts": []}})


def build_service(monkeypatch, client: FakeDocMindClient) -> DocMindService:
    monkeypatch.setenv("DOCMIND_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("DOCMIND_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setattr(DocMindService, "_create_client", lambda self: client)
    return DocMindService()


def test_docmind_service_submit_status_and_collect(monkeypatch, tmp_path) -> None:
    client = FakeDocMindClient()
    service = build_service(monkeypatch, client)
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.4")

    task_id = service.submit_job(file_path, "demo.pdf")
    status = service.query_status(task_id)
    text = service.collect_all_results(task_id)

    assert task_id == "task-123"
    assert status["Data"]["Status"] == "success"
    assert client.submitted[0][0].file_name == "demo.pdf"
    assert client.submitted[0][0].file_name_extension == "pdf"
    assert client.submitted[0][0].llm_enhancement is False
    assert "# 标题" in text
    assert "正文" in text


def test_docmind_service_wait_for_completion(monkeypatch) -> None:
    service = build_service(monkeypatch, FakeDocMindClient())
    responses = [
        {"Data": {"Status": "processing"}},
        {"Data": {"Status": "success"}},
    ]
    monkeypatch.setattr(service, "query_status", lambda _: responses.pop(0))
    monkeypatch.setattr(docmind_service.time, "sleep", lambda _: None)

    assert service.wait_for_completion("task-123", poll_interval=0, max_wait=10)


def test_docmind_service_wait_failed_and_timeout(monkeypatch) -> None:
    service = build_service(monkeypatch, FakeDocMindClient())
    monkeypatch.setattr(service, "query_status", lambda _: {"Data": {"Status": "failed"}})
    assert not service.wait_for_completion("task-123", poll_interval=0, max_wait=10)

    monkeypatch.setattr(service, "query_status", lambda _: {"Data": {"Status": "processing"}})
    assert not service.wait_for_completion("task-123", poll_interval=0, max_wait=-1)


def test_parse_pdf_raises_for_empty_result(monkeypatch, tmp_path) -> None:
    class EmptyService:
        def submit_job(self, file_path, file_name):
            return "task-123"

        def wait_for_completion(self, task_id):
            return True

        def collect_all_results(self, task_id):
            return "   "

    monkeypatch.setattr(docmind_service, "DocMindService", EmptyService)
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.4")

    with pytest.raises(RuntimeError, match="解析结果为空"):
        parse_pdf(str(file_path), "demo.pdf")
