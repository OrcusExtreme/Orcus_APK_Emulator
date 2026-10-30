"""A tiny App Inventor block model: one tree renders to YAIL (for the buildserver) and to Blockly XML (.bky).

Only the blocks the Orcus fixtures need. Formats follow appinventor-sources (blocklyeditor YAIL generators and
the Companion's own Screen1.bky/.yail): component_event / component_set_get / component_method, text,
math_number, logic_boolean, lexical and global variables, text_join, math_add, math_format_as_decimal,
controls_forRange, controls_eval_but_ignore, lists_select_item, lists_create_with, controls_openAnotherScreen,
controls_closeScreen.
"""

from dataclasses import dataclass, field
from itertools import count
from xml.sax.saxutils import escape, quoteattr

_ids = count(1)


def yail_str(s: str) -> str:
    """AI.Yail.quotifyForREPL: escape quotes and backslashes, anything outside printable ASCII as \\uXXXX."""
    out = []
    for ch in s:
        if ch in '\\"':
            out.append("\\" + ch)
        elif " " <= ch <= "~":
            out.append(ch)
        else:
            out.extend(f"\\u{unit:04x}" for unit in _utf16_units(ch))
    return '"' + "".join(out) + '"'


def _utf16_units(ch: str) -> list[int]:
    data = ch.encode("utf-16-be")
    return [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]


def _bid() -> str:
    return str(next(_ids))


def reset_ids() -> None:
    """Number block ids from 1 in each .bky, so a project always produces the same file."""
    global _ids
    _ids = count(1)


class Expr:
    def yail(self) -> str: ...
    def bky(self) -> str: ...


class Stmt:
    def yail(self) -> str: ...
    def bky(self) -> str: ...


def chain(stmts: "list[Stmt]") -> str:
    """Blockly statements are a <next> chain."""
    out = ""
    for s in reversed(stmts):
        inner = s.bky()
        out = inner if not out else inner[: inner.rfind("</block>")] + f"<next>{out}</next></block>"
    return out


@dataclass
class Text(Expr):
    s: str

    def yail(self) -> str:
        return yail_str(self.s)

    def bky(self) -> str:
        return f'<block type="text" id="{_bid()}"><field name="TEXT">{escape(self.s)}</field></block>'


@dataclass
class Num(Expr):
    n: float | int

    def yail(self) -> str:
        return _num(self.n)

    def bky(self) -> str:
        return f'<block type="math_number" id="{_bid()}"><field name="NUM">{_num(self.n)}</field></block>'


def _num(n: float | int) -> str:
    return str(int(n)) if float(n).is_integer() else repr(float(n))


@dataclass
class Bool(Expr):
    b: bool

    def yail(self) -> str:
        return "#t" if self.b else "#f"

    def bky(self) -> str:
        return f'<block type="logic_boolean" id="{_bid()}"><field name="BOOL">{"TRUE" if self.b else "FALSE"}</field></block>'


@dataclass
class Param(Expr):
    """An event parameter (a lexical variable)."""
    name: str

    def yail(self) -> str:
        return f"(lexical-value ${self.name})"

    def bky(self) -> str:
        return (f'<block type="lexical_variable_get" id="{_bid()}"><mutation><eventparam name="{self.name}"/>'
                f'</mutation><field name="VAR">{self.name}</field></block>')


@dataclass
class Global(Expr):
    name: str

    def yail(self) -> str:
        return f"(get-var g${self.name})"

    def bky(self) -> str:
        return f'<block type="lexical_variable_get" id="{_bid()}"><field name="VAR">global {self.name}</field></block>'


def _mutation(ctype: str, comp: str, **extra: str) -> str:
    attrs = " ".join(f"{k}={quoteattr(v)}" for k, v in extra.items())
    return (f'<mutation xmlns="http://www.w3.org/1999/xhtml" component_type="{ctype}" {attrs} is_generic="false" '
            f'instance_name="{comp}"></mutation><field name="COMPONENT_SELECTOR">{comp}</field>')


@dataclass
class Get(Expr):
    comp: str
    ctype: str
    prop: str

    def yail(self) -> str:
        return f"(get-property '{self.comp} '{self.prop})"

    def bky(self) -> str:
        return (f'<block type="component_set_get" id="{_bid()}">'
                f'{_mutation(self.ctype, self.comp, set_or_get="get", property_name=self.prop)}'
                f'<field name="PROP">{self.prop}</field></block>')


@dataclass
class Call(Expr, Stmt):
    """component_method; works as a statement or an expression."""
    comp: str
    ctype: str
    method: str
    args: list[Expr] = field(default_factory=list)
    types: list[str] = field(default_factory=list)

    def yail(self) -> str:
        args = " ".join(a.yail() for a in self.args)
        return (f"(call-component-method '{self.comp} '{self.method} (*list-for-runtime* {args}) "
                f"'({' '.join(self.types)}))")

    def bky(self) -> str:
        values = "".join(f'<value name="ARG{i}">{a.bky()}</value>' for i, a in enumerate(self.args))
        return (f'<block type="component_method" id="{_bid()}">'
                f'{_mutation(self.ctype, self.comp, method_name=self.method)}{values}</block>')


@dataclass
class Join(Expr):
    parts: list[Expr]

    def yail(self) -> str:
        return (f"(call-yail-primitive string-append (*list-for-runtime* {' '.join(p.yail() for p in self.parts)}) "
                f"'({' '.join('text' for _ in self.parts)}) \"join\")")

    def bky(self) -> str:
        values = "".join(f'<value name="ADD{i}">{p.bky()}</value>' for i, p in enumerate(self.parts))
        return f'<block type="text_join" id="{_bid()}"><mutation items="{len(self.parts)}"></mutation>{values}</block>'


@dataclass
class Add(Expr):
    a: Expr
    b: Expr

    def yail(self) -> str:
        return f"(call-yail-primitive + (*list-for-runtime* {self.a.yail()} {self.b.yail()}) '(number number) \"+\")"

    def bky(self) -> str:
        return (f'<block type="math_add" id="{_bid()}"><mutation items="2"></mutation>'
                f'<value name="NUM0">{self.a.bky()}</value><value name="NUM1">{self.b.bky()}</value></block>')


@dataclass
class Sub(Expr):
    a: Expr
    b: Expr

    def yail(self) -> str:
        return f"(call-yail-primitive - (*list-for-runtime* {self.a.yail()} {self.b.yail()}) '(number number) \"-\")"

    def bky(self) -> str:
        return (f'<block type="math_subtract" id="{_bid()}"><value name="A">{self.a.bky()}</value>'
                f'<value name="B">{self.b.bky()}</value></block>')


@dataclass
class RandomInt(Expr):
    lo: Expr
    hi: Expr

    def yail(self) -> str:
        return (f"(call-yail-primitive random-integer (*list-for-runtime* {self.lo.yail()} {self.hi.yail()}) "
                f"'(number number) \"random integer\")")

    def bky(self) -> str:
        return (f'<block type="math_random_int" id="{_bid()}"><value name="FROM">{self.lo.bky()}</value>'
                f'<value name="TO">{self.hi.bky()}</value></block>')


@dataclass
class Decimal(Expr):
    x: Expr
    places: int

    def yail(self) -> str:
        return (f"(call-yail-primitive format-as-decimal (*list-for-runtime* {self.x.yail()} {self.places}) "
                f"'(number number) \"format as decimal\")")

    def bky(self) -> str:
        return (f'<block type="math_format_as_decimal" id="{_bid()}"><value name="NUM">{self.x.bky()}</value>'
                f'<value name="PLACES">{Num(self.places).bky()}</value></block>')


@dataclass
class EmptyList(Expr):
    def yail(self) -> str:
        return "(call-yail-primitive make-yail-list (*list-for-runtime* ) '() \"make a list\")"

    def bky(self) -> str:
        return f'<block type="lists_create_with" id="{_bid()}"><mutation items="0"></mutation></block>'


@dataclass
class SelectItem(Expr):
    lst: Expr
    index: Expr

    def yail(self) -> str:
        return (f"(call-yail-primitive yail-list-get-item (*list-for-runtime* {self.lst.yail()} {self.index.yail()}) "
                f"'(list number) \"select list item\")")

    def bky(self) -> str:
        return (f'<block type="lists_select_item" id="{_bid()}"><value name="LIST">{self.lst.bky()}</value>'
                f'<value name="NUM">{self.index.bky()}</value></block>')


@dataclass
class Set(Stmt):
    comp: str
    ctype: str
    prop: str
    value: Expr
    ytype: str = "text"

    def yail(self) -> str:
        return f"(set-and-coerce-property! '{self.comp} '{self.prop} {self.value.yail()} '{self.ytype})"

    def bky(self) -> str:
        return (f'<block type="component_set_get" id="{_bid()}">'
                f'{_mutation(self.ctype, self.comp, set_or_get="set", property_name=self.prop)}'
                f'<field name="PROP">{self.prop}</field><value name="VALUE">{self.value.bky()}</value></block>')


@dataclass
class SetGlobal(Stmt):
    name: str
    value: Expr

    def yail(self) -> str:
        return f"(set-var! g${self.name} {self.value.yail()})"

    def bky(self) -> str:
        return (f'<block type="lexical_variable_set" id="{_bid()}"><field name="VAR">global {self.name}</field>'
                f'<value name="VALUE">{self.value.bky()}</value></block>')


@dataclass
class ForRange(Stmt):
    var: str
    start: Expr
    end: Expr
    step: Expr
    body: list[Stmt]

    def yail(self) -> str:
        body = " ".join(s.yail() for s in self.body)
        return f"(forrange ${self.var} (begin {body}) {self.start.yail()} {self.end.yail()} {self.step.yail()})"

    def bky(self) -> str:
        return (f'<block type="controls_forRange" id="{_bid()}"><field name="VAR">{self.var}</field>'
                f'<value name="START">{self.start.bky()}</value><value name="END">{self.end.bky()}</value>'
                f'<value name="STEP">{self.step.bky()}</value><statement name="DO">{chain(self.body)}'
                f'</statement></block>')


@dataclass
class EvalIgnore(Stmt):
    """'evaluate but ignore': run an expression as a statement."""
    value: Expr

    def yail(self) -> str:
        return f'(begin {self.value.yail()} "ignored")'

    def bky(self) -> str:
        return (f'<block type="controls_eval_but_ignore" id="{_bid()}"><value name="VALUE">{self.value.bky()}'
                f'</value></block>')


@dataclass
class OpenScreen(Stmt):
    name: str

    def yail(self) -> str:
        return (f"(call-yail-primitive open-another-screen (*list-for-runtime* {yail_str(self.name)}) '(text) "
                f"\"open another screen\")")

    def bky(self) -> str:
        return (f'<block type="controls_openAnotherScreen" id="{_bid()}"><value name="SCREEN">'
                f'{Text(self.name).bky()}</value></block>')


@dataclass
class CloseScreen(Stmt):
    def yail(self) -> str:
        return "(call-yail-primitive close-screen (*list-for-runtime* ) '() \"close screen\")"

    def bky(self) -> str:
        return f'<block type="controls_closeScreen" id="{_bid()}"></block>'


# --- top level ------------------------------------------------------------------------------------------
@dataclass
class GlobalDecl:
    name: str
    value: Expr

    def yail(self) -> str:
        return f"(def g${self.name} {self.value.yail()})"

    def bky(self, x: int, y: int) -> str:
        return (f'<block type="global_declaration" id="{_bid()}" x="{x}" y="{y}"><field name="NAME">{self.name}</field>'
                f'<value name="VALUE">{self.value.bky()}</value></block>')


@dataclass
class Event:
    comp: str
    ctype: str
    event: str
    params: list[str]
    body: list[Stmt]

    def yail(self) -> str:
        params = " ".join(f"${p}" for p in self.params)
        body = "\n ".join(s.yail() for s in self.body) or "(get-var *the-null-value*)"
        return f"(define-event {self.comp} {self.event}({params})(set-this-form)\n  {body})"

    def bky(self, x: int, y: int) -> str:
        return (f'<block type="component_event" id="{_bid()}" x="{x}" y="{y}">'
                f'{_mutation(self.ctype, self.comp, event_name=self.event)}'
                f'<statement name="DO">{chain(self.body)}</statement></block>')
