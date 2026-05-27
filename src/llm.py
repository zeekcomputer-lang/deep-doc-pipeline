"""
순수 OpenAI SDK 클라이언트 (LangChain 래퍼 금지).
gpt-oss 등 OpenAI 호환 엔드포인트 지원.
"""
from __future__ import annotations
import os
from typing import Type, TypeVar
from openai import OpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def get_client() -> OpenAI:
    """OpenAI 호환 클라이언트 생성. base_url/api_key는 환경변수에서."""
    return OpenAI(
        base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("OPENAI_API_KEY", "ollama"),
    )


def get_model(role: str = "default") -> str:
    """노드별 모델 분리 지원. 미설정 시 MODEL_NAME 폴백."""
    env_key = {
        "extractor": "EXTRACTOR_MODEL",
        "judge": "JUDGE_MODEL",
        "writer": "WRITER_MODEL",
    }.get(role)
    if env_key and os.getenv(env_key):
        return os.getenv(env_key)
    return os.getenv("MODEL_NAME", "gpt-oss:20b")


def structured_call(
    messages: list,
    response_model: Type[T],
    role: str = "default",
    temperature: float = 0.0,
    max_retries: int = 3,
) -> T:
    """
    client.beta.chat.completions.parse + Pydantic 강제.
    파싱 실패 시 최대 3회 자체 재시도.

    Note:
        gpt-oss는 OpenAI Structured Outputs 호환 (response_format=json_schema).
        엔진이 미지원이면 fallback으로 일반 chat.completions + manual parse를 시도.
    """
    client = get_client()
    model = get_model(role)
    last_err = None

    for attempt in range(max_retries):
        try:
            # Primary: parse API (Structured Outputs)
            completion = client.beta.chat.completions.parse(
                model=model,
                messages=messages,
                response_format=response_model,
                temperature=temperature,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise ValueError("parsed is None")
            return parsed
        except Exception as e:
            last_err = e
            # Fallback: JSON mode + manual parse
            try:
                schema = response_model.model_json_schema()
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages + [{
                        "role": "system",
                        "content": (
                            "Respond ONLY with valid JSON matching this schema:\n"
                            f"{schema}\n"
                            "No prose, no markdown fences."
                        ),
                    }],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                raw = completion.choices[0].message.content
                return response_model.model_validate_json(raw)
            except Exception as e2:
                last_err = e2
                print(f"  [structured_call retry {attempt+1}/{max_retries}] {type(e2).__name__}: {e2}")
                continue

    raise RuntimeError(f"structured_call failed after {max_retries} attempts: {last_err}")
