# Copyright 2020 Makani Technologies LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Python code generation backend."""

import re
import textwrap

from makani.lib.python import c_helpers
from makani.lib.python.pack2 import backend


class BackendPy(backend.Backend):
  """Python code generation backend."""

  _primary_type_map = {
      'uint8': 'py_types.UInt8',
      'int8': 'py_types.Int8',
      'uint16': 'py_types.UInt16',
      'int16': 'py_types.Int16',
      'uint32': 'py_types.UInt32',
      'int32': 'py_types.Int32',
      'float32': 'py_types.Float32',
      'date': 'py_types.Date',
  }

  # Maps enum width to ctypes._SimpleCData _type_.
  _enum_type_map = {
      1: 'b',
      2: 'h',
      4: 'l',
  }

  def __init__(self, c_header_path):
    super(self.__class__, self).__init__()

    self.c_header_path = c_header_path

    self.path_re = re.compile(r'/')
    self._Start()

  def _PathToModulePath(self, path):
    module_path = self.path_re.sub('.', path)
    return 'makani.' + module_path

  def _Start(self):
    self.source_string = textwrap.dedent("""\
        # This file is automatically generated.  Do not edit.

        import ctypes
        import yaml

        from makani.lib.python import pack2
        from makani.lib.python.pack2 import py_types

        """)

  def _Finalize(self):
    pass

  def _AddStruct(self, struct, include_extra):

    fields_str = ''
    offsets_str = ''

    # In order to produce source that we can exec(), we need to explicitly
    # declare our type references as being in the global scope.
    globals_set = set()

    for field in struct.body.fields:
      if field.type_obj.path:
        type_name = (self._PathToModulePath(field.type_obj.path)
                     + '.' + field.type_obj.name)
      else:
        type_name = field.type_obj.name
      if type_name in self._primary_type_map:
        type_name = self._primary_type_map[type_name]

      if type_name == 'string':
        fields_str += "      ('{name}', ctypes.c_char * {size}),\n".format(
            name=field.name,
            size=field.type_obj.width)
        globals_set.add('ctypes')
      elif field.extent == 1:
        fields_str += "      ('{name}', {type_name}),\n".format(
            type_name=type_name,
            name=field.name)
        parts = re.split(r'\.', type_name)
        globals_set.add(parts[0])
      else:
        fields_str += "      ('{name}', {type_name} * {extent}),\n".format(
            type_name=type_name,
            name=field.name,
            extent=field.extent)
        parts = re.split(r'\.', type_name)
        globals_set.add(parts[0])

      offsets_str += "      '{name}': {offset},\n".format(
          name=field.name, offset=field.offset)

      globals_str = ''
      for type_name in globals_set:
        globals_str += '  global %s\n' % type_name

    self.source_string += textwrap.dedent("""\
        class {type_name} (py_types.Structure):
        {globals_str}
          _fields_ = [
        {fields}
          ]
          _offsets_ = {{
        {offsets}
          }}
          size = {size}
          alignment = {alignment}
        """).format(type_name=struct.name,
                    size=struct.width,
                    alignment=struct.alignment,
                    globals_str=globals_str,
                    fields=fields_str,
                    offsets=offsets_str)

    if include_extra:
      self.source_string += (
          '  crc = 0x{crc:08x}\n'
          '  source = "{source}"\n'
          ) .format(crc=struct.Crc32(),
                    source=struct.Source())

      if not struct.forced_crc:
        self.source_string += 'pack2.RegisterParam({type_name})\n\n'.format(
            type_name=struct.name)

    self.source_string += '\n\n'

  def AddInclude(self, path):
    self.source_string += 'import %s\n' % self._PathToModulePath(path)

  def AddBitfield(self, bitfield):
    raise NotImplementedError('Bitfields not implemented for %s'
                              % self.__class__.__name__)

  def AddEnum(self, enum):
    value_map_str = ''
    c_value_map_str = ''
    name_map_str = ''
    constants_str = ''

    for value in sorted(enum.body.value_map.keys()):
      name = enum.body.value_map[value]
      const_name = c_helpers.CamelToSnake(name).upper()
      c_name = 'k' + enum.name + name

      value_map_str += "    {value}: '{name}',\n".format(name=name, value=value)
      c_value_map_str += "    {value}: '{c_name}',\n".format(
          c_name=c_name, value=value)
      name_map_str += "    '{name}': {value},\n".format(name=name, value=value)

      constants_str += (
          '{type_name}.{const_name} = {type_name}({value})\n'.format(
              const_name=const_name, value=value, type_name=enum.name))

    max_value = max(enum.body.value_map.keys())
    min_value = min(enum.body.value_map.keys())

    # Strip trailing newline from above generated code.
    value_map_str = value_map_str[:-1]
    name_map_str = name_map_str[:-1]
    constants_str = constants_str[:-1]

    header_path = self.c_header_path

    self.source_string += textwrap.dedent("""\
        class {type_name}(ctypes._SimpleCData, py_types.PackableCType):
          _type_ = '{type_code}'
          _value_map = {{
        {value_map}
          }}
          _c_value_map = {{
        {c_value_map}
          }}
          _name_map = {{
        {name_map}
          }}
          max_value = {max_value}
          min_value = {min_value}

          def __init__(self, value=0):
            super(self.__class__, self).__init__()
            self.__setstate__(value)

          def __setstate__(self, state):
            if isinstance(state, basestring):
              self.value = self._name_map[state]
            elif isinstance(state, self.__class__):
              self.value = state.value
            else:
              self.value = state

          def __repr__(self):
            return self._value_map[self.value]

          def __hash__(self):
            return self.value

          def __eq__(self, other):
            if isinstance(other, basestring):
              return self.value == self._name_map[other]
            elif isinstance(other, self.__class__):
              return self.value == other.value
            else:
              return self.value == other

          def __ne__(self, other):
            return not self.__eq__(other)

          def CName(self):
            return self._c_value_map[self.value]

          @classmethod
          def Names(cls):
            return [{type_name}(v) for v in cls._value_map.keys()]

          @classmethod
          def iteritems(cls):
            return cls._name_map.iteritems()

          @classmethod
          def HeaderFile(cls):
            return "{output_c_header}"

          @classmethod
          def TypeName(cls):
            return "{type_name}"

        {constants}

        """).format(type_name=enum.name,
                    type_code=self._enum_type_map[enum.width],
                    value_map=value_map_str,
                    c_value_map=c_value_map_str,
                    name_map=name_map_str,
                    max_value=max_value,
                    min_value=min_value,
                    output_c_header=header_path,
                    constants=constants_str)

  def AddScaled(self, bitfield):
    raise NotImplementedError('Scaleds not implemented for %s'
                              % self.__class__.__name__)

  def AddStruct(self, struct):
    self._AddStruct(struct, False)

  def _AddYamlLoader(self, obj):
    self.source_string += textwrap.dedent("""\
        class {type_name}YamlLoader(yaml.YAMLObject):
          global {type_name}
          global yaml
          yaml_tag = '!{type_name}'
          yaml_loader = yaml.SafeLoader
          data_type = {type_name}

          @classmethod
          def from_yaml(cls, loader, node):
            # Clear object cache to ensure all objects are deep.
            loader.constructed_objects = {{}}
            state = loader.construct_mapping(node, deep=True)
            return {type_name}(state=state)


        """).format(type_name=obj.name)

  def AddHeader(self, header):
    self.AddStruct(header)

  def AddParam(self, param):
    self._AddStruct(param, True)
    self._AddYamlLoader(param)

  def Finalize(self):
    self._Finalize()

  def GetSourceString(self, name):
    if name == 'source':
      return self.source_string
    else:
      raise ValueError('Unknown source %s.' % name)
