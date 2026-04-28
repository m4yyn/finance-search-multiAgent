import os
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from alibabacloud_docmind_api20220711.client import Client as DocMindClient
from alibabacloud_docmind_api20220711 import models as docmind_models
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models


SUCCESS_STATUSES = {"success", "succeeded", "finished", "completed"}
FAILED_STATUSES = {"failed", "fail", "error", "canceled", "cancelled"}


def _model_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_map"):
        return value.to_map()
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }


def _case_insensitive_get(data: dict[str, Any], *keys: str) -> Any:
    lowered = {key.lower(): value for key, value in data.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _extract_body(response: Any) -> dict[str, Any]:
    return _model_to_dict(getattr(response, "body", response))


def _extract_data(response_or_body: Any) -> Any:
    body = _extract_body(response_or_body)
    return _case_insensitive_get(body, "data") or {}


def _extract_task_id(response: Any) -> str | None:
    data = _extract_data(response)
    if isinstance(data, dict):
        value = _case_insensitive_get(data, "id", "task_id", "taskId")
        return str(value) if value else None
    value = getattr(data, "id", None)
    return str(value) if value else None


def _extract_status(status_response: dict[str, Any]) -> str | None:
    data = _case_insensitive_get(status_response, "data") or status_response
    if isinstance(data, dict):
        status = _case_insensitive_get(data, "status")
        return str(status).lower() if status else None
    status = getattr(data, "status", None)
    return str(status).lower() if status else None


def _extract_layout_items(data: Any) -> list[Any]:
    if isinstance(data, dict):
        for key in ["layouts", "Layouts", "layout", "Layout", "results", "Results"]:
            value = data.get(key)
            if isinstance(value, list):
                return value
        return []
    if isinstance(data, list):
        return data
    return []


def _collect_text_parts(value: Any) -> list[str]:
    parts: list[str] = []
    preferred_keys = [
        "markdownContent",
        "markdown_content",
        "markdown",
        "text",
        "content",
        "html",
        "llmResult",
        "llm_result",
    ]
    if isinstance(value, dict):
        for key in preferred_keys:
            item = _case_insensitive_get(value, key)
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
        if parts:
            return parts
        for item in value.values():
            parts.extend(_collect_text_parts(item))
    elif isinstance(value, list):
        for item in value:
            parts.extend(_collect_text_parts(item))
    elif isinstance(value, str) and value.strip():
        parts.append(value.strip())
    return parts


class DocMindService:
    def __init__(self):
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        self.access_key_id = os.getenv("DOCMIND_ACCESS_KEY_ID")
        self.access_key_secret = os.getenv("DOCMIND_ACCESS_KEY_SECRET")
        self.endpoint = "docmind-api.cn-hangzhou.aliyuncs.com"
        self.client = self._create_client()

    def _create_client(self) -> DocMindClient:
        if not self.access_key_id or not self.access_key_secret:
            raise RuntimeError("DocMind access key is not configured.")
        config = open_api_models.Config(
            access_key_id=self.access_key_id,
            access_key_secret=self.access_key_secret,
            endpoint=self.endpoint,
        )
        return DocMindClient(config)

    def submit_job(self, file_path, file_name) -> Optional[str]:
        """提交解析任务，返回 task_id"""
        path = Path(file_path)
        suffix = Path(file_name).suffix.lstrip(".") or path.suffix.lstrip(".")
        with path.open("rb") as file_object:
            request = docmind_models.SubmitDocParserJobAdvanceRequest(
                file_name=file_name,
                file_name_extension=suffix,
                file_url_object=file_object,
                llm_enhancement=False,
            )
            response = self.client.submit_doc_parser_job_advance(
                request,
                util_models.RuntimeOptions(),
            )
        return _extract_task_id(response)

    def query_status(self, task_id) -> Optional[dict]:
        """查询任务状态"""
        if not task_id:
            return None
        request = docmind_models.QueryDocParserStatusRequest(id=task_id)
        response = self.client.query_doc_parser_status(request)
        return _extract_body(response)

    def wait_for_completion(
        self,
        task_id,
        poll_interval=5,
        max_wait=300,
    ) -> bool:
        """轮询直到完成"""
        deadline = time.monotonic() + max_wait
        while time.monotonic() <= deadline:
            status_response = self.query_status(task_id)
            if not status_response:
                return False
            status = _extract_status(status_response)
            if status in SUCCESS_STATUSES:
                return True
            if status in FAILED_STATUSES:
                return False
            time.sleep(poll_interval)
        return False

    def get_result(self, task_id, layout_num=0, layout_step_size=10):
        """分页拉取解析结果"""
        request = docmind_models.GetDocParserResultRequest(
            id=task_id,
            layout_num=layout_num,
            layout_step_size=layout_step_size,
        )
        response = self.client.get_doc_parser_result(request)
        return _extract_body(response)

    def collect_all_results(self, task_id) -> str:
        """聚合所有 layout 拼成完整 markdown"""
        layout_num = 0
        layout_step_size = 10
        all_parts: list[str] = []
        for _ in range(1000):
            result = self.get_result(task_id, layout_num, layout_step_size)
            data = _case_insensitive_get(result, "data") or result
            layouts = _extract_layout_items(data)
            page_source = layouts if layouts else data
            parts = _collect_text_parts(page_source)
            if not parts:
                break
            all_parts.extend(parts)
            if len(layouts) < layout_step_size:
                break
            layout_num += layout_step_size
        return "\n\n".join(part for part in all_parts if part.strip())


def parse_pdf(file_path: str, file_name: str) -> str:
    """对外的简化函数：完整流程，返回提取的全文文本"""
    service = DocMindService()
    task_id = service.submit_job(file_path, file_name)
    if not task_id:
        raise RuntimeError("DocMind 任务提交失败")
    if not service.wait_for_completion(task_id):
        raise RuntimeError("DocMind 解析超时或失败")
    text = service.collect_all_results(task_id)
    if not text.strip():
        raise RuntimeError("解析结果为空")
    return text
