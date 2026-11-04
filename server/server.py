"""집콕체크 AI 서버 — '들은 말로 빠르게 채우기'의 정확도를 높여요.

브라우저(jipkok.html)가 들은 말과 '채울 수 있는 칸 목록'을 보내면,
Gemini가 말에 나온 답만 골라 칸 이름(key)과 값(value)으로 돌려줘요.
API 키는 서버의 환경 변수(GEMINI_API_KEY)에만 두고, 코드나 HTML에는 절대 넣지 않아요.

로컬 실행:
  pip install -r requirements.txt
  export GEMINI_API_KEY=...            (Windows PowerShell: $env:GEMINI_API_KEY="...")
  uvicorn server:app --port 8000

환경 변수:
  GEMINI_API_KEY   (필수) Google AI Studio에서 받은 키
  GEMINI_MODEL     (선택) 기본값 gemini-flash-latest
  ALLOWED_ORIGINS  (선택) 앱 주소, 쉼표로 여러 개. 예: https://내아이디.github.io  (기본값 * = 모두 허용)
"""

import json
import logging
import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
log = logging.getLogger("jipkok")

app = FastAPI(title="집콕체크 AI 서버")
# 로컬에서 HTML 파일을 더블클릭해서 열면(file://) Origin이 "null"이에요. 그래서 기본값은 모두 허용이고,
# GitHub Pages에 올리면 ALLOWED_ORIGINS에 그 주소만 넣으세요.
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["POST"], allow_headers=["Content-Type"])

client = genai.Client()  # GEMINI_API_KEY 환경 변수를 읽어요

# 공개 주소라 누구나 부를 수 있어요. 무료 한도를 지키려고 IP마다 1분에 10번까지만 받아요.
RATE_LIMIT, RATE_WINDOW = 10, 60
hits: dict[str, deque] = defaultdict(deque)


def too_many(ip: str) -> bool:
    now, q = time.monotonic(), hits[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return True
    q.append(now)
    return False


class Option(BaseModel):
    v: str
    label: str


class FieldSpec(BaseModel):
    key: str = Field(max_length=40)             # 예: "q5.has", "ctx.deposit"
    question: str = Field(max_length=200)        # 질문 문장
    label: str = Field(max_length=120)           # 칸 이름
    money: bool = False                          # 금액 칸이면 True (만 원 단위)
    options: list[Option] = Field(default_factory=list, max_length=8)


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    fields: list[FieldSpec] = Field(min_length=1, max_length=80)


SYSTEM = """너는 서울에서 전월세 계약을 앞둔 대학생을 돕는 체크리스트 앱의 입력 도우미야.
사용자가 공인중개사나 집주인에게 들은 말(음성 인식 결과라 띄어쓰기·맞춤법이 틀릴 수 있어)을 보내면,
아래 '채울 수 있는 칸' 중 그 말에 실제로 나온 내용만 골라 답을 채워.

규칙:
- 말에 근거가 있는 칸만 채워. 추측하거나 일반 상식으로 채우지 마.
- 선택지 칸은 value에 선택지의 v 값을 그대로 넣어. 딱 맞는 선택지가 없으면 그 칸은 빼.
- 금액 칸은 value에 '만 원' 단위 정수를 문자열로 넣어. 예: "1억 2천" → "12000", "오천만 원" → "5000", "월세 오십" → "50".
- "보증금 천에 오십"처럼 월세 계약 관용 표현은 보증금 1000만 원, 월세 50만 원으로 읽어.
- 들은 말을 그대로 옮겼다면 '말로만 들었어요' 같은 확인 방법 칸은 그렇게 채워도 돼. 서류를 직접 봤다는 말이 있을 때만 서류 쪽 선택지를 골라.
- quote에는 근거가 된 원문 구절을 그대로 짧게 옮겨.
- 이미 돈을 보냈는데 연락이 끊겼다거나 사기를 당한 것 같다는 내용이 있으면 urgent를 true로.
- 법률 판단이나 조언은 하지 마. 칸을 채우는 일만 해."""


def build_schema(keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": keys},
                        "value": {"type": "string"},
                        "quote": {"type": "string"},
                    },
                    "required": ["key", "value", "quote"],
                },
            },
            "urgent": {"type": "boolean"},
        },
        "required": ["answers", "urgent"],
    }


def describe(fields: list[FieldSpec]) -> str:
    lines = []
    for f in fields:
        kind = "금액(만 원)" if f.money else " / ".join(f'{o.v}="{o.label}"' for o in f.options)
        lines.append(f"- {f.key} | {f.question} | {f.label} | {kind}")
    return "\n".join(lines)


def clean(answers: list[dict], fields: list[FieldSpec]) -> list[dict]:
    """모델이 돌려준 값이 칸에 실제로 들어갈 수 있는지 한 번 더 확인해요."""
    spec = {f.key: f for f in fields}
    out, seen = [], set()
    for a in answers:
        f = spec.get(a.get("key"))
        if not f or f.key in seen:
            continue
        v = str(a.get("value", "")).strip()
        if f.money:
            digits = v.replace(",", "")
            if not digits.isdigit() or not 0 < int(digits) < 10_000_000:
                continue
            v = int(digits)
        elif v not in {o.v for o in f.options}:
            continue
        seen.add(f.key)
        out.append({"key": f.key, "value": v, "quote": str(a.get("quote", ""))[:120]})
    return out


@app.post("/extract-info")
async def extract_info(req: ExtractRequest, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if too_many(ip):
        raise HTTPException(429, "요청이 많아요. 1분 뒤 다시 시도해 주세요.")
    try:
        response = await client.aio.models.generate_content(
            model=MODEL,
            contents=f"채울 수 있는 칸 (key | 질문 | 칸 이름 | 선택지):\n{describe(req.fields)}\n\n들은 말:\n{req.text}",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_json_schema=build_schema([f.key for f in req.fields]),
                temperature=0,
            ),
        )
    except errors.ClientError as e:  # 4xx: 키가 틀렸거나 무료 한도를 넘었을 때
        log.error("Gemini 요청 오류 %s: %s", e.code, e.message)
        if e.code == 429:
            raise HTTPException(429, "오늘 쓸 수 있는 AI 무료 한도를 넘었어요.")
        raise HTTPException(502, "AI 요청이 거절됐어요.")
    except errors.ServerError as e:
        log.error("Gemini 서버 오류 %s: %s", e.code, e.message)
        raise HTTPException(503, "AI 서버가 잠시 응답하지 못했어요.")

    try:
        data = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        log.error("JSON이 아닌 응답: %r", response.text)
        return {"answers": [], "urgent": False}
    return {"answers": clean(data.get("answers", []), req.fields), "urgent": bool(data.get("urgent"))}


@app.get("/health")
async def health():
    return {"ok": True}
