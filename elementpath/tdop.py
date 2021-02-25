#
# Copyright (c), 2018-2020, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
"""
This module contains base classes and helper functions for defining Pratt parsers.
"""
import sys
import re
from unicodedata import name as unicode_name
from decimal import Decimal, DecimalException
from itertools import takewhile
from abc import ABCMeta
from collections.abc import MutableSequence


#
# Simple top down parser based on Vaughan Pratt's algorithm (Top Down Operator Precedence).
#
# References:
#
#   https://tdop.github.io/  (Vaughan R. Pratt's "Top Down Operator Precedence" - 1973)
#   http://crockford.com/javascript/tdop/tdop.html  (Douglas Crockford - 2007)
#   http://effbot.org/zone/simple-top-down-parsing.htm (Fredrik Lundh - 2008)
#
# This implementation is based on a base class for tokens and a base class for parsers.
# A real parser is built with a derivation of the base parser class followed by the
# registrations of token classes for the symbols of the language.
#
# A parser can be extended by derivation, copying the reusable token classes and
# defining the additional ones. See the files xpath1_parser.py and xpath2_parser.py
# for a fully implementation example of a real parser.
#

# Parser special symbols set, that includes the special symbols of TDOP plus two
# additional special symbols for managing invalid literals and unknown symbols.
SPECIAL_SYMBOLS = frozenset((
    '(string)', '(float)', '(decimal)', '(integer)',
    '(name)', '(end)', '(invalid)', '(unknown)'
))

SPACE_PATTERN = re.compile(r'\s')


class ParseError(SyntaxError):
    """An error when parsing source with TDOP parser."""


def count_leading_spaces(s):
    return sum(1 for _ in takewhile(str.isspace, s))


def symbol_to_classname(symbol):
    """
    Converts a symbol string to an identifier (only alphanumeric and '_').
    """
    def get_id_name(c):
        if c.isalnum() or c == '_':
            return c
        else:
            return '%s_' % unicode_name(str(c)).title()

    if symbol.isalnum():
        return symbol.title()
    elif symbol in SPECIAL_SYMBOLS:
        return symbol[1:-1].title()
    elif all(c in '-_' for c in symbol):
        value = ' '.join(unicode_name(c) for c in symbol)
        return value.title().replace(' ', '').replace('-', '').replace('_', '')

    value = symbol.replace('-', '_')
    if value.isidentifier():
        return value.title().replace('_', '')

    value = ''.join(get_id_name(c) for c in symbol)
    return value.replace(' ', '').replace('-', '').replace('_', '')


class MultiLabel(object):
    """
    Helper class for defining multi-value label for tokens. Useful when a symbol has more roles.
    A label of this type has equivalence with each of its values.

    Example:
        label = MultiLabel('function', 'operator')
        label == 'symbol'    # False
        label == 'function'  # True
        label == 'operator'  # True
    """
    def __init__(self, *values):
        self.values = values

    def __eq__(self, other):
        return any(other == v for v in self.values)

    def __ne__(self, other):
        return all(other != v for v in self.values)

    def __repr__(self):
        return '%s%s' % (self.__class__.__name__, self.values)

    def __str__(self):
        return '__'.join(self.values).replace(' ', '_')

    def __hash__(self):
        return hash(self.values)

    def __contains__(self, item):
        return any(item in v for v in self.values)

    def startswith(self, s):
        return any(v.startswith(s) for v in self.values)

    def endswith(self, s):
        return any(v.endswith(s) for v in self.values)


class Token(MutableSequence):
    """
    Token base class for defining a parser based on Pratt's method.

    Each token instance is a list-like object. The number of token's items is
    the arity of the represented operator, where token's items are the operands.
    Nullary operators are used for symbols, names and literals. Tokens with items
    represent the other operators (unary, binary and so on).

    Each token class has a *symbol*, a lbp (left binding power) value and a rbp
    (right binding power) value, that are used in the sense described by the
    Pratt's method. This implementation of Pratt tokens includes two extra
    attributes, *pattern* and *label*, that can be used to simplify the parsing
    of symbols in a concrete parser.

    :param parser: The parser instance that creates the token instance.
    :param value: The token value. If not provided defaults to token symbol.

    :cvar symbol: the symbol of the token class.
    :cvar lbp: Pratt's left binding power, defaults to 0.
    :cvar rbp: Pratt's right binding power, defaults to 0.
    :cvar pattern: the regex pattern used for the token class. Defaults to the \
    escaped symbol. Can be customized to match more detailed conditions (eg. a \
    function with its left round bracket), in order to simplify the related code.
    :cvar label: defines the typology of the token class. Its value is used in \
    representations of the token instance and can be used to restrict code choices \
    without more complicated analysis. The label value can be set as needed by the \
    parser implementation (eg. 'function', 'axis', 'constructor function' are used by \
    the XPath parsers). In the base parser class defaults to 'symbol' with 'literal' \
    and 'operator' as possible alternatives. If set by a tuple of values the token \
    class label is transformed to a multi-value label, that means the token class can \
    covers multiple roles (eg. as XPath function or axis). In those cases the definitive \
    role is defined at parse time (nud and/or led methods) after the token instance creation.
    """
    symbol = None     # the token identifier
    lbp = 0           # left binding power
    rbp = 0           # right binding power
    pattern = None    # a custom regex pattern for building the tokenizer
    label = 'symbol'  # optional label

    def __init__(self, parser, value=None):
        self._items = []
        self.parser = parser
        self.value = value if value is not None else self.symbol
        self._source: str = parser.source
        try:
            self.span = parser.match.span()
        except AttributeError:
            # If the token is created outside the parsing phase and then
            # the source string is the empty string and match is None
            self.span = (0, 0)

    def __getitem__(self, i):
        return self._items[i]

    def __setitem__(self, i, item):
        self._items[i] = item

    def __delitem__(self, i):
        del self._items[i]

    def __len__(self):
        return len(self._items)

    def insert(self, i, item):
        self._items.insert(i, item)

    def __str__(self):
        if self.symbol in SPECIAL_SYMBOLS:
            return '%r %s' % (self.value, self.symbol[1:-1])
        else:
            return '%r %s' % (self.symbol, self.label)

    def __repr__(self):
        symbol, value = self.symbol, self.value
        if value != symbol:
            return u'%s(value=%r)' % (self.__class__.__name__, value)
        else:
            return u'%s()' % self.__class__.__name__

    def __eq__(self, other):
        try:
            return self.symbol == other.symbol and self.value == other.value
        except AttributeError:
            return False

    @property
    def arity(self):
        return len(self)

    @property
    def tree(self):
        """Returns a tree representation string."""
        symbol, length = self.symbol, len(self)
        if symbol == '(name)':
            return u'(%s)' % self.value
        elif symbol in SPECIAL_SYMBOLS:
            return u'(%r)' % self.value
        elif symbol == '(':
            return '()' if not self else self[0].tree
        elif not length:
            return u'(%s)' % symbol
        else:
            return u'(%s %s)' % (symbol, ' '.join(item.tree for item in self))

    @property
    def source(self):
        """Returns the source representation string."""
        symbol = self.symbol
        if symbol == '(name)':
            return self.value
        elif symbol == '(decimal)':
            return str(self.value)
        elif symbol in SPECIAL_SYMBOLS:
            return repr(self.value)
        else:
            length = len(self)
            if not length:
                return symbol
            elif length == 1:
                if 'postfix' in self.label:
                    return '%s %s' % (self[0].source, symbol)
                return '%s %s' % (symbol, self[0].source)
            elif length == 2:
                return '%s %s %s' % (self[0].source, symbol, self[1].source)
            else:
                return '%s %s' % (symbol, ' '.join(item.source for item in self))

    @property
    def position(self):
        """A tuple with the position of the token in terms of line and column."""
        token_index = self.span[0]
        line = self._source[:token_index].count('\n') + 1
        if line == 1:
            return 1, token_index + 1
        return line, token_index - self._source[:token_index].rindex('\n')

    def nud(self):
        """Pratt's null denotation method"""
        raise self.wrong_syntax()

    def led(self, left):
        """Pratt's left denotation method"""
        raise self.wrong_syntax()

    def evaluate(self, *args, **kwargs):
        """Evaluation method"""

    def iter(self, *symbols):
        """Returns a generator for iterating the token's tree."""
        if not self:
            if not symbols or self.symbol in symbols:
                yield self
        elif len(self) == 1:
            if not symbols or self.symbol in symbols:
                yield self
            yield from self[0].iter(*symbols)
        else:
            yield from self[0].iter(*symbols)
            if not symbols or self.symbol in symbols:
                yield self
            for t in self._items[1:]:
                yield from t.iter(*symbols)

    def expected(self, *symbols, message=None):
        if symbols and self.symbol not in symbols:
            raise self.wrong_syntax(message)

    def unexpected(self, *symbols, message=None):
        if not symbols or self.symbol in symbols:
            raise self.wrong_syntax(message)

    def wrong_syntax(self, message=None):
        if message:
            return ParseError(message)
        elif self.symbol not in SPECIAL_SYMBOLS:
            return ParseError('unexpected %s' % self)
        elif self.symbol == '(invalid)':
            return ParseError('invalid literal %r' % self.value)
        elif self.symbol == '(unknown)':
            return ParseError('unknown symbol %r' % self.value)
        elif self.symbol == '(name)':
            return ParseError('unexpected name %r' % self.value)
        elif self.symbol != '(end)':
            return ParseError('unexpected literal %r' % self.value)
        elif self.parser.token is None:
            return ParseError('source is empty')
        else:
            return ParseError('unexpected end of source')

    def wrong_type(self, message='invalid type'):
        return TypeError(message)

    def wrong_value(self, message='invalid value'):
        return ValueError(message)


class ParserMeta(type):

    def __new__(mcs, name, bases, namespace):
        cls = super(ParserMeta, mcs).__new__(mcs, name, bases, namespace)

        # Avoids more parsers definitions for a single module
        for k, v in sys.modules[cls.__module__].__dict__.items():
            if isinstance(v, ParserMeta) and v.__module__ == cls.__module__:
                raise RuntimeError("Multiple parser class definitions per module are not allowed")

        # Checks and initializes class attributes
        if not hasattr(cls, 'token_base_class'):
            cls.token_base_class = Token
        if not hasattr(cls, 'literals_pattern'):
            cls.literals_pattern = re.compile(
                r"""'[^']*'|"[^"]*"|(?:\d+|\.\d+)(?:\.\d*)?(?:[Ee][+-]?\d+)?"""
            )
        if not hasattr(cls, 'name_pattern'):
            cls.name_pattern = re.compile(r'[A-Za-z0-9_]+')
        if 'tokenizer' not in namespace:
            cls.tokenizer = None
        if 'SYMBOLS' not in namespace:
            cls.SYMBOLS = set()
            for base_class in bases:
                if hasattr(base_class, 'SYMBOLS'):
                    cls.SYMBOLS.update(base_class.SYMBOLS)
                    break
        if 'symbol_table' not in namespace:
            cls.symbol_table = {}
            for base_class in bases:
                if hasattr(base_class, 'symbol_table'):
                    cls.symbol_table.update(base_class.symbol_table)
                    break
        return cls


class Parser(metaclass=ParserMeta):
    """
    Parser class for implementing a Top Down Operator Precedence parser.

    :cvar SYMBOLS: the symbols of the definable tokens for the parser. In the base class it's an \
    immutable set that contains the symbols for special tokens (literals, names and end-token).\
    Has to be extended in a concrete parser adding all the symbols of the language.
    :cvar symbol_table: a dictionary that stores the token classes defined for the language.
    :type symbol_table: dict
    :cvar token_base_class: the base class for creating language's token classes.
    :type token_base_class: Token
    :cvar tokenizer: the language tokenizer compiled regexp.
    """
    SYMBOLS = SPECIAL_SYMBOLS
    token_base_class = Token
    tokenizer = None
    symbol_table = {}

    def __init__(self):
        if self.tokenizer is None:
            self.build()
        self.source = ''
        self.tokens = iter(())
        self.match = None
        self.token = None
        self.next_token = None

    def __eq__(self, other):
        return self.token_base_class is other.token_base_class and \
            self.SYMBOLS == other.SYMBOLS and \
            self.symbol_table == other.symbol_table

    def parse(self, source):
        """
        Parses a source code of the formal language. This is the main method that has to be
        called for a parser's instance.

        :param source: The source string.
        :return: The root of the token's tree that parse the source.
        """
        try:
            try:
                self.tokens = iter(self.tokenizer.finditer(source))
            except TypeError as err:
                token = self.symbol_table['(invalid)'](self, type(source))
                raise token.wrong_syntax('invalid source type, {}'.format(err))

            self.source = source
            self.advance()
            root_token = self.expression()
            self.next_token.expected('(end)')
            return root_token
        finally:
            self.tokens = iter(())
            self.match = None
            self.token = None
            self.next_token = None

    def advance(self, *symbols):
        """
        The Pratt's function for advancing to next token.

        :param symbols: Optional arguments tuple. If not empty one of the provided \
        symbols is expected. If the next token's symbol differs the parser raises a \
        parse error.
        :return: The next token instance.
        """
        if self.next_token is not None:
            if self.next_token.symbol == '(end)' or \
                    symbols and self.next_token.symbol not in symbols:
                raise self.next_token.wrong_syntax()

        self.token = self.next_token
        while True:
            try:
                self.match = next(self.tokens)
            except StopIteration:
                self.next_token = self.symbol_table['(end)'](self)
                break
            else:
                literal, symbol, name, unknown = self.match.groups()
                if symbol is not None:
                    try:
                        self.next_token = self.symbol_table[symbol](self)
                    except KeyError:
                        if self.name_pattern.match(symbol) is None:
                            self.next_token = self.symbol_table['(unknown)'](self, symbol)
                            raise self.next_token.wrong_syntax()
                        self.next_token = self.symbol_table['(name)'](self, symbol)
                    break
                elif literal is not None:
                    if literal[0] in '\'"':
                        value = self.unescape(literal)
                        self.next_token = self.symbol_table['(string)'](self, value)
                    elif 'e' in literal or 'E' in literal:
                        try:
                            value = float(literal)
                        except ValueError as err:
                            self.next_token = self.symbol_table['(invalid)'](self, literal)
                            raise self.next_token.wrong_syntax(message=str(err))
                        else:
                            self.next_token = self.symbol_table['(float)'](self, value)
                    elif '.' in literal:
                        try:
                            value = Decimal(literal)
                        except DecimalException as err:
                            self.next_token = self.symbol_table['(invalid)'](self, literal)
                            raise self.next_token.wrong_syntax(message=str(err))
                        else:
                            self.next_token = self.symbol_table['(decimal)'](self, value)
                    else:
                        self.next_token = self.symbol_table['(integer)'](self, int(literal))
                    break
                elif name is not None:
                    self.next_token = self.symbol_table['(name)'](self, name)
                    break
                elif unknown is not None:
                    self.next_token = self.symbol_table['(unknown)'](self, unknown)
                    raise self.next_token.wrong_syntax()
                elif str(self.match.group()).strip():
                    msg = "unexpected matching %r: incompatible tokenizer"
                    raise RuntimeError(msg % self.match.group())
        return self.next_token

    def advance_until(self, *stop_symbols):
        """
        Advances until one of the symbols is found or the end of source is reached,
        returning the raw source string placed before. Useful for raw parsing of
        comments and references enclosed between specific symbols.

        :param stop_symbols: The symbols that have to be found for stopping advance.
        :return: The source string chunk enclosed between the initial position \
        and the first stop symbol.
        """
        if not stop_symbols:
            raise self.next_token.wrong_type("at least a stop symbol required!")
        elif self.next_token.symbol == '(end)':
            raise self.next_token.wrong_syntax()

        self.token = self.next_token
        source_chunk = []
        while True:
            try:
                self.match = next(self.tokens)
            except StopIteration:
                self.next_token = self.symbol_table['(end)'](self)
                break
            else:
                symbol = self.match.group(2)
                if symbol is not None:
                    symbol = symbol.strip()
                    if symbol not in stop_symbols:
                        source_chunk.append(symbol)
                    else:
                        try:
                            self.next_token = self.symbol_table[symbol](self)
                            break
                        except KeyError:
                            self.next_token = self.symbol_table['(unknown)'](self)
                            raise self.next_token.wrong_syntax()
                else:
                    source_chunk.append(self.match.group())
        return ''.join(source_chunk)

    def expression(self, rbp=0):
        """
        Pratt's function for parsing an expression. It calls token.nud() and then advances
        until the right binding power is less the left binding power of the next
        token, invoking the led() method on the following token.

        :param rbp: right binding power for the expression.
        :return: left token.
        """
        token = self.next_token
        self.advance()
        left = token.nud()
        while rbp < self.next_token.lbp:
            token = self.next_token
            self.advance()
            left = token.led(left)
        return left

    @property
    def position(self):
        """Property that returns the current line and column indexes."""
        if self.token is None:
            return 1, 1 + count_leading_spaces(self.source)
        return self.token.position

    def is_source_start(self):
        """
        Returns `True` if the parser is positioned at the start
        of the source, ignoring the spaces.
        """
        if self.token is None:
            return True
        return not bool(self.source[0:self.token.span[0]].strip())

    def is_line_start(self):
        """
        Returns `True` if the parser is positioned at the start
        of a source line, ignoring the spaces.
        """
        if self.token is None:
            return True
        token_index = self.token.span[0]
        try:
            line_start = self.source[:token_index].rindex('\n') + 1
        except ValueError:
            return not bool(self.source[:token_index].strip())
        else:
            return not bool(self.source[line_start:token_index].strip())

    def is_spaced(self, before=True, after=True):
        """
        Returns `True` if the source has an extra space (whitespace, tab or newline)
        immediately before or after the current position of the parser.

        :param before: if `True` considers also the extra spaces before \
        the current token symbol.
        :param after: if `True` considers also the extra spaces after \
        the current token symbol.
        """
        if self.token is None:
            return False

        start, end = self.token.span
        try:
            if before and start > 0 and self.source[start - 1] in ' \t\n':
                return True
            return after and self.source[end] in ' \t\n'
        except IndexError:
            return False

    @staticmethod
    def unescape(string_literal):
        return string_literal[1:-1].replace("\\'", "'").replace('\\"', '"')

    @classmethod
    def register(cls, symbol, **kwargs):
        """
        Register/update a token class in the symbol table.

        :param symbol: The identifier symbol for a new class or an existent token class.
        :param kwargs: Optional attributes/methods for the token class.
        :return: A token class.
        """
        try:
            try:
                if ' ' in symbol:
                    raise ValueError("%r: a symbol can't contain whitespaces" % symbol)
            except TypeError:
                assert isinstance(symbol, type) and issubclass(symbol, Token), \
                    "A %r subclass requested, not %r." % (Token, symbol)
                symbol, token_class = symbol.symbol, symbol
                assert symbol in cls.symbol_table and cls.symbol_table[symbol] is token_class, \
                    "Token class %r is not registered." % token_class
            else:
                token_class = cls.symbol_table[symbol]

        except KeyError:
            # Register a new symbol and create a new custom class. The new class
            # name is registered at parser class's module level.
            if symbol not in cls.SYMBOLS:
                raise NameError('%r is not a symbol of the parser %r.' % (symbol, cls))

            kwargs['symbol'] = symbol
            label = kwargs.get('label', 'symbol')
            if isinstance(label, tuple):
                label = kwargs['label'] = MultiLabel(*label)

            token_class_name = "_{}{}".format(
                symbol_to_classname(symbol), str(label).title().replace(' ', '')
            )
            token_class_bases = kwargs.get('bases', (cls.token_base_class,))
            kwargs.update({
                '__module__': cls.__module__,
                '__qualname__': token_class_name,
                '__return__': None
            })
            token_class = ABCMeta(token_class_name, token_class_bases, kwargs)
            cls.symbol_table[symbol] = token_class
            MutableSequence.register(token_class)
            setattr(sys.modules[cls.__module__], token_class_name, token_class)

        else:
            for key, value in kwargs.items():
                if key == 'lbp' and value > token_class.lbp:
                    token_class.lbp = value
                elif key == 'rbp' and value > token_class.rbp:
                    token_class.rbp = value
                elif callable(value):
                    setattr(token_class, key, value)

        return token_class

    @classmethod
    def unregister(cls, symbol):
        """Unregister a token class from the symbol table."""
        del cls.symbol_table[symbol.strip()]

    @classmethod
    def duplicate(cls, symbol, new_symbol, **kwargs):
        """Duplicate a token class with a new symbol."""
        token_class = cls.symbol_table[symbol]
        new_token_class = cls.register(new_symbol, **kwargs)
        for key, value in token_class.__dict__.items():
            if key in kwargs or key in ('symbol', 'pattern') or key.startswith('_'):
                continue
            setattr(new_token_class, key, value)
        return new_token_class

    @classmethod
    def literal(cls, symbol, bp=0):
        """Register a token for a symbol that represents a *literal*."""
        def nud(self):
            return self

        def evaluate(self, *_args, **_kwargs):
            return self.value

        return cls.register(symbol, label='literal', lbp=bp, evaluate=evaluate, nud=nud)

    @classmethod
    def nullary(cls, symbol, bp=0):
        """Register a token for a symbol that represents a *nullary* operator."""
        def nud(self):
            return self
        return cls.register(symbol, label='operator', lbp=bp, nud=nud)

    @classmethod
    def prefix(cls, symbol, bp=0):
        """Register a token for a symbol that represents a *prefix* unary operator."""
        def nud(self):
            self[:] = self.parser.expression(rbp=bp),
            return self
        return cls.register(symbol, label='prefix operator', lbp=bp, rbp=bp, nud=nud)

    @classmethod
    def postfix(cls, symbol, bp=0):
        """Register a token for a symbol that represents a *postfix* unary operator."""
        def led(self, left):
            self[:] = left,
            return self
        return cls.register(symbol, label='postfix operator', lbp=bp, rbp=bp, led=led)

    @classmethod
    def infix(cls, symbol, bp=0):
        """Register a token for a symbol that represents an *infix* binary operator."""
        def led(self, left):
            self[:] = left, self.parser.expression(rbp=bp)
            return self
        return cls.register(symbol, label='operator', lbp=bp, rbp=bp, led=led)

    @classmethod
    def infixr(cls, symbol, bp=0):
        """Register a token for a symbol that represents an *infixr* binary operator."""
        def led(self, left):
            self[:] = left, self.parser.expression(rbp=bp - 1)
            return self
        return cls.register(symbol, label='operator', lbp=bp, rbp=bp - 1, led=led)

    @classmethod
    def method(cls, symbol, bp=0):
        """
        Register a token for a symbol that represents a custom operator or redefine
        a method for an existing token.
        """
        token_class = cls.register(symbol, label='operator', lbp=bp, rbp=bp)

        def bind(func):
            assert callable(getattr(token_class, func.__name__, None)), \
                "The name %r does not match with a callable of %r." % (func.__name__, token_class)
            setattr(token_class, func.__name__, func)
            return func
        return bind

    @classmethod
    def build(cls):
        """
        Builds the parser class. Checks if all declared symbols are defined
        and builds a the regex tokenizer using the symbol related patterns.
        """
        if not cls.SYMBOLS.issubset(cls.symbol_table.keys()):
            unregistered = [s for s in cls.SYMBOLS if s not in cls.symbol_table]
            raise ValueError("The parser %r has unregistered symbols: %r" % (cls, unregistered))
        cls.tokenizer = cls.create_tokenizer(cls.symbol_table)

    build_tokenizer = build  # For backward compatibility

    @classmethod
    def create_tokenizer(cls, symbol_table):
        """
        Returns a regex based tokenizer built from a symbol table of token classes.
        The returned tokenizer skips extra spaces between symbols.

        A regular expression is created from the symbol table of the parser using a template.
        The symbols are inserted in the template putting the longer symbols first. Symbols and
        their patterns can't contain spaces.

        :param symbol_table: a dictionary containing the token classes of the formal language.
        """
        character_patterns = []
        string_patterns = []
        name_patterns = []
        custom_patterns = set()

        for symbol, token_class in symbol_table.items():
            if symbol in SPECIAL_SYMBOLS:
                continue
            elif token_class.pattern is not None:
                custom_patterns.add(token_class.pattern)
            elif cls.name_pattern.match(symbol) is not None:
                name_patterns.append(re.escape(symbol))
            elif len(symbol) == 1:
                character_patterns.append(re.escape(symbol))
            else:
                string_patterns.append(re.escape(symbol))

        symbols_patterns = []
        if string_patterns:
            symbols_patterns.append('|'.join(sorted(string_patterns, key=lambda x: -len(x))))
        if character_patterns:
            symbols_patterns.append('[{}]'.format(''.join(character_patterns)))
        if name_patterns:
            symbols_patterns.append(r'\b(?:{})\b(?![\-\.])'.format(
                '|'.join(sorted(name_patterns, key=lambda x: -len(x)))
            ))
        if custom_patterns:
            symbols_patterns.append('|'.join(custom_patterns))

        tokenizer_pattern = r"({})|({})|({})|(\S)|\s+".format(
            cls.literals_pattern.pattern,
            '|'.join(symbols_patterns),
            cls.name_pattern.pattern
        )
        return re.compile(tokenizer_pattern)
