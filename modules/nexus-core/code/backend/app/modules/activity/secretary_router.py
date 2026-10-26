"""HTTP 层（契约 v2.39 4.1 / 4.2 / 4.3）：秘书报告、问人、sessionChange 提议。**不许有业务判断**，全在 ``secretary.py`` / ``questions.py`` / ``session_items.py``。

提交（三个 POST）：无 Bearer 可以；带 Bearer 只有范围 ``write``；其余 403（同 ``_no_device_token_unless_write``）。回答 / 撤下只许人。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from . import questions, secretary, session_items
from .new_items_router import _no_device_token_unless_write as submitter
from .new_items_router import human

router = APIRouter(prefix="/activity", tags=["activity"])


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: Any = None  # 形状 / 长度在 secretary.submit 里判（400，不是 422）
    text: Any = None
    author: Any = None


class QuestionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: Any = None


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option: Any = None
    text: Any = None


class SessionChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: StrictStr
    op: Literal["reassign"]
    taskId: StrictStr | None = None
    projectId: StrictStr | None = None
    reason: StrictStr = ""
    directApply: StrictBool = False
    confidencePercent: Annotated[StrictInt, Field(ge=0, le=100)] | None = None


@router.post("/secretary-reports", dependencies=[Depends(submitter)])
def post_report(body: ReportIn) -> dict:
    return secretary.submit(body.date, body.text, body.author)


@router.get("/secretary-reports")
def get_reports(date: str | None = None, from_: Annotated[str | None, Query(alias="from")] = None, to: str | None = None) -> dict:
    return secretary.listing(date, from_, to)


@router.post("/questions", dependencies=[Depends(submitter)])
def post_questions(body: QuestionsIn) -> dict:
    return questions.submit(body.questions)


@router.get("/questions")
def get_questions(status: Literal["pending", "all"] = "pending", limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    return questions.listing(status, limit)


@router.post("/questions/{qid}/answer", dependencies=[Depends(human)])
def answer(qid: str, body: AnswerIn, request: Request) -> dict:
    return questions.answer(qid, body.option, body.text, request)


@router.post("/questions/{qid}/dismiss", dependencies=[Depends(human)])
def dismiss(qid: str) -> dict:
    return questions.dismiss(qid)


@router.post("/proposals/sessions", dependencies=[Depends(submitter)])
def propose_session(body: SessionChangeIn, request: Request) -> dict:
    return session_items.propose(body.eventId, body.taskId, body.projectId, body.reason, body.directApply, request, body.confidencePercent)
