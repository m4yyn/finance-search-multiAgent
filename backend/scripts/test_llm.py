import asyncio
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config.settings import get_settings  # noqa: E402
from app.service.llm_service import stream_chat_completion  # noqa: E402


async def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        raise RuntimeError("OPENAI_API_KEY is not configured in backend/.env")

    print("OpenAI streaming smoke:")
    chunks: list[str] = []
    async for delta in stream_chat_completion(
        [{"role": "user", "content": "请用一句中文回复：流式测试通过"}]
    ):
        chunks.append(delta)
        print(delta, end="", flush=True)
    print()

    if not chunks:
        raise AssertionError("No streaming chunks received from OpenAI.")
    print(f"OK: received {len(chunks)} streaming chunks.")


if __name__ == "__main__":
    asyncio.run(main())
