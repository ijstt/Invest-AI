"""Клиент тяжёлого LLM для суммаризации и синтеза.

Поддерживает два провайдера (выбор через GEO_LLM_PROVIDER):
- "local" — Ollama по HTTP (без доп. пакета, через httpx);
- "cloud" — OpenAI-совместимый chat-эндпоинт (DeepSeek и т.п.) как fallback.

Всё устойчиво к недоступности: при ошибке возвращаем None, а вызывающий код
деградирует до правил (например, простой список заголовков вместо связной сводки).
"""

from __future__ import annotations

import httpx

from config.settings import get_settings
from geoanalytics.core.logging import get_logger

log = get_logger("nlp.llm")


def _generate_ollama(
    prompt: str,
    *,
    timeout: float,
    model: str | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
    keep_alive: str | None = None,
    repeat_penalty: float | None = None,
    stop: list[str] | None = None,
    temperature: float | None = None,
    system: str | None = None,
) -> str | None:
    s = get_settings()
    options = {
        # num_ctx критичен: дефолт Ollama 2048 молча обрежет длинный
        # RAG-промпт (контекст актива + новости + индикаторы + факторы).
        "num_ctx": num_ctx or s.llm_num_ctx,
        # Потолок генерации: на CPU (~5 ток/с) без него ответ «растекается»
        # и упирается в таймаут.
        "num_predict": num_predict or s.llm_num_predict,
        # temperature: для аналитики нужна низкая (дефолт Ollama ~0.7 даёт галлюцинации
        # и языковые срывы — рус+кит у маленьких Qwen). None → берём из настроек.
        "temperature": temperature if temperature is not None else s.llm_temperature,
    }
    # repeat_penalty/stop — лечат склонность лёгкой модели зацикливаться и дописывать
    # служебный хвост («Ответ:»/повтор промпта); задаются вызывающим (ask-нарратив).
    if repeat_penalty is not None:
        options["repeat_penalty"] = repeat_penalty
    if stop:
        options["stop"] = stop
    # /api/chat (не /api/generate): system-роль резко повышает дисциплину Qwen по языку
    # и формату — главный рычаг против срыва рус→кит на длинной генерации.
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = httpx.post(
            f"{s.ollama_host}/api/chat",
            json={
                "model": model or s.llm_model,
                "messages": messages,
                "stream": False,
                # Держим модель в RAM между вызовами: иначе каждый платит за загрузку.
                "keep_alive": keep_alive or s.llm_keep_alive,
                "options": options,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return (resp.json().get("message", {}).get("content", "") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — LLM опционален
        log.warning("ollama_unavailable", host=s.ollama_host, error=str(exc))
        return None


def _generate_cloud(
    prompt: str,
    *,
    timeout: float,
    model: str | None = None,
    # num_ctx/num_predict/keep_alive/repeat_penalty — особенности Ollama, в облачном API
    # не применимы; stop и temperature поддерживаются OpenAI-совместимым API.
    num_ctx: int | None = None,
    num_predict: int | None = None,
    keep_alive: str | None = None,
    repeat_penalty: float | None = None,
    stop: list[str] | None = None,
    temperature: float | None = None,
    system: str | None = None,
) -> str | None:
    s = get_settings()
    if not s.cloud_api_key or not s.cloud_base_url:
        log.warning("cloud_llm_not_configured")
        return None
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model or s.llm_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else s.llm_temperature,
        }
        if stop:
            payload["stop"] = stop
        resp = httpx.post(
            f"{s.cloud_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {s.cloud_api_key}"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip() or None
    except Exception as exc:  # noqa: BLE001
        log.warning("cloud_llm_failed", error=str(exc))
        return None


def generate(
    prompt: str,
    *,
    timeout: float | None = None,
    model: str | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
    keep_alive: str | None = None,
    repeat_penalty: float | None = None,
    stop: list[str] | None = None,
    temperature: float | None = None,
    system: str | None = None,
) -> str | None:
    """Генерация текста выбранным провайдером. None при недоступности.

    Оверрайды (`model`/`num_ctx`/`num_predict`/`keep_alive`) — для лёгких путей вроде
    ask-роутера, которым на слабом железе нужна маленькая модель с коротким контекстом
    и keep_alive (экономия RAM); `repeat_penalty`/`stop` гасят зацикливание; `temperature`
    низкая для аналитики; `system` — системная роль (chat API) для дисциплины языка/формата.
    None → значение из настроек.
    """
    s = get_settings()
    if timeout is None:
        timeout = s.llm_timeout
    impl = _generate_cloud if s.llm_provider == "cloud" else _generate_ollama
    return impl(prompt, timeout=timeout, model=model, num_ctx=num_ctx,
                num_predict=num_predict, keep_alive=keep_alive,
                repeat_penalty=repeat_penalty, stop=stop, temperature=temperature,
                system=system)


def is_available() -> bool:
    """Доступен ли LLM сейчас (быстрая проверка без генерации)."""
    s = get_settings()
    if s.llm_provider == "cloud":
        return bool(s.cloud_api_key and s.cloud_base_url)
    try:
        httpx.get(f"{s.ollama_host}/api/tags", timeout=3.0).raise_for_status()
        return True
    except Exception:  # noqa: BLE001
        return False
