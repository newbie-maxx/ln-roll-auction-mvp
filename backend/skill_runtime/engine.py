"""Skill 运行时引擎（切片 B）：JSON SOP 资产的加载 / 校验 / 热加载 / 生命周期 / 执行。

skill 是纯配置资产（无业务代码）：加载期做 schema 校验（结构完整性、表达式编译、
工具名白名单），运行期按步骤类型（tool/if/foreach/while/assert/assign/fail）递归
执行并逐步留痕；工具调用经注入的 ``tool_caller(name, args) -> dict`` 回调完成
（后续接 MCP 客户端，本模块不关心协议）。

热加载：每次 run/list 前比对目录内 ``*.json`` 的 ``(mtime_ns, sha256)``，变化才
重解析；解析失败保留旧版本继续可用并审计 ``reload_rejected``；从未成功加载过的
坏文件在 list 中标注（status 取自文件）但 run 拒绝。

生命周期：draft→active；active↔suspended；active/suspended→deprecated；
deprecated→offline。仅合法迁移写回 JSON（保序、缩进 2）并审计。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .expr import ExprError, compile_template

__all__ = [
    "SkillRuntime", "ToolError",
    "SkillRuntimeError", "SkillNotFoundError", "SkillValidationError", "SkillStatusError",
    "SKILL_STATUSES", "STATUS_TRANSITIONS",
]

SKILL_STATUSES = ("draft", "active", "suspended", "deprecated", "offline")

#: 生命周期迁移表：仅列出的目标状态合法（deprecated→offline 为终点）。
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active"}),
    "active": frozenset({"suspended", "deprecated"}),
    "suspended": frozenset({"active", "deprecated"}),
    "deprecated": frozenset({"offline"}),
    "offline": frozenset(),
}

INPUT_TYPES = ("string", "number", "boolean")
STEP_KINDS = ("tool", "if", "foreach", "while", "assert", "assign", "fail")
_TRUNCATE_LEN = 2000  # 留痕中字符串实参超过该长度即截断标记


class SkillRuntimeError(Exception):
    """运行时 API 层错误（未知 skill / 非法迁移 / 写回失败等）。"""


class SkillNotFoundError(SkillRuntimeError):
    """skill 不存在，或文件存在但从未成功加载。"""


class SkillValidationError(SkillRuntimeError):
    """skill 资产未通过加载校验。"""


class SkillStatusError(SkillRuntimeError):
    """非法生命周期状态或迁移。"""


class ToolError(Exception):
    """tool_caller 抛出的工具错误；引擎捕获后转为运行失败（TOOL_ERROR）。"""


class _Fail(Exception):
    """run 过程中的失败，转为运行记录的 error 字段，不向外抛。"""

    def __init__(self, code: str, message: str, step: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.step = step


class _RunState:
    """单次 run 的可变状态（步骤计数），避免挂在实例上保证可重入。"""

    __slots__ = ("count",)

    def __init__(self):
        self.count = 0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 加载期编译 ----------

def _compile_value(value: Any) -> tuple:
    """把 JSON 值编译为可解析节点：``("expr", fn)`` / ``("lit", v)`` / ``("map", ..)`` / ``("arr", ..)``。

    字符串含 ``${`` 时必须是整段模板，否则 ExprError（加载期拒绝）；嵌套 dict/list
    中的模板同样编译。
    """
    if isinstance(value, str):
        fn = compile_template(value)
        return ("expr", fn) if fn is not None else ("lit", value)
    if isinstance(value, dict):
        return ("map", {k: _compile_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return ("arr", [_compile_value(v) for v in value])
    return ("lit", value)


def _resolve(node: tuple, ctx: dict) -> Any:
    tag, payload = node
    if tag == "lit":
        return payload
    if tag == "expr":
        return payload(ctx)  # ExprError 向上冒泡
    if tag == "map":
        return {k: _resolve(v, ctx) for k, v in payload.items()}
    return [_resolve(v, ctx) for v in payload]


def _compile_tool(raw: dict, label: str, known_tools: set[str] | None) -> dict:
    name = raw.get("tool")
    if not isinstance(name, str) or not name:
        raise SkillValidationError(f"{label}: tool 名须为非空字符串")
    if known_tools is not None and name not in known_tools:
        raise SkillValidationError(f"{label}: 未知工具: {name}")
    args = raw.get("args")
    if not isinstance(args, dict):
        raise SkillValidationError(f"{label}: tool.args 须为对象")
    output = raw.get("output")
    if output is not None and (not isinstance(output, str) or not output):
        raise SkillValidationError(f"{label}: output 须为非空字符串")
    return {"kind": "tool", "tool": name, "args": _compile_value(args), "output": output}


def _compile_steps(steps: Any, label: str, known_tools: set[str] | None) -> list[dict]:
    if not isinstance(steps, list):
        raise SkillValidationError(f"{label}: 步骤序列须为列表")
    compiled: list[dict] = []
    for i, raw in enumerate(steps):
        step_label = f"{label}.steps[{i}]"
        if not isinstance(raw, dict):
            raise SkillValidationError(f"{step_label}: 步骤须为对象")
        kinds = [k for k in STEP_KINDS if k in raw]
        if len(kinds) != 1:
            raise SkillValidationError(
                f"{step_label}: 须恰含一个步骤类型键 {list(STEP_KINDS)}，实际为 {kinds or '无'}")
        kind = kinds[0]
        try:
            if kind == "tool":
                compiled.append(_compile_tool(raw, step_label, known_tools))
            elif kind == "if":
                then_steps = raw.get("then")
                else_steps = raw.get("else", [])
                if not isinstance(then_steps, list):
                    raise SkillValidationError(f"{step_label}: if.then 须为步骤列表")
                if not isinstance(else_steps, list):
                    raise SkillValidationError(f"{step_label}: if.else 须为步骤列表")
                compiled.append({
                    "kind": "if", "cond": _compile_value(raw["if"]),
                    "then": _compile_steps(then_steps, step_label + ".then", known_tools),
                    "else": _compile_steps(else_steps, step_label + ".else", known_tools),
                })
            elif kind == "foreach":
                var = raw.get("as")
                if not isinstance(var, str) or not var:
                    raise SkillValidationError(f"{step_label}: foreach.as 须为非空字符串")
                compiled.append({
                    "kind": "foreach", "iter": _compile_value(raw["foreach"]), "as": var,
                    "body": _compile_steps(raw.get("body"), step_label + ".body", known_tools),
                })
            elif kind == "while":
                max_iter = raw.get("max_iter")
                if not isinstance(max_iter, int) or isinstance(max_iter, bool) or max_iter < 1:
                    raise SkillValidationError(
                        f"{step_label}: while.max_iter 须为正整数（必填，防死循环）")
                compiled.append({
                    "kind": "while", "cond": _compile_value(raw["while"]), "max_iter": max_iter,
                    "body": _compile_steps(raw.get("body"), step_label + ".body", known_tools),
                })
            elif kind == "assert":
                message = raw.get("message")
                if message is not None and not isinstance(message, str):
                    raise SkillValidationError(f"{step_label}: assert.message 须为字符串")
                compiled.append({"kind": "assert", "cond": _compile_value(raw["assert"]),
                                 "message": message, "source": raw["assert"]})
            elif kind == "assign":
                mapping = raw["assign"]
                if not isinstance(mapping, dict):
                    raise SkillValidationError(f"{step_label}: assign 须为对象")
                compiled.append({"kind": "assign",
                                 "items": [(k, _compile_value(v)) for k, v in mapping.items()]})
            else:  # fail
                spec = raw["fail"]
                if not isinstance(spec, dict):
                    raise SkillValidationError(f"{step_label}: fail 须为对象")
                code = spec.get("code")
                message = spec.get("message", "")
                if not isinstance(code, str) or not code:
                    raise SkillValidationError(f"{step_label}: fail.code 须为非空字符串")
                if not isinstance(message, str):
                    raise SkillValidationError(f"{step_label}: fail.message 须为字符串")
                compiled.append({"kind": "fail", "code": code, "message": message})
        except ExprError as exc:
            raise SkillValidationError(f"{step_label}: 表达式错误 - {exc}") from None
        except KeyError as exc:
            raise SkillValidationError(f"{step_label}: {kind} 步骤缺少字段 {exc}") from None
    return compiled


def _compile_skill(data: Any, label: str, known_tools: set[str] | None) -> list[dict]:
    """加载校验 + 步骤编译；任何问题抛 SkillValidationError。"""
    if not isinstance(data, dict):
        raise SkillValidationError(f"{label}: skill 资产须为 JSON 对象")
    for field in ("id", "name", "version"):
        val = data.get(field)
        if not isinstance(val, str) or not val.strip():
            raise SkillValidationError(f"{label}: {field} 须为非空字符串")
    if data.get("status") not in SKILL_STATUSES:
        raise SkillValidationError(
            f"{label}: status 非法: {data.get('status')!r}（允许 {'/'.join(SKILL_STATUSES)}）")
    owner = data.get("owner")
    if owner is not None and not isinstance(owner, str):
        raise SkillValidationError(f"{label}: owner 须为字符串")
    inputs = data.get("inputs", {})
    if not isinstance(inputs, dict):
        raise SkillValidationError(f"{label}: inputs 须为对象")
    for iname, spec in inputs.items():
        if not isinstance(spec, dict) or spec.get("type") not in INPUT_TYPES:
            raise SkillValidationError(
                f"{label}: inputs.{iname} 须为对象且 type ∈ {'/'.join(INPUT_TYPES)}")
        if not isinstance(spec.get("required", False), bool):
            raise SkillValidationError(f"{label}: inputs.{iname}.required 须为布尔")
    returns = data.get("returns")
    if returns is not None:
        if not isinstance(returns, list) or not all(isinstance(r, str) and r for r in returns):
            raise SkillValidationError(f"{label}: returns 须为非空字符串列表")
    return _compile_steps(data.get("steps", []), label, known_tools)


class _Skill:
    """已成功加载的 skill（原始 JSON + 编译后步骤）。"""

    __slots__ = ("raw", "id", "name", "version", "status", "inputs", "returns",
                 "steps", "sha256", "loaded_at")

    def __init__(self, raw: dict, steps: list[dict], sha256: str, loaded_at: str):
        self.raw = raw
        self.id = raw["id"]
        self.name = raw["name"]
        self.version = raw["version"]
        self.status = raw["status"]
        self.inputs = raw.get("inputs", {})
        self.returns = list(raw.get("returns") or [])
        self.steps = steps
        self.sha256 = sha256
        self.loaded_at = loaded_at


class _Entry:
    """目录中一个 ``*.json`` 文件的注册表条目。

    skill 为最近一次成功加载的版本（文件后来变坏时保留旧版本继续可用）；
    skill 为 None 表示从未成功加载。
    """

    __slots__ = ("filename", "mtime_ns", "sha256", "data", "skill", "error")

    def __init__(self, filename: str, mtime_ns: int, sha256: str,
                 data: Any, skill: _Skill | None, error: str | None):
        self.filename = filename
        self.mtime_ns = mtime_ns
        self.sha256 = sha256
        self.data = data
        self.skill = skill
        self.error = error


def _truncate_snapshot(value: Any) -> Any:
    """留痕用：超长字符串值截断并标记（递归作用于 dict/list）。"""
    if isinstance(value, str):
        if len(value) <= _TRUNCATE_LEN:
            return value
        return value[:_TRUNCATE_LEN] + f"...[截断,原始长度={len(value)}]"
    if isinstance(value, dict):
        return {k: _truncate_snapshot(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_snapshot(v) for v in value]
    return value


class SkillRuntime:
    """SOP 配置驱动的 skill 运行时：加载 / 校验 / 热加载 / 生命周期 / 执行。"""

    def __init__(self, skills_dir: Path, tool_caller: Callable[[str, dict], dict], *,
                 known_tools: set[str] | None = None, persister: Any = None,
                 max_steps: int = 2000):
        self._dir = Path(skills_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        if not callable(tool_caller):
            raise SkillRuntimeError("tool_caller 须为可调用对象")
        self._tool_caller = tool_caller
        self._known_tools = None if known_tools is None else set(known_tools)
        self._persister = persister
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
            raise SkillRuntimeError("max_steps 须为正整数")
        self._max_steps = max_steps
        self._entries: dict[str, _Entry] = {}
        self._by_id: dict[str, _Entry] = {}
        self._scan(initial=True)

    # ---------- 对外 API ----------

    def run(self, skill_id: str, inputs: dict) -> dict:
        """执行一个 skill；成败均返回运行记录并经 persister.log_run 留痕。"""
        self._scan()
        entry = self._by_id.get(skill_id)
        if entry is None:
            for e in self._entries.values():
                if (e.skill is None and isinstance(e.data, dict)
                        and e.data.get("id") == skill_id):
                    raise SkillNotFoundError(
                        f"skill '{skill_id}' 存在文件但从未成功加载: {e.error}")
            raise SkillNotFoundError(f"未知 skill: {skill_id}")
        sk = entry.skill
        record: dict[str, Any] = {
            "run_id": f"sk-{uuid.uuid4().hex[:8]}",
            "skill_id": sk.id,
            "skill_version": sk.version,
            "status": "failed",
            "outputs": None,
            "error": None,
            "inputs": dict(inputs) if isinstance(inputs, dict) else inputs,
            "steps": [],
            "started_at": _now(),
            "finished_at": None,
        }
        try:
            if sk.status != "active":
                raise _Fail("SKILL_NOT_ACTIVE", f"skill 当前状态为 {sk.status}，仅 active 可执行")
            self._check_inputs(sk, record["inputs"])
            ctx = dict(record["inputs"])
            self._exec_block(sk.steps, ctx, _RunState(), record["steps"])
            missing = [r for r in sk.returns if r not in ctx]
            if missing:
                raise _Fail("MISSING_RETURN",
                            f"returns 声明的变量缺失: {', '.join(missing)}")
            record["outputs"] = {r: ctx[r] for r in sk.returns}
            record["status"] = "ok"
        except _Fail as exc:
            record["error"] = {"code": exc.code, "message": exc.message, "step": exc.step}
        finally:
            record["finished_at"] = _now()
        if self._persister is not None:
            self._persister.log_run(record)
        return record

    def list_skills(self) -> list[dict]:
        """列出全部 skill 文件（含从未加载成功的坏文件），按 id 排序。"""
        self._scan()
        items = []
        for entry in self._entries.values():
            sk = entry.skill
            if sk is not None:
                items.append({"id": sk.id, "name": sk.name, "version": sk.version,
                              "status": sk.status, "sha256": sk.sha256,
                              "loaded_at": sk.loaded_at, "error": None})
            else:
                data = entry.data if isinstance(entry.data, dict) else {}
                raw = data.get
                items.append({
                    "id": raw("id") if isinstance(raw("id"), str) else None,
                    "name": raw("name") if isinstance(raw("name"), str) else None,
                    "version": raw("version") if isinstance(raw("version"), str) else None,
                    "status": raw("status"),  # 坏文件：status 直接取自文件
                    "sha256": entry.sha256, "loaded_at": None, "error": entry.error,
                })
        items.sort(key=lambda it: (it["id"] is None, it["id"] or ""))
        return items

    def set_status(self, skill_id: str, new_status: str, reason: str = "") -> dict:
        """生命周期迁移：仅合法迁移写回 JSON 文件（保序、缩进 2）并审计。"""
        if new_status not in SKILL_STATUSES:
            raise SkillStatusError(
                f"非法状态: {new_status!r}（允许 {'/'.join(SKILL_STATUSES)}）")
        if not isinstance(reason, str):
            raise SkillStatusError("reason 须为字符串")
        self._scan()
        entry = self._by_id.get(skill_id)
        if entry is None:
            raise SkillNotFoundError(f"未知 skill: {skill_id}")
        sk = entry.skill
        current = sk.status
        if new_status not in STATUS_TRANSITIONS[current]:
            raise SkillStatusError(f"非法状态迁移: {current} → {new_status}")
        sk.raw["status"] = new_status
        text = json.dumps(sk.raw, ensure_ascii=False, indent=2) + "\n"
        path = self._dir / entry.filename
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            sk.raw["status"] = current  # 写回失败回滚内存状态
            raise SkillRuntimeError(f"写回 skill 文件失败: {exc}") from None
        sk.status = new_status
        sk.sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        entry.sha256 = sk.sha256
        entry.mtime_ns = path.stat().st_mtime_ns
        self._audit({"event": "status_changed", "skill_id": skill_id,
                     "from": current, "to": new_status, "reason": reason})
        return {"skill_id": skill_id, "from": current, "to": new_status, "reason": reason}

    def reload(self, skill_id: str | None = None) -> dict:
        """强制重扫目录（忽略缓存），返回 {loaded, rejected} 报告。"""
        self._scan(force=True)
        loaded: list[str] = []
        rejected: list[dict] = []
        for entry in self._entries.values():
            if entry.skill is not None:
                if skill_id is None or entry.skill.id == skill_id:
                    loaded.append(entry.skill.id)
            else:
                fid = entry.data.get("id") if isinstance(entry.data, dict) else None
                if skill_id is None or fid == skill_id:
                    rejected.append({"skill_id": fid, "error": entry.error})
        return {"loaded": sorted(loaded), "rejected": rejected}

    # ---------- 热加载 / 注册表 ----------

    def _scan(self, force: bool = False, initial: bool = False) -> None:
        try:
            paths = sorted(p for p in self._dir.glob("*.json") if p.is_file())
        except OSError:
            paths = []
        seen: set[str] = set()
        for path in paths:
            seen.add(path.name)
            try:
                mtime_ns = path.stat().st_mtime_ns
                raw = path.read_bytes()
            except OSError as exc:
                self._record_failure(path.name, -1, "", None, f"文件读取失败: {exc}", initial)
                continue
            entry = self._entries.get(path.name)
            if (not force and entry is not None
                    and entry.mtime_ns == mtime_ns and entry.sha256 == sha256_of(raw)):
                continue  # 内容未变，跳过解析
            self._load_file(path.name, raw, mtime_ns, initial)
        for gone in set(self._entries) - seen:
            del self._entries[gone]  # 文件已删除
        self._reindex()

    def _load_file(self, name: str, raw: bytes, mtime_ns: int, initial: bool) -> None:
        sha = sha256_of(raw)
        prev = self._entries.get(name)
        data: Any = None
        skill: _Skill | None = None
        error: str | None = None
        try:
            data = json.loads(raw)
            steps = _compile_skill(data, name, self._known_tools)
            if prev is not None and prev.skill is not None and prev.skill.sha256 == sha:
                skill = prev.skill  # 内容未变：保留原载入时间
            else:
                conflict = next((e for e in self._entries.values()
                                 if e.skill is not None and e.skill.id == data.get("id")
                                 and e.filename != name), None)
                if conflict is not None:
                    error = f"重复 skill id: {data.get('id')}（已被 {conflict.filename} 占用）"
                else:
                    skill = _Skill(data, steps, sha, _now())
        except ValueError as exc:  # JSONDecodeError / UnicodeDecodeError
            error = f"JSON 解析失败: {exc}"
        except SkillValidationError as exc:
            error = str(exc)
        if skill is not None:
            self._entries[name] = _Entry(name, mtime_ns, sha, data, skill, None)
            return
        self._record_failure(name, mtime_ns, sha, data, error, initial)

    def _record_failure(self, name: str, mtime_ns: int, sha: str, data: Any,
                        error: str | None, initial: bool) -> None:
        prev = self._entries.get(name)
        if prev is not None and prev.skill is not None:
            # 解析失败 → 旧版本继续可用，仅刷新尝试缓存（同内容不重复审计）
            prev.mtime_ns, prev.sha256, prev.data, prev.error = mtime_ns, sha, data, error
            if not initial:
                self._audit({"event": "reload_rejected", "skill_id": prev.skill.id,
                             "error": error})
            return
        skill_id = None
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            skill_id = data["id"]
        self._entries[name] = _Entry(name, mtime_ns, sha, data, None, error)
        if not initial:
            self._audit({"event": "reload_rejected", "skill_id": skill_id, "error": error})

    def _reindex(self) -> None:
        self._by_id = {e.skill.id: e for e in self._entries.values() if e.skill is not None}

    def _audit(self, entry: dict) -> None:
        if self._persister is not None:
            self._persister.log_audit(entry)

    # ---------- 执行 ----------

    def _check_inputs(self, sk: _Skill, inputs: Any) -> None:
        if not isinstance(inputs, dict):
            raise _Fail("INVALID_INPUT", "inputs 须为对象")
        for name, spec in sk.inputs.items():
            required = spec.get("required", False)
            if name not in inputs:
                if required:
                    raise _Fail("INVALID_INPUT", f"缺少必填输入: {name}")
                continue
            val = inputs[name]
            want = spec["type"]
            if want == "string" and not isinstance(val, str):
                raise _Fail("INVALID_INPUT",
                            f"输入 {name} 类型不符: 期望 string，得到 {type(val).__name__}")
            if want == "number" and (isinstance(val, bool) or not isinstance(val, (int, float))):
                raise _Fail("INVALID_INPUT",
                            f"输入 {name} 类型不符: 期望 number，得到 {type(val).__name__}")
            if want == "boolean" and not isinstance(val, bool):
                raise _Fail("INVALID_INPUT",
                            f"输入 {name} 类型不符: 期望 boolean，得到 {type(val).__name__}")

    def _value(self, node: tuple, ctx: dict, index: int) -> Any:
        try:
            return _resolve(node, ctx)
        except ExprError as exc:
            raise _Fail("EXPR_ERROR", f"步骤 {index} 表达式求值失败: {exc}", index) from None

    def _exec_block(self, steps: list[dict], ctx: dict, state: _RunState,
                    trace: list[dict]) -> None:
        for step in steps:
            state.count += 1
            if state.count > self._max_steps:
                raise _Fail("MAX_STEPS_EXCEEDED",
                            f"步骤总数超过上限 max_steps={self._max_steps}")
            index = state.count - 1
            kind = step["kind"]
            if kind == "tool":
                entry = {"index": index, "kind": kind, "tool": step["tool"],
                         "args_resolved": None, "ok": False}
                trace.append(entry)
                args = self._value(step["args"], ctx, index)
                entry["args_resolved"] = _truncate_snapshot(args)
                try:
                    result = self._tool_caller(step["tool"], args)
                except Exception as exc:  # ToolError 或其他工具侧异常 → 运行失败
                    message = str(exc).strip() or type(exc).__name__
                    entry["error"] = message
                    raise _Fail("TOOL_ERROR",
                                f"步骤 {index} 调用工具 {step['tool']} 失败: {message}",
                                index) from None
                entry["ok"] = True
                if step["output"]:
                    ctx[step["output"]] = result
            elif kind == "if":
                entry = {"index": index, "kind": kind, "ok": False}
                trace.append(entry)
                branch = step["then"] if self._value(step["cond"], ctx, index) else step["else"]
                self._exec_block(branch, ctx, state, trace)
                entry["ok"] = True
            elif kind == "foreach":
                entry = {"index": index, "kind": kind, "ok": False}
                trace.append(entry)
                items = self._value(step["iter"], ctx, index)
                if not isinstance(items, list):
                    raise _Fail("FOREACH_NOT_LIST",
                                f"步骤 {index} foreach 结果须为列表，得到 {type(items).__name__}",
                                index)
                for item in items:
                    ctx[step["as"]] = item
                    self._exec_block(step["body"], ctx, state, trace)
                entry["ok"] = True
            elif kind == "while":
                entry = {"index": index, "kind": kind, "ok": False}
                trace.append(entry)
                iterations = 0
                while self._value(step["cond"], ctx, index):
                    if iterations >= step["max_iter"]:
                        raise _Fail("WHILE_MAX_ITER",
                                    f"步骤 {index} while 超过 max_iter={step['max_iter']} 次仍为真",
                                    index)
                    self._exec_block(step["body"], ctx, state, trace)
                    iterations += 1
                entry["ok"] = True
            elif kind == "assert":
                entry = {"index": index, "kind": kind, "ok": False}
                trace.append(entry)
                if not self._value(step["cond"], ctx, index):
                    message = step["message"] or f"断言失败: {step['source']}"
                    entry["error"] = message
                    raise _Fail("ASSERT_FAILED", message, index)
                entry["ok"] = True
            elif kind == "assign":
                entry = {"index": index, "kind": kind, "ok": False}
                trace.append(entry)
                for var, node in step["items"]:
                    ctx[var] = self._value(node, ctx, index)
                entry["ok"] = True
            else:  # fail
                entry = {"index": index, "kind": kind, "ok": False,
                         "error": f"{step['code']}: {step['message']}"}
                trace.append(entry)
                raise _Fail(step["code"], step["message"], index)


def sha256_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
