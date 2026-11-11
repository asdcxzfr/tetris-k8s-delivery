# 11 字符串 `N` 被解析成布尔 `false`：YAML 1.1 与 Norway problem 的二次踩坑

> **一句话**：`yaml.safe_dump` 认为字符串 `N` 不需要引号，而 k8s 用的 go-yaml 把裸 `N` 读成布尔 `false`。两个库各自"正确"，交接处就炸。同一个坑在本项目里踩了两次——一次手写 YAML，一次用 PyYAML 生成快照。

**领域**：YAML 规范 / 工具链互操作 · **难度**：★★★☆☆ · **状态**：已修复（有工具兜底）

## 现象

`kubectl apply` 直接拒绝：

```
The Rollout "tetris-game" is invalid:
spec.template.spec.containers[0].env[1].name: Invalid value: "boolean":
  spec.template.spec.containers[0].env[1].name in body must be of type string: "boolean"
```

诡异之处：**源文件里那一行看起来明明是字符串**。

```yaml
env:
  - name: N
    value: "1"
```

第二次更隐蔽：手工写的 AnalysisTemplate 没问题，但用 PyYAML 把集群对象导出成清单、再 `kubectl apply` 同一份快照时，又报同样的错——**导出脚本把所有字母序靠前的位置改坏了**。

## 排查

用两个解析器读同一段文本，看它们各自解析成什么：

```bash
python3 - <<'PY'
import yaml
for s in ['N: 1', 'N': '1']:
    pass
print(yaml.safe_load('name: N'))
# {'name': 'N'}   ← PyYAML: 字符串
PY
```

再用 k8s 自己的解析器验证（它有 `--dry-run=server`，但类型校验在客户端 schema 就会挂，直接看报错类型最快）：

```bash
kubectl apply --dry-run=server -f bad.yaml   # 报 must be of type string: "boolean"
echo '{"name": N}' | kubectl create --dry-run=server -f - >/dev/null
```

## 根因

**YAML 1.1 把 `y` / `Y` / `n` / `N` / `yes` / `no` / `on` / `off` 都定义为布尔**。这带来一个著名副作用：任何以这些字母开头的普通单词都会被吃掉（`Norway` 的 `N` 让它变成 `false`），社区称之为 **Norway problem**。

两个库的处理方式不同，而**各自的处理都是对的**：

- **PyYAML 刻意偏离规范**：它不把单字母 `y/Y/n/N` 当布尔（只认 `yes/no/on/off/true/false`）。于是 `yaml.safe_dump('N')` 输出裸 `N`——在 PyYAML 的世界里这没有歧义。
- **go-yaml（k8s 用的）是完整的 YAML 1.1**：裸 `N` 解析成布尔 `false`。

于是 PyYAML 生成的"合法 YAML"交给 k8s 就变成了 `name: false`。类型校验当然失败，而且报错位置在**被改坏的字段**上，离真正的元凶（导出脚本）很远。

## 修复

治本：**导出/生成 YAML 时，对所有歧义字符串强制加引号**。这就是仓库里唯一的工具 `99-tools/export-clean.py`（2130 字节）的职责：

```python
class QuotedStr(str):
    pass

def represent_quoted(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')

yaml.add_representer(QuotedStr, represent_quoted)
# 命中歧义集合就包成 QuotedStr：N/Y/n/y/yes/no/on/off/true/false/null/~/数字形态/空串
```

治标（手写时）：**只要值可能被误判，就一律加引号**，尤其是 `N`、`Y`、`on`、`off`、`yes`、`no`，以及 `1.0`、`007`、`null`、`~`、空字符串。

另一条实践：**不要用 PyYAML 直接 dump 集群对象**当作交付快照。要交付的是"服务端解析后仍等价"的东西，所以校验必须包含一步 `kubectl apply --dry-run=server`。

## 验证

```bash
# 1) 全仓扫描可疑裸值（env name / 标签值 / 注解值）
grep -rnE '^\s*(- )?(name|value|app|version): *(N|Y|n|y|yes|no|on|off)\s*$' 02-k8s/

# 2) 逐文档做服务端 dry-run（唯一可信的类型校验）
for f in 02-k8s/*.yaml; do
  kubectl apply --dry-run=server -f "$f" >/dev/null 2>&1 || echo "FAIL: $f"
done
```

本项目此后的校验 A 就是跑这套循环，11 个文件 0 失败。

## 教训

1. **"两个库都符合各自规范"也能出错**：互操作问题不会在任何一边报错，只会集中在交接处爆炸。
2. **凡是"生成 YAML"的代码，都要有引号策略**，不能依赖 dump 库的默认行为；默认行为随库、版本、甚至语言而变。
3. **交付物必须经过目标系统的校验器**（`--dry-run=server`），而不是"我这边能解析"。类型错误的代价是把人引向错误的位置。
