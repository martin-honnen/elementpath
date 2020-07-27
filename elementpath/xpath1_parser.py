#
# Copyright (c), 2018-2020, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
import re
import math
import decimal
import operator
from copy import copy

from .exceptions import MissingContextError, ElementPathKeyError
from .datatypes import AbstractDateTime, Duration, DayTimeDuration, \
    YearMonthDuration, NumericProxy, ArithmeticProxy
from .xpath_context import XPathSchemaContext
from .tdop_parser import Parser
from .namespaces import XML_ID, XML_LANG, XML_NAMESPACE, XSD_NAMESPACE, get_prefixed_name
from .schema_proxy import AbstractSchemaProxy
from .xpath_token import XPathToken
from .xpath_nodes import NamespaceNode, TypedAttribute, TypedElement, is_etree_element, \
    is_xpath_node, is_element_node, is_document_node, is_attribute_node, is_text_node, \
    is_comment_node, is_processing_instruction_node, node_name

OPERATORS_MAP = {
    '=': operator.eq,
    '!=': operator.ne,
    '>': operator.gt,
    '>=': operator.ge,
    '<': operator.lt,
    '<=': operator.le,
}


class XPath1Parser(Parser):
    """
    XPath 1.0 expression parser class. Provide a *namespaces* dictionary argument for
    mapping namespace prefixes to URI inside expressions. If *strict* is set to `False`
    the parser enables also the parsing of QNames, like the ElementPath library.

    :param namespaces: a dictionary with mapping from namespace prefixes into URIs.
    :param strict: a strict mode is `False` the parser enables parsing of QNames \
    in extended format, like the Python's ElementPath library. Default is `True`.
    """
    version = '1.0'
    """The XPath version string."""

    token_base_class = XPathToken
    literals_pattern = re.compile(
        r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|(?:\d+|\.\d+)(?:\.\d*)?(?:[Ee][+-]?\d+)?"""
    )
    name_pattern = re.compile(r'[^\d\W][\w.\-\xb7\u0300-\u036F\u203F\u2040]*')

    SYMBOLS = Parser.SYMBOLS | {
        # Axes
        'descendant-or-self', 'following-sibling', 'preceding-sibling',
        'ancestor-or-self', 'descendant', 'attribute', 'following',
        'namespace', 'preceding', 'ancestor', 'parent', 'child', 'self',

        # Operators
        'and', 'mod', 'div', 'or', '..', '//', '!=', '<=', '>=', '(', ')', '[', ']',
        ':', '.', '@', ',', '/', '|', '*', '-', '=', '+', '<', '>', '$', '::',

        # Node test functions
        'node', 'text', 'comment', 'processing-instruction',

        # Node set functions
        'last', 'position', 'count', 'id', 'name', 'local-name', 'namespace-uri',

        # String functions
        'string', 'concat', 'starts-with', 'contains',
        'substring-before', 'substring-after', 'substring',
        'string-length', 'normalize-space', 'translate',

        # Boolean functions
        'boolean', 'not', 'true', 'false', 'lang',

        # Number functions
        'number', 'sum', 'floor', 'ceiling', 'round',

        # Symbols for ElementPath extensions
        '{', '}'
    }

    DEFAULT_NAMESPACES = {'xml': XML_NAMESPACE}
    """
    The default prefix-to-namespace associations of the XPath class. These namespaces
    are updated in the instance with the ones passed with the *namespaces* argument.
    """

    # Labels and symbols admitted after a path step
    PATH_STEP_LABELS = ('axis', 'kind test')
    PATH_STEP_SYMBOLS = {
        '(integer)', '(string)', '(float)', '(decimal)', '(name)', '*', '@', '..', '.', '{'
    }

    variables = None  # XPath 1.0 doesn't have static context's in-scope variables
    schema = None     # XPath 1.0 doesn't have schema bindings

    def __init__(self, namespaces=None, strict=True, *args, **kwargs):
        super(XPath1Parser, self).__init__()
        self.namespaces = self.DEFAULT_NAMESPACES.copy()
        if namespaces is not None:
            self.namespaces.update(namespaces)
        self.strict = strict

    @property
    def compatibility_mode(self):
        """XPath 1.0 compatibility mode."""
        return True

    @property
    def default_namespace(self):
        """
        The default namespace. For XPath 1.0 this value is always `None` because the default
        namespace is ignored (see https://www.w3.org/TR/1999/REC-xpath-19991116/#node-tests).
        """
        return

    def get_qname(self, namespace, local_name):
        if ':' in local_name:
            return local_name

        for pfx, uri in self.namespaces.items():
            if uri == namespace:
                if pfx:
                    return '%s:%s' % (pfx, local_name)
                return local_name
        else:
            return local_name

    @staticmethod
    def unescape(string_literal):
        if string_literal.startswith("'"):
            return string_literal[1:-1].replace("''", "'")
        else:
            return string_literal[1:-1].replace('""', '"')

    @classmethod
    def axis(cls, symbol, bp=80):
        """Register a token for a symbol that represents an XPath *axis*."""
        def nud_(self):
            self.parser.advance('::')
            self.parser.next_token.expected(
                '(name)', '*', 'text', 'node', 'document-node',
                'comment', 'processing-instruction', 'attribute',
                'schema-attribute', 'element', 'schema-element'
            )
            self[:] = self.parser.expression(rbp=bp),
            return self

        pattern = r'\b%s(?=\s*\:\:|\s*\(\:.*\:\)\s*\:\:)' % symbol
        return cls.register(symbol, pattern=pattern, label='axis', lbp=bp, rbp=bp, nud=nud_)

    @classmethod
    def function(cls, symbol, nargs=None, label='function', bp=90):
        """
        Registers a token class for a symbol that represents an XPath *callable* object.
        For default a callable labeled as *function* is registered but a different label
        can be provided.
        """
        def nud_(self):
            code = 'XPST0017' if self.label == 'function' else 'XPST0003'
            self.value = None
            self.parser.advance('(')
            if nargs is None:
                del self[:]
                if self.parser.next_token.symbol == ')':
                    raise self.error(code, 'at least an argument is required')
                while True:
                    self.append(self.parser.expression(5))
                    if self.parser.next_token.symbol != ',':
                        break
                    self.parser.advance()
                self.parser.advance(')')
                return self
            elif nargs == 0:
                if self.parser.next_token.symbol != ')':
                    raise self.error(code, '%s has no arguments' % str(self))
                self.parser.advance()
                return self
            elif isinstance(nargs, (tuple, list)):
                min_args, max_args = nargs
            else:
                min_args = max_args = nargs

            k = 0
            while k < min_args:
                if self.parser.next_token.symbol == ')':
                    msg = 'Too few arguments: expected at least %s arguments' % min_args
                    raise self.wrong_nargs(msg if min_args > 1 else msg[:-1])

                self[k:] = self.parser.expression(5),
                k += 1
                if k < min_args:
                    if self.parser.next_token.symbol == ')':
                        msg = 'Too few arguments: expected at least %s arguments' % min_args
                        raise self.error(code, msg if min_args > 1 else msg[:-1])
                    self.parser.advance(',')

            while k < max_args:
                if self.parser.next_token.symbol == ',':
                    self.parser.advance(',')
                    self[k:] = self.parser.expression(5),
                elif k == 0 and self.parser.next_token.symbol != ')':
                    self[k:] = self.parser.expression(5),
                else:
                    break
                k += 1

            if self.parser.next_token.symbol == ',':
                msg = 'Too many arguments: expected at most %s arguments' % max_args
                raise self.error(code, msg if max_args > 1 else msg[:-1])

            self.parser.advance(')')
            return self

        pattern = r'\b%s(?=\s*\(|\s*\(\:.*\:\)\()' % symbol
        return cls.register(symbol, pattern=pattern, label=label, lbp=bp, rbp=bp, nud=nud_)

    def parse(self, source):
        root_token = super(XPath1Parser, self).parse(source)
        try:
            root_token.evaluate()  # Static context evaluation
        except MissingContextError:
            pass
        return root_token

    def expected_name(self, *symbols, message=None):
        """
        Checks the next symbol with a list of symbols. Replaces the next token
        with a '(name)' token if check fails and the symbol can be also a name.
        Otherwise raises a syntax error.

        :param symbols: a sequence of symbols.
        :param message: optional error message.
        """
        if self.next_token.symbol in symbols:
            return
        elif self.next_token.label == 'operator' and \
                self.name_pattern.match(self.next_token.symbol) is not None:
            self.next_token = self.symbol_table['(name)'](self, self.next_token.symbol)
        else:
            raise self.next_token.wrong_syntax(message)


##
# XPath1 definitions
register = XPath1Parser.register
literal = XPath1Parser.literal
nullary = XPath1Parser.nullary
prefix = XPath1Parser.prefix
infix = XPath1Parser.infix
postfix = XPath1Parser.postfix
method = XPath1Parser.method
function = XPath1Parser.function
axis = XPath1Parser.axis


###
# Simple symbols
register(',')
register(')', bp=100)
register(']')
register('::')
register('}')


###
# Literals
literal('(string)')
literal('(float)')
literal('(decimal)')
literal('(integer)')
literal('(invalid)')
literal('(unknown)')


@method(register('(name)', bp=10, label='literal'))
def nud(self):
    if self.parser.next_token.symbol == '(':
        if self.namespace == XSD_NAMESPACE:
            raise self.error('XPST0017', 'unknown constructor function {!r}'.format(self.value))
        raise self.error('XPST0017', 'unknown function {!r}'.format(self.value))
    elif self.parser.next_token.symbol == '::':
        raise self.missing_axis("axis '%s::' not found" % self.value)
    return self


@method('(name)')
def evaluate(self, context=None):
    return [x for x in self.select(context)]


@method('(name)')
def select(self, context=None):
    if context is None:
        raise self.missing_context()

    name = self.value

    if isinstance(context, XPathSchemaContext):
        # Bind with the XSD type from a schema
        for item in map(lambda x: self.match_xsd_type(x, name), context.iter_children_or_self()):
            if item:
                yield item
        return

    if name[0] == '{' or not self.parser.default_namespace:
        tag = name
    else:
        tag = '{%s}%s' % (self.parser.default_namespace, name)

    # With an ElementTree context checks if the token is bound to an XSD type. If not
    # try a match using the element path. If this match fails the xsd_type attribute
    # is set with the schema object to prevent other checks until the schema change.
    if self.xsd_types is self.parser.schema:

        # Untyped selection
        for item in context.iter_children_or_self():
            if is_attribute_node(item, name) or is_element_node(item, tag):
                yield item

    elif self.xsd_types is None or isinstance(self.xsd_types, AbstractSchemaProxy):

        # Try to match the type using the path
        for item in context.iter_children_or_self():
            if is_attribute_node(item, name) or is_element_node(item, tag):
                path = context.get_path(item)

                xsd_component = self.parser.schema.find(path, self.parser.namespaces)
                if xsd_component is not None:
                    self.xsd_types = {tag: xsd_component.type}
                else:
                    self.xsd_types = self.parser.schema

                yield self.get_typed_node(item)
    else:
        # XSD typed selection
        for item in context.iter_children_or_self():
            if is_attribute_node(item, name) or is_element_node(item, tag):
                yield self.get_typed_node(item)


###
# Namespace prefix reference
@method(':', bp=95)
def led(self, left):
    if self.parser.version == '1.0':
        left.expected('(name)')
    else:
        left.expected('(name)', '*')

    if self.parser.next_token.label not in ('function', 'constructor'):
        self.parser.expected_name('(name)', '*')
    if self.parser.is_spaced():
        raise self.wrong_syntax("a QName cannot contains spaces before or after ':'")

    if left.symbol == '(name)':
        try:
            namespace = self.get_namespace(left.value)
        except ElementPathKeyError:
            msg = "prefix {!r} is not declared".format(left.value)
            raise self.error('XPST0081', msg) from None
        else:
            self.parser.next_token.bind_namespace(namespace)
    elif self.parser.next_token.symbol != '(name)':
        raise self.wrong_syntax()

    self[:] = left, self.parser.expression(90)
    self.value = '{}:{}'.format(self[0].value, self[1].value)

    if self[1].symbol == ':':
        raise self.wrong_syntax('{!r} is not a QName'.format(self.source))
    return self


@method(':')
def evaluate(self, context=None):
    if self[1].label in ('function', 'constructor'):
        return self[1].evaluate(context)
    return [x for x in self.select(context)]


@method(':')
def select(self, context=None):
    if self[1].label in ('function', 'constructor'):
        value = self[1].evaluate(context)
        if isinstance(value, list):
            yield from value
        else:
            yield value
        return

    if self[0].value == '*':
        name = '*:%s' % self[1].value
    else:
        try:
            namespace = self.get_namespace(self[0].value)
        except ElementPathKeyError:
            msg = "prefix {!r} has not been declared".format(self[0].value)
            raise self.error('XPST0081', msg) from None
        else:
            name = '{%s}%s' % (namespace, self[1].value)

    if context is None:
        yield name
    elif isinstance(context, XPathSchemaContext):
        for item in map(lambda x: self.match_xsd_type(x, name), context.iter_children_or_self()):
            if item:
                yield item

    elif self.xsd_types is self.parser.schema:
        for item in context.iter_children_or_self():
            if is_attribute_node(item, name) or is_element_node(item, name):
                yield item

    elif self.xsd_types is None or isinstance(self.xsd_types, AbstractSchemaProxy):
        for item in context.iter_children_or_self():
            if is_attribute_node(item, name) or is_element_node(item, name):
                path = context.get_path(item)
                xsd_component = self.parser.schema.find(path, self.parser.namespaces)
                if xsd_component is not None:
                    self.add_xsd_type(xsd_component.name, xsd_component.type)
                else:
                    self.xsd_types = self.parser.schema
                yield self.get_typed_node(item)

    else:
        # XSD typed selection
        for item in context.iter_children_or_self():
            if is_attribute_node(item, name) or is_element_node(item, name):
                yield self.get_typed_node(item)


###
# Namespace URI as in ElementPath
@method('{', bp=95)
def nud(self):
    if self.parser.strict:
        raise self.wrong_syntax("not allowed symbol if parser has strict=True")

    namespace = self.parser.next_token.value + self.parser.advance_until('}')
    self.parser.advance()
    if self.parser.next_token.label not in ('function', 'constructor'):
        self.parser.expected_name('(name)', '*')
    self.parser.next_token.bind_namespace(namespace)

    self[:] = self.parser.symbol_table['(string)'](self.parser, namespace), \
        self.parser.expression(90)
    return self


@method('{')
def evaluate(self, context=None):
    if self[1].label == 'function':
        return self[1].evaluate(context)
    else:
        return '{%s}%s' % (self[0].value, self[1].value)


@method('{')
def select(self, context=None):
    if self[1].label == 'function':
        yield self[1].evaluate(context)
    elif context is None:
        raise self.missing_context()
    else:
        value = '{%s}%s' % (self[0].value, self[1].value)
        for item in context.iter_children_or_self():
            if is_attribute_node(item, value):
                yield item[1]
            elif is_element_node(item, value):
                yield item


###
# Variables
@method('$', bp=90)
def nud(self):
    self.parser.expected_name('(name)')
    self[:] = self.parser.expression(rbp=90),
    if ':' in self[0].value:
        raise self[0].wrong_syntax("variable reference requires a simple reference name")
    return self


@method('$')
def evaluate(self, context=None):
    if context is None:
        raise self.missing_context()

    try:
        return context.variable_values[self[0].value]
    except KeyError as err:
        raise self.missing_name('unknown variable %r' % str(err)) from None


###
# Nullary operators (use only the context)
@method(nullary('*'))
def select(self, context=None):
    if self:
        # Product operator
        item = self.evaluate(context)
        if item is not None:
            if context is not None:
                context.item = item
            yield item
    elif context is None:
        raise self.missing_context()
    else:
        # Wildcard literal
        for item in context.iter_children_or_self():
            if context.is_principal_node_kind():
                if hasattr(item, 'type'):
                    self.add_xsd_type(item.name, item.type)
                if is_attribute_node(item):
                    yield item[1]
                else:
                    yield item


@method(nullary('.'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()

    for item in context.iter_self():
        if item is not None:
            if hasattr(item, 'type') and isinstance(context, XPathSchemaContext):
                self.add_xsd_type(item.name, item.type)
            yield item
        elif is_document_node(context.root):
            yield context.root


@method(nullary('..'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        parent = context.get_parent(context.item)
        if is_element_node(parent):
            context.item = parent
            yield parent


###
# Logical Operators
@method(infix('or', bp=20))
def evaluate(self, context=None):
    return self.boolean_value(self[0].evaluate(copy(context))) or \
        self.boolean_value(self[1].evaluate(copy(context)))


@method(infix('and', bp=25))
def evaluate(self, context=None):
    return self.boolean_value(self[0].evaluate(copy(context))) and \
        self.boolean_value(self[1].evaluate(copy(context)))


###
# Comparison operators
@method('=', bp=30)
@method('!=', bp=30)
@method('<', bp=30)
@method('>', bp=30)
@method('<=', bp=30)
@method('>=', bp=30)
def led(self, left):
    if left.symbol in OPERATORS_MAP:
        raise self.wrong_syntax()
    self[:] = left, self.parser.expression(rbp=30)
    return self


@method('=')
@method('!=')
@method('<')
@method('>')
@method('<=')
@method('>=')
def evaluate(self, context=None):
    op = OPERATORS_MAP[self.symbol]
    try:
        if self.parser.version == '1.0':
            return any(op(x1, x2) for x1, x2 in self.get_comparison_data(context))

        for operands in self.get_comparison_data(context):
            if any(isinstance(x, int) for x in operands) and \
                    any(isinstance(x, str) for x in operands):
                raise TypeError("cannot compare {!r} and {!r}")
            if op(*operands):
                return True
        return False
    except TypeError as err:
        raise self.error('XPTY0004', str(err))
    except ValueError as err:
        raise self.error('FORG0001', str(err))


###
# Numerical operators
prefix('+', bp=40)
prefix('-', bp=70)


@method(infix('+', bp=40))
def evaluate(self, context=None):
    if len(self) == 1:
        arg = self.get_argument(context, cls=NumericProxy)
        if arg is not None:
            return +arg
    else:
        op1, op2 = self.get_operands(context, cls=ArithmeticProxy)
        if op1 is not None:
            try:
                return op1 + op2
            except ValueError as err:
                raise self.error('FORG0001', str(err)) from None
            except TypeError as err:
                raise self.error('XPTY0004', str(err))
            except OverflowError as err:
                if isinstance(op1, AbstractDateTime):
                    raise self.error('FODT0001', str(err))
                elif isinstance(op1, Duration):
                    raise self.error('FODT0002', str(err))
                else:
                    raise self.error('FOAR0002', str(err))


@method(infix('-', bp=40))
def evaluate(self, context=None):
    if len(self) == 1:
        arg = self.get_argument(context, cls=NumericProxy)
        if arg is not None:
            return -arg
    else:
        op1, op2 = self.get_operands(context, cls=ArithmeticProxy)
        if op1 is not None:
            try:
                return op1 - op2
            except TypeError as err:
                raise self.error('XPTY0004', str(err)) from None
            except OverflowError as err:
                if isinstance(op1, AbstractDateTime):
                    raise self.error('FODT0001', str(err))
                elif isinstance(op1, Duration):
                    raise self.error('FODT0002', str(err))
                else:
                    raise self.error('FOAR0002', str(err))


@method(infix('*', bp=45))
def evaluate(self, context=None):
    if self:
        op1, op2 = self.get_operands(context, cls=ArithmeticProxy)
        if op1 is not None:
            try:
                if isinstance(op2, (YearMonthDuration, DayTimeDuration)):
                    return op2 * op1
                return op1 * op2
            except TypeError as err:
                if isinstance(op1, float):
                    if math.isnan(op1):
                        raise self.error('FOCA0005', str(err)) from None
                    elif math.isinf(op1):
                        raise self.error('FODT0002', str(err)) from None

                if isinstance(op2, float):
                    if math.isnan(op2):
                        raise self.error('FOCA0005', str(err)) from None
                    elif math.isinf(op2):
                        raise self.error('FODT0002', str(err)) from None

                raise self.error('XPTY0004', str(err)) from None
            except ValueError as err:
                raise self.error('FOCA0005', str(err)) from None
            except OverflowError as err:
                if isinstance(op1, AbstractDateTime):
                    raise self.error('FODT0001', str(err)) from None
                elif isinstance(op1, Duration):
                    raise self.error('FODT0002', str(err)) from None
                else:
                    raise self.error('FOAR0002', str(err)) from None
    else:
        # This is not a multiplication operator but a wildcard select statement
        return [x for x in self.select(context)]


@method(infix('div', bp=45))
def evaluate(self, context=None):
    dividend, divisor = self.get_operands(context, cls=ArithmeticProxy)
    if dividend is None:
        return
    elif divisor != 0:
        try:
            if isinstance(dividend, int) and isinstance(divisor, int):
                return decimal.Decimal(dividend) / decimal.Decimal(divisor)
            return dividend / divisor
        except TypeError as err:
            raise self.error('XPTY0004', str(err)) from None
        except ValueError as err:
            raise self.error('FOCA0005', str(err)) from None
        except OverflowError as err:
            raise self.error('FOAR0002', str(err)) from None
        except (ZeroDivisionError, decimal.DivisionByZero):
            if isinstance(dividend, AbstractDateTime):
                raise self.error('FODT0001') from None
            elif isinstance(dividend, Duration):
                raise self.error('FODT0002') from None
            raise self.error('FOAR0001') from None

    elif isinstance(dividend, AbstractDateTime):
        raise self.error('FODT0001')
    elif isinstance(dividend, Duration):
        raise self.error('FODT0002')
    elif not self.parser.compatibility_mode and \
            isinstance(dividend, (int, decimal.Decimal)) and \
            isinstance(divisor, (int, decimal.Decimal)):
        raise self.error('FOAR0001')
    elif dividend == 0:
        return float('nan')
    elif dividend > 0:
        return float('-inf') if str(divisor).startswith('-') else float('inf')
    else:
        return float('inf') if str(divisor).startswith('-') else float('-inf')


@method(infix('mod', bp=45))
def evaluate(self, context=None):
    op1, op2 = self.get_operands(context, cls=NumericProxy)
    if op1 is not None:
        if op2 == 0 and isinstance(op2, float):
            return float('nan')
        elif math.isinf(op2) and not math.isinf(op1) and op1 != 0:
            return op1 if self.parser.version != '1.0' else float('nan')

        try:
            if isinstance(op1, int) and isinstance(op2, int):
                return op1 % op2 if op1 * op2 >= 0 else -(abs(op1) % op2)
            return op1 % op2
        except TypeError as err:
            raise self.wrong_type(str(err)) from None
        except (ZeroDivisionError, decimal.InvalidOperation):
            raise self.error('FOAR0001') from None


###
# Union expressions
@method('|', bp=50)
def led(self, left):
    self.cut_and_sort = True
    if left.symbol in {'|', 'union'}:
        left.cut_and_sort = False
    self[:] = left, self.parser.expression(rbp=50)
    return self


@method('|')
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    elif not self.cut_and_sort:
        for k in range(2):
            yield from self[k].select(context.copy())
    else:
        results = {item for k in range(2) for item in self[k].select(context.copy())}
        yield from context.iter_results(results)


###
# Path expressions
@method('//', bp=75)
def nud(self):
    if self.parser.next_token.label not in self.parser.PATH_STEP_LABELS:
        self.parser.expected_name(*self.parser.PATH_STEP_SYMBOLS)

    self[:] = self.parser.expression(75),
    return self


@method('/', bp=75)
def nud(self):
    if self.parser.next_token.label not in self.parser.PATH_STEP_LABELS:
        try:
            self.parser.expected_name(*self.parser.PATH_STEP_SYMBOLS)
        except SyntaxError:
            return self

    self[:] = self.parser.expression(75),
    return self


@method('//')
@method('/')
def led(self, left):
    if self.parser.next_token.label not in self.parser.PATH_STEP_LABELS:
        self.parser.expected_name(*self.parser.PATH_STEP_SYMBOLS)

    self[:] = left, self.parser.expression(75)
    return self


@method('/')
def select(self, context=None):
    """
    Child path expression. Selects child:: axis as default (when bind to '*' or '(name)').
    """
    if context is None:
        raise self.missing_context()
    elif not self:
        if is_document_node(context.root):
            yield context.root
    elif len(self) == 1:
        if is_document_node(context.root) or context.item is context.root:
            context.item = None
            yield from self[0].select(context)
    else:
        items = []
        for _ in context.iter_selector(self[0].select):
            if not is_xpath_node(context.item):
                raise self.error('XPTY0019')

            for result in self[1].select(context):
                if not is_etree_element(result) and not isinstance(result, tuple):
                    yield result
                elif result in items:
                    pass
                elif isinstance(result, (TypedAttribute, TypedElement)):
                    if result[0] not in items:
                        items.append(result)
                        yield result
                else:
                    items.append(result)
                    yield result
                    if isinstance(context, XPathSchemaContext):
                        if isinstance(result, tuple):
                            self[1].add_xsd_type(result[0], result[1].type)
                        elif hasattr(result, 'type'):
                            self[1].add_xsd_type(result.tag, result.type)


@method('//')
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    elif len(self) == 1:
        if is_document_node(context.root) or context.item is context.root:
            context.item = None
            for _ in context.iter_descendants(axis='descendant-or-self'):
                yield from self[0].select(context)
    else:
        for elem in self[0].select(context):
            if not is_element_node(elem):
                raise self.wrong_type("left operand must returns element nodes: %r" % elem)
            for _ in context.iter_descendants(item=elem):
                yield from self[1].select(context)


###
# Predicate filters
@method('[', bp=80)
def led(self, left):
    self[:] = left, self.parser.expression()
    self.parser.advance(']')
    return self


@method('[')
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    elif self[0].label == 'axis':
        selector = self[0].select(context)
    else:
        selector = context.iter_selector(self[0].select)

    for context.item in selector:
        predicate = [x for x in self[1].select(context.copy())]
        if len(predicate) == 1 and isinstance(predicate[0], NumericProxy):
            if context.position == predicate[0]:
                yield context.item
        elif self.boolean_value(predicate):
            yield context.item


###
# Parenthesized expressions
@method('(', bp=100)
def nud(self):
    self[:] = self.parser.expression(),
    self.parser.advance(')')
    return self


@method('(')
def evaluate(self, context=None):
    return self[0].evaluate(context)


@method('(')
def select(self, context=None):
    return self[0].select(context)


###
# Axes
@method('@', bp=80)
def nud(self):
    self.parser.expected_name('*', '(name)', ':', message="invalid attribute specification")
    self[:] = self.parser.expression(rbp=80),
    return self


@method('@')
@method(axis('attribute'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()

    for _ in context.iter_attributes():
        yield from self[0].select(context)


@method(axis('namespace'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    elif is_element_node(context.item):
        elem = context.item
        namespaces = self.parser.namespaces

        for prefix_, uri in namespaces.items():
            context.item = NamespaceNode(prefix_, uri)
            yield context.item

        if hasattr(elem, 'nsmap'):
            # Add element's namespaces for lxml (and use None for default namespace)
            # noinspection PyUnresolvedReferences
            for prefix_, uri in elem.nsmap.items():
                if prefix_ not in namespaces:
                    context.item = NamespaceNode(prefix_, uri)
                    yield context.item


@method(axis('self'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for _ in context.iter_self():
            yield from self[0].select(context)


@method(axis('child'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for _ in context.iter_children_or_self(child_axis=True):
            yield from self[0].select(context)


@method(axis('parent'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for _ in context.iter_parent():
            yield from self[0].select(context)


@method(axis('following-sibling'))
@method(axis('preceding-sibling'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for _ in context.iter_siblings(axis=self.symbol):
            yield from self[0].select(context)


@method(axis('ancestor'))
@method(axis('ancestor-or-self'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for _ in context.iter_ancestors(axis=self.symbol):
            yield from self[0].select(context)


@method(axis('descendant'))
@method(axis('descendant-or-self'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for _ in context.iter_descendants(axis=self.symbol):
            yield from self[0].select(context)


@method(axis('following'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for _ in context.iter_followings():
            yield from self[0].select(context)


@method(axis('preceding'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    elif is_element_node(context.item):
        for _ in context.iter_preceding():
            yield from self[0].select(context)


###
# Kind tests (for matching of node types in XPath 1.0 or sequence types in XPath 2.0)
@method(function('node', nargs=0, label='kind test'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for item in context.iter_children_or_self():
            if item is None:
                yield context.root
            elif is_xpath_node(item):
                yield item


@method(function('processing-instruction', nargs=(0, 1), label='kind test'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    elif is_processing_instruction_node(context.item):
        yield context.item


@method(function('comment', nargs=0, label='kind test'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    elif is_comment_node(context.item):
        yield context.item


@method(function('text', nargs=0, label='kind test'))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        for item in context.iter_children_or_self():
            if is_text_node(item):
                yield item


###
# Node set functions
@method(function('last', nargs=0))
def evaluate(self, context=None):
    return context.size if context is not None else 0


@method(function('position', nargs=0))
def evaluate(self, context=None):
    return context.position if context is not None else 0


@method(function('count', nargs=1))
def evaluate(self, context=None):
    return len([x for x in self[0].select(context)])


@method(function('id', nargs=1))
def select(self, context=None):
    if context is None:
        raise self.missing_context()
    else:
        value = self[0].evaluate(context)
        item = context.item
        if is_element_node(item):
            yield from filter(lambda e: e.get(XML_ID) == value, item.iter())


@method(function('name', nargs=(0, 1)))
@method(function('local-name', nargs=(0, 1)))
@method(function('namespace-uri', nargs=(0, 1)))
def evaluate(self, context=None):
    name = node_name(self.get_argument(context, default_to_context=True))
    if name is None:
        return ''

    symbol = self.symbol
    if symbol == 'name':
        return get_prefixed_name(name, self.parser.namespaces)
    elif not name or name[0] != '{':
        return name if symbol == 'local-name' else ''
    elif symbol == 'local-name':
        return name.split('}')[1]
    elif symbol == 'namespace-uri':
        return name.split('}')[0][1:]


###
# String functions
@method(function('string', nargs=(0, 1)))
def evaluate(self, context=None):
    if not self:
        if context is None:
            raise self.missing_context()
        return self.string_value(context.item)
    return self.string_value(self.get_argument(context))


@method(function('contains', nargs=2))
def evaluate(self, context=None):
    arg1 = self.get_argument(context, default='', cls=str)
    arg2 = self.get_argument(context, index=1, default='', cls=str)
    return arg2 in arg1


@method(function('concat'))
def evaluate(self, context=None):
    return ''.join(self.string_value(self.get_argument(context, index=k))
                   for k in range(len(self)))


@method(function('string-length', nargs=(0, 1)))
def evaluate(self, context=None):
    return len(self.get_argument(context, default_to_context=True, default='', cls=str))


@method(function('normalize-space', nargs=(0, 1)))
def evaluate(self, context=None):
    if self.parser.version == '1.0':
        arg = self.string_value(self.get_argument(context, default_to_context=True, default=''))
    else:
        arg = self.get_argument(context, default_to_context=True, default='', cls=str)
    return ' '.join(arg.strip().split())


@method(function('starts-with', nargs=2))
def evaluate(self, context=None):
    arg1 = self.get_argument(context, default='', cls=str)
    arg2 = self.get_argument(context, index=1, default='', cls=str)
    return arg1.startswith(arg2)


@method(function('translate', nargs=3))
def evaluate(self, context=None):
    arg = self.get_argument(context, default='', cls=str)
    map_string = self.get_argument(context, index=1, default='', cls=str)
    trans_string = self.get_argument(context, index=2, default='', cls=str)

    if len(map_string) == len(trans_string):
        return arg.translate(str.maketrans(map_string, trans_string))
    elif len(map_string) > len(trans_string):
        k = len(trans_string)
        return arg.translate(str.maketrans(map_string[:k], trans_string, map_string[k:]))
    else:
        return arg.translate(str.maketrans(map_string, trans_string[:len(map_string)]))


@method(function('substring', nargs=(2, 3)))
def evaluate(self, context=None):
    item = self.get_argument(context, default='', cls=str)
    start = self.get_argument(context, index=1)
    try:
        if math.isnan(start) or math.isinf(start):
            return ''
    except TypeError:
        raise self.wrong_type("the second argument must be xs:numeric") from None
    else:
        start = int(round(start)) - 1

    if len(self) == 2:
        return '' if item is None else item[max(start, 0):]
    else:
        length = self.get_argument(context, index=2)
        try:
            if math.isnan(length) or length <= 0:
                return ''
        except TypeError:
            raise self.wrong_type("the third argument must be xs:numeric") from None

        if item is None:
            return ''
        elif math.isinf(length):
            return item[max(start, 0):]
        else:
            stop = start + int(round(length))
            return '' if item is None else item[slice(max(start, 0), max(stop, 0))]


@method(function('substring-before', nargs=2))
@method(function('substring-after', nargs=2))
def evaluate(self, context=None):
    arg1 = self.get_argument(context, default='', cls=str)
    arg2 = self.get_argument(context, index=1, default='', cls=str)
    if arg1 is None:
        return ''

    try:
        index = arg1.find(arg2)
    except AttributeError:
        raise self.wrong_type("the first argument must be a string") from None
    except TypeError:
        raise self.wrong_type("the second argument must be a string") from None

    if index < 0:
        return ''
    if self.symbol == 'substring-before':
        return arg1[:index]
    else:
        return arg1[index + len(arg2):]


###
# Boolean functions
@method(function('boolean', nargs=1))
def evaluate(self, context=None):
    return self.boolean_value([x for x in self[0].select(context)])


@method(function('not', nargs=1))
def evaluate(self, context=None):
    return not self.boolean_value([x for x in self[0].select(context)])


@method(function('true', nargs=0))
def evaluate(self, context=None):
    return True


@method(function('false', nargs=0))
def evaluate(self, context=None):
    return False


@method(function('lang', nargs=1))
def evaluate(self, context=None):
    if context is None:
        raise self.missing_context()
    elif not is_element_node(context.item):
        return False
    else:
        try:
            lang = context.item.attrib[XML_LANG].strip()
        except KeyError:
            for elem in context.iter_ancestor():
                if XML_LANG in elem.attrib:
                    lang = elem.attrib[XML_LANG]
                    break
            else:
                return False

        if '-' in lang:
            lang, _ = lang.split('-')
        return lang.lower() == self[0].evaluate().lower()


###
# Number functions
@method(function('number', nargs=(0, 1)))
def evaluate(self, context=None):
    arg = self.get_argument(context, default_to_context=True)
    try:
        return float(self.string_value(arg) if is_xpath_node(arg) else arg)
    except (TypeError, ValueError):
        return float('nan')


@method(function('sum', nargs=(1, 2)))
def evaluate(self, context=None):
    values = [x[-1] if isinstance(x, tuple) else x for x in self[0].select(context)]

    if not values:
        zero = 0 if len(self) == 1 else self.get_argument(context, index=1)
        return [] if zero is None else zero
    elif any(isinstance(x, float) and math.isnan(x) for x in values):
        return float('nan')

    if all(isinstance(x, (decimal.Decimal, int)) for x in values):
        return sum(values)
    elif all(isinstance(x, DayTimeDuration) for x in values) or \
            all(isinstance(x, YearMonthDuration) for x in values):
        return sum(values[1:], start=values[0])

    try:
        return sum(self.number_value(x) for x in values)
    except TypeError:
        if self.parser.version == '1.0':
            return float('nan')
        raise self.error('FORG0006') from None


@method(function('ceiling', nargs=1))
@method(function('floor', nargs=1))
def evaluate(self, context=None):
    arg = self.get_argument(context)
    if arg is None:
        return float('nan') if self.parser.version == '1.0' else []
    elif is_xpath_node(arg) or self.parser.compatibility_mode:
        arg = self.number_value(arg)

    if isinstance(arg, float) and (math.isnan(arg) or math.isinf(arg)):
        return arg

    try:
        return math.floor(arg) if self.symbol == 'floor' else math.ceil(arg)
    except TypeError as err:
        raise self.wrong_type(str(err)) from None


@method(function('round', nargs=1))
def evaluate(self, context=None):
    arg = self.get_argument(context)
    if arg is None:
        return float('nan') if self.parser.version == '1.0' else []
    elif is_xpath_node(arg) or self.parser.compatibility_mode:
        arg = self.number_value(arg)

    if isinstance(arg, float) and (math.isnan(arg) or math.isinf(arg)):
        return arg

    try:
        number = decimal.Decimal(arg)
        if number > 0:
            return number.quantize(decimal.Decimal('1'), rounding='ROUND_HALF_UP')
        else:
            return number.quantize(decimal.Decimal('1'), rounding='ROUND_HALF_DOWN')
    except TypeError as err:
        raise self.wrong_type(str(err)) from None
    except decimal.DecimalException as err:
        raise self.wrong_value(str(err)) from None


register('(end)')
XPath1Parser.build()
