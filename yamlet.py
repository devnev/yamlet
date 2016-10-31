#!/usr/bin/env python3
# vim: et sw=2 sts=2 ts=8

import itertools
import yaml
import re
import os.path


def execute(path, content=None):
  if not content:
    with open(path) as f:
      content = f.read()
  doc = parse(path, content, ParseContext())
  result = doc.execute()
  return yaml.serialize(result)


class LazyMap(object):
  def __init__(self, fn, m=None):
    self._fn = fn
    self._m = m
  def __getitem__(self, key):
    return self._fn(key)


class LazyObj(object):
  def __init__(self, fn, o=None):
    self._fn = fn
    self._o = o
  def __getattr__(self, key):
    return self._fn(key)


class FloatScalar(yaml.ScalarNode):
  TAG = 'tag:yaml.org,2002:float'
  def __init__(self, value):
    yaml.ScalarNode.__init__(self, self.TAG, str(value))
    self.float_value = value
  def __add__(self, other):
    if isinstance(other, int):
      return FloatScalar(self.float_value + other)
    if isinstance(other, float):
      return FloatScalar(self.float_value + other)
    if isinstance(other, IntScalar):
      return FloatScalar(self.float_value + other.int_value)
    if isinstance(other, FloatScalar):
      return FloatScalar(self.float_value + other.float_value)
    return NotImplemented
  def __radd__(self, other):
    return self.__add__(other)


class IntScalar(yaml.ScalarNode):
  TAG = 'tag:yaml.org,2002:int'
  def __init__(self, value):
    yaml.ScalarNode.__init__(self, self.TAG, str(value))
    self.int_value = value
  def __add__(self, other):
    if isinstance(other, int):
      return IntScalar(self.int_value + other)
    if isinstance(other, float):
      return FloatScalar(self.int_value + other)
    if isinstance(other, IntScalar):
      return IntScalar(self.int_value + other.int_value)
    if isinstance(other, FloatScalar):
      return FloatScalar(self.int_value + other.float_value)
    return NotImplemented
  def __radd__(self, other):
    return self.__add__(other)


class Sequence(yaml.SequenceNode):
  def __getitem__(self, index):
    return self.value[index]


class Mapping(yaml.MappingNode):
  def __getitem__(self, key):
    try:
      return next(v for k, v in self.value if k == key)
    except StopIteration:
      raise KeyError(key)


class ParseContext(object):
  def __init__(self):
    self.loaded_documents = {}
    self.trace = []
  def _push_trace(self, trace_item):
    ctx = ParseContext()
    ctx.loaded_documents = self.loaded_documents
    ctx.trace = self.trace + [trace_item]
    return ctx
  def load(self, path):
    if path in self.loaded_documents:
      if not self.loaded_documents[path]:
        raise Exception("circular import of {} (trace: {})".format(path, self.trace))
      return self.loaded_documents[path]
    with open(path) as f:
      content = f.read()
    self.loaded_documents[path] = None
    doc = parse(path, content, self._push_trace(path))
    self.loaded_documents[path] = doc
    return doc


def parse(path, content, ctx):
  loader = yaml.Loader(content)
  loader.add_implicit_resolver('!expr', re.compile(r'^\(.*$'), '(')
  try:
    root = loader.get_single_node()
  finally:
    loader.dispose()
  if not isinstance(root, yaml.MappingNode):
    raise Exception("root of {} is not a map".format(path))

  for k, _ in root.value:
    if not isinstance(k, yaml.ScalarNode):
      raise Exception("root of {} has non-scalar key {}".format(path, k))

  keys = set(v[0].value for v in root.value)
  unknown_keys = keys - set(['imports', 'locals', 'exports', 'result'])
  if unknown_keys:
    raise Exception("unknown keys {} in template {}".format(unknown_keys, path))

  root_map = dict((k.value, v) for k, v in root.value)
  for name in ['imports', 'locals', 'exports']:
    if name not in root_map:
      root_map[name] = yaml.MappingNode(None, [])
    elif not isinstance(root_map[name], yaml.MappingNode):
      raise Exception("{} of {} is not a map".format(name, path))
    for k, _ in root_map[name].value:
      if not isinstance(k, yaml.ScalarNode):
        raise Exception("{} of {} has non-scalar key {}".format(name, path, k))

  multiple_definitions = set()
  for n1, n2 in itertools.combinations(['imports', 'locals', 'exports'], 2):
    n1_names = set(k for k, v in root_map[n1].value)
    n2_names = set(k for k, v in root_map[n2].value)
    multiple_definitions |= n1_names & n2_names
  if multiple_definitions:
    raise Exception("multiple top-level definitions for {} in {}".format(multiple_definitions, path))

  imports = {}
  if 'imports' in root_map:
    for imp_name, imp_relpath in root_map['imports'].value:
      if not isinstance(imp_name, yaml.ScalarNode):
        raise Exception("import name not a scalar")
      if not isinstance(imp_relpath, yaml.ScalarNode):
        raise Exception("import path not a scalar")
      imp_name = imp_name.value
      imp_relpath = imp_relpath.value
      imp_path = os.path.join(os.path.dirname(path), imp_relpath)
      imports[imp_name] = ctx.load(imp_path)

  internal = dict(
      [(k.value, v) for k, v in root_map['locals'].value] +
      [(k.value, v) for k, v in root_map['exports'].value])
  exports = dict((k.value, v) for k, v in root_map['exports'].value)

  internal_scope = Scopes(internal, parent=None, internal=None)
  exports_scope = Scopes(exports, parent=None, internal=internal_scope)

  return Document(
      path=path,
      imports=imports,
      internal=internal_scope,
      exports=exports_scope,
      result=root_map.get('result'),
      )


class Document(object):
  def __init__(self, path, imports, internal, exports, result):
    self.path = path
    self.imports = imports
    self.internal = internal
    self.exports = exports
    self.result = result
  def load_export(self, name, transformed):
    if not self.exports.has(name):
      raise AttributeError("name {} not exported by {}".format(name, self.path))
    return self.exports.transform(name, self, transformed)
  def execute(self):
    return transform(self.result, self, self.internal, {})


class Scopes(object):
  def __init__(self, items, parent, internal):
    self.items = items
    self.parent = parent
    self.internal = internal or self
  def has(self, name):
    if name in self.items:
      return True
    if not self.parent:
      return False
    return self.parent.find(name)
  def transform(self, name, document, transformed):
    if name in self.items:
      return transform(self.items[name], document, self.internal, transformed)
    if self.parent:
      return self.parent.transform(name, document, transformed)
    raise AttributeError("name {} not in scope".format(name))


def transform(node, document, scopes, transformed):
  if (node, scopes) in transformed:
    return transformed[(node, scopes)]
  local_transform = lambda n: transform(n, document, scopes, transformed)

  if isinstance(node, yaml.SequenceNode):
    children = [local_transform(n) for n in node.value]
    tr = Sequence(node.tag, children, node.start_mark, node.end_mark, node.flow_style)
    transformed[(node, scopes)] = tr
    return tr

  if isinstance(node, yaml.MappingNode):
    children = [(local_transform(k), local_transform(v)) for k, v in node.value]
    tr = Mapping(node.tag, children, node.start_mark, node.end_mark, node.flow_style)
    transformed[(node, scopes)] = tr
    return tr

  if not isinstance(node, yaml.ScalarNode):
    raise Exception("unknown node type {}".format(node))

  if node.tag == IntScalar.TAG:
    tr = IntScalar(int(node.value))
    transformed[(node, scopes)] = tr
    return tr

  if node.tag == FloatScalar.TAG:
    tr = IntScalar(float(node.value))
    transformed[(node, scopes)] = tr
    return tr

  if node.tag == '!expr':
    return eval_expr(node.value, document, scopes, transformed)

  return node


def eval_expr(expr, document, scopes, transformed):

  def lookup_mapping(mapping, key):
    try:
      return next(v for k, v in mapping.value if isinstance(k, yaml.ScalarNode) and k.value == key)
    except StopIteration:
      raise KeyError("%s not in %s" % (key, mapping))

  def wrap(node):
    if isinstance(node, yaml.SequenceNode):
      return LazyMap(lambda k: wrap(node.value[k]), node)
    elif isinstance(node, yaml.MappingNode):
      return LazyObj(lambda k: wrap(lookup_mapping(node, k)), node)
    else:
      return node

  def unwrap(node):
    if isinstance(node, LazyObj) and node._o:
      return node._o
    if isinstance(node, LazyMap) and node._m:
      return node._m
    return node

  def lookup_import(import_name, name):
    return wrap(document.imports[import_name].load_export(name, transformed))

  def lookup(name):
    if scopes.has(name):
      return wrap(scopes.transform(name, document, transformed))
    if name in document.imports:
      return LazyObj(lambda name2: lookup_import(name, name2))
    raise AttributeError("name {} not in scope".format(name))

  result = unwrap(eval(expr, {'__builtins__':None}, LazyMap(lookup)))

  if isinstance(result, yaml.Node):
    return result
  elif isinstance(result, int):
    return IntScalar(result)
  elif isinstance(result, float):
    return FloatScalar(result)
  elif isinstance(result, str):
    return yaml.ScalarNode(None, result)
  else:
    raise ValueError("cannot transform {} to YAML node".format(result))


if __name__ == "__main__":
  import sys
  result = execute(sys.argv[1])
  print(result, end='')
