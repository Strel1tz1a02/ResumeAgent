"""将模型泄漏到正文中的 DSML 工具调用恢复为通用调用。"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.ai_chat.tools.buffer import encode_tool_call

_BARS = r"(?:\||｜)*"
_START_RE = re.compile(rf"<{_BARS}DSML{_BARS}tool_calls>")
_BLOCK_RE = re.compile(
    rf"<{_BARS}DSML{_BARS}tool_calls>(?P<body>.*?)"
    rf"</{_BARS}DSML{_BARS}tool_calls>",
    re.DOTALL,
)
_INVOKE_RE = re.compile(
    rf"<{_BARS}DSML{_BARS}invoke\b(?P<attrs>[^>]*)>(?P<body>.*?)"
    rf"</{_BARS}DSML{_BARS}invoke>",
    re.DOTALL,
)
_PARAMETER_RE = re.compile(
    rf"<{_BARS}DSML{_BARS}parameter\b(?P<attrs>[^>]*)>(?P<value>.*?)"
    rf"</{_BARS}DSML{_BARS}parameter>",
    re.DOTALL,
)
_ATTRIBUTE_RE = re.compile(r'(?P<name>[a-zA-Z_][\w-]*)="(?P<value>[^"]*)"')


def _attributes(value: str) -> dict[str, str]:
    """读取 DSML 标签属性。"""
    return {
        match.group("name"): match.group("value")
        for match in _ATTRIBUTE_RE.finditer(value)
    }


def _parameter_value(raw: str, is_string: bool | None) -> Any:
    """按 DSML 的字符串标记恢复 JSON 值或普通字符串。"""
    value = html.unescape(raw).strip()
    if is_string is True:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        if is_string is None:
            return value
        raise


def _parse_calls(value: str) -> tuple[list[str], str]:
    """解析完整 DSML 块，并返回块外仍应展示的正文。"""
    block = _BLOCK_RE.search(value)
    if block is None:
        return [], value
    calls: list[str] = []
    try:
        for index, invoke in enumerate(_INVOKE_RE.finditer(block.group("body"))):
            invoke_attrs = _attributes(invoke.group("attrs"))
            name = invoke_attrs.get("name", "")
            if not name:
                return [], value
            arguments: dict[str, Any] = {}
            for parameter in _PARAMETER_RE.finditer(invoke.group("body")):
                attrs = _attributes(parameter.group("attrs"))
                parameter_name = attrs.get("name", "")
                if not parameter_name:
                    return [], value
                string_flag = attrs.get("string")
                is_string = (
                    string_flag.lower() == "true"
                    if string_flag is not None
                    else None
                )
                arguments[parameter_name] = _parameter_value(
                    parameter.group("value"), is_string
                )
            calls.append(
                encode_tool_call(
                    index=index,
                    provider_id=None,
                    name=name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                )
            )
    except (json.JSONDecodeError, ValueError):
        return [], value
    if not calls:
        return [], value
    visible = value[: block.start()] + value[block.end() :]
    return calls, visible


@dataclass
class DsmlToolCallFallback:
    """流式隐藏可能的 DSML 前缀，结束时原子恢复工具调用。"""

    _buffer: str = field(default="", init=False)
    _capturing: bool = field(default=False, init=False)

    def feed(self, text: str) -> str:
        """接收正文增量，只返回已确定不是 DSML 协议的文本。"""
        self._buffer += text
        if self._capturing:
            return ""
        match = _START_RE.search(self._buffer)
        if match is not None:
            visible = self._buffer[: match.start()]
            self._buffer = self._buffer[match.start() :]
            self._capturing = True
            return visible
        last_open = self._buffer.rfind("<")
        if last_open >= 0 and len(self._buffer) - last_open <= 64:
            visible = self._buffer[:last_open]
            self._buffer = self._buffer[last_open:]
            return visible
        visible = self._buffer
        self._buffer = ""
        return visible

    def finish(self) -> tuple[list[str], str]:
        """流结束时解析已捕获协议；解析失败则原样作为正文返回。"""
        calls, visible = _parse_calls(self._buffer)
        self._buffer = ""
        self._capturing = False
        return calls, visible
