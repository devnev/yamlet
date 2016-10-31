# YAML Templates

## Example

`utils.yaml`
```
exports:
  add: !func
    params: [x, y]
    result: (x + y)
```

`thing.yaml`
```
imports:
  utils: utils.yaml
result:
  the_usual: plain strings á la YAML
  some_func_calls: (utils.add(1, utils.add(2, 3)))
  just_an_expr: (1 + 2)
  not_an_expr: 1 + 2
```

output:
```
$ yamlet.py thing.yaml
the_usual: plain strings á la YAML
some_func_calls: 6
just_an_expr: 3
not_an_expr: 1 + 2
```
