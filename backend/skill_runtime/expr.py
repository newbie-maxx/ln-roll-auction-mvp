"""SOP 表达式引擎（安全白名单）。

语法：数字 / 单引号字符串 / true / false / null 字面量；变量与属性访问 ``a.b``、
下标 ``a[0]``、``a['k']``；一元 ``-``/``not``；二元 ``+ - * / %``、比较
``== != < <= > >=``、``and or``；唯一函数 ``len(x)``。除零为求值期错误。

实现：``ast.parse(..., mode='eval')`` + 白名单节点校验（Name/Constant/Attribute/
Subscript/UnaryOp/BinOp/BoolOp/Compare/Call 仅 len），出现任何其他节点（函数定义、
任意调用、下划线名等）即校验错误。Attribute/Subscript 对 dict 取键，缺失返回
null 而非抛错，便于业务判断（``a.missing == null``）。

字符串模板：``"${a.b > 1 and len(xs) > 0}"`` 整段模板 → 求值结果；模板串内混有
其他文本（如 ``"prefix${x}"``）不支持 → 编译期拒绝。
"""
from __future__ import annotations

import ast
from typing import Any, Callable

__all__ = ["ExprError", "compile_expr", "evaluate", "compile_template", "template_inner"]


class ExprError(Exception):
    """表达式编译期或求值期错误。"""


_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Attribute, ast.Subscript, ast.Load,
    ast.UnaryOp, ast.USub, ast.Not, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Mod, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE, ast.Call,
)
_ARITH_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
_CMP_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_CONST_TYPES = (int, float, str, bool, type(None))


def _check_ident(name: str, what: str) -> None:
    # 拒绝下划线开头的名字（__class__/__dict__ 等内部属性）；run_date 等普通名字合法
    if not name or name.startswith("_"):
        raise ExprError(f"不允许的{what}: {name!r}（禁止下划线开头的名字，防越权访问内部属性）")


def _check(node: ast.AST) -> None:
    """递归校验 AST 节点是否全部落在白名单内。"""
    if not isinstance(node, _ALLOWED_NODES):
        raise ExprError(f"不允许的语法节点: {type(node).__name__}")
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, _CONST_TYPES):
            raise ExprError(f"不允许的字面量: {node.value!r}")
    elif isinstance(node, ast.Name):
        _check_ident(node.id, "变量名")
    elif isinstance(node, ast.Attribute):
        _check_ident(node.attr, "属性名")
    elif isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "len"):
            raise ExprError("仅允许调用 len()")
        if len(node.args) != 1 or node.keywords:
            raise ExprError("len() 恰接受一个位置参数")
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ARITH_OPS):
            raise ExprError(f"不允许的运算符: {type(node.op).__name__}")
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if not isinstance(op, _CMP_OPS):
                raise ExprError(f"不允许的比较运算符: {type(op).__name__}")
    for child in ast.iter_child_nodes(node):
        _check(child)


def _eval(node: ast.AST, ctx: dict) -> Any:
    """对已校验的 AST 递归求值（不使用 eval，杜绝逃逸）。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        name = node.id
        if name == "true":
            return True
        if name == "false":
            return False
        if name == "null":
            return None
        return ctx.get(name)  # 未定义变量按 null 处理
    if isinstance(node, ast.Attribute):
        obj = _eval(node.value, ctx)
        if isinstance(obj, dict):
            return obj.get(node.attr)  # 缺失键 → null
        raise ExprError(f"仅支持对 dict 取属性，无法取 .{node.attr}")
    if isinstance(node, ast.Subscript):
        obj = _eval(node.value, ctx)
        key = _eval(node.slice, ctx)
        if isinstance(obj, dict):
            try:
                return obj.get(key)
            except TypeError:
                raise ExprError(f"dict 键不可哈希: {key!r}") from None
        if isinstance(obj, (list, tuple, str)):
            if not isinstance(key, int):
                raise ExprError(f"序列下标须为整数，得到 {key!r}")
            return obj[key] if -len(obj) <= key < len(obj) else None
        raise ExprError("仅支持对 dict/list 取下标")
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval(node.operand, ctx)
        val = _eval(node.operand, ctx)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ExprError(f"一元 '-' 仅用于数字，得到 {val!r}")
        return -val
    if isinstance(node, ast.BinOp):
        return _arith(node.op, _eval(node.left, ctx), _eval(node.right, ctx))
    if isinstance(node, ast.BoolOp):
        is_and = isinstance(node.op, ast.And)
        result = _eval(node.values[0], ctx)
        for sub in node.values[1:]:
            if is_and and not result:
                return result
            if not is_and and result:
                return result
            result = _eval(sub, ctx)
        return result
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, ctx)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):  # 编译期已限定 len(x)
        val = _eval(node.args[0], ctx)
        if isinstance(val, (str, list, tuple, dict)):
            return len(val)
        raise ExprError(f"len() 参数须为 str/list/dict，得到 {type(val).__name__}")
    raise ExprError(f"不允许的语法节点: {type(node).__name__}")  # 编译期已拦，兜底


def _arith(op: ast.AST, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Add) and isinstance(left, str) and isinstance(right, str):
        return left + right
    if (isinstance(left, bool) or isinstance(right, bool)
            or not isinstance(left, (int, float)) or not isinstance(right, (int, float))):
        raise ExprError(f"算术运算须为数字，得到 {left!r} / {right!r}")
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        if right == 0:
            raise ExprError("除零")
        return left / right
    if isinstance(op, ast.Mod):
        if right == 0:
            raise ExprError("模零")
        return left % right
    raise ExprError("不允许的算术运算符")  # 编译期已拦，兜底


def _compare(op: ast.AST, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    numeric = isinstance(left, (int, float)) and isinstance(right, (int, float))
    textual = isinstance(left, str) and isinstance(right, str)
    if not (numeric or textual):
        raise ExprError(f"排序比较须为数字或同为字符串，得到 {left!r} / {right!r}")
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    return left >= right


def compile_expr(source: str) -> Callable[[dict], Any]:
    """编译表达式（错误在编译期报），返回 ``fn(ctx)`` 可反复求值。"""
    if not isinstance(source, str):
        raise ExprError("表达式须为字符串")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ExprError(f"表达式语法错误: {source!r}（{exc}）") from None
    _check(tree)

    def _run(ctx: dict) -> Any:
        return _eval(tree.body, ctx if isinstance(ctx, dict) else {})

    return _run


def evaluate(source: str, ctx: dict) -> Any:
    """一次性编译并求值。"""
    return compile_expr(source)(ctx)


def template_inner(text: str) -> str | None:
    """整段 ``${...}`` 模板 → 内部表达式；非模板串 → None；混合文本/残缺模板 → ExprError。"""
    if not isinstance(text, str) or "${" not in text:
        return None
    if not (text.startswith("${") and text.endswith("}")):
        raise ExprError(f"模板串必须整串为 ${{...}} 形式，不支持混合文本: {text!r}")
    inner = text[2:-1]
    if "${" in inner:
        raise ExprError(f"不支持多个/嵌套模板: {text!r}")
    return inner


def compile_template(text: str) -> Callable[[dict], Any] | None:
    """整段 ``${...}`` 模板 → 编译后求值函数；字面量 → None；混合文本 → ExprError。"""
    inner = template_inner(text)
    return None if inner is None else compile_expr(inner)
