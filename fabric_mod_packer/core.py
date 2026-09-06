"""核心逻辑：读取/校验 Fabric mod jar，合并元数据，打包与解包。"""

import json
import re
import zipfile
from pathlib import Path

from . import versions

NEST_DIR = "META-INF/jars"
ID_RE = re.compile(r"^[a-z][a-z0-9-_]{1,63}$")

# environment 取值 -> 运行侧集合，交集用它来算
_ENV_SIDES = {"*": {"client", "server"}, "client": {"client"}, "server": {"server"}}


class PackError(Exception):
    """可预期的错误（输入不合法、无法合并等），消息直接展示给用户。"""


class Mod:
    """一个输入 jar 的元数据摘要。"""

    def __init__(self, path, meta, entries):
        self.path = path
        self.meta = meta
        self.id = meta["id"]
        self.version = meta["version"]
        name = meta.get("name")
        self.name = name if isinstance(name, str) and name else self.id
        environment = meta.get("environment")
        self.environment = "*" if environment is None else environment
        self.depends = meta.get("depends", {})
        self.entries = entries


def load_mod(path):
    """读取并校验一个输入 jar；不是合法 Fabric mod 则抛 PackError。"""
    path = Path(path)
    if not path.is_file():
        raise PackError(f"文件不存在：{path}")

    def fail(reason):
        return PackError(f"无法识别为 Fabric Mod：{path}（{reason}）")

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise fail("不是有效的 zip/jar 文件") from None
    with zf:
        try:
            raw = zf.read("fabric.mod.json")
        except KeyError:
            raise fail("根目录没有 fabric.mod.json") from None
        entries = zf.namelist()
    try:
        meta = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:
        raise fail(f"fabric.mod.json 解析失败（{exc}）") from None
    if not isinstance(meta, dict):
        raise fail("fabric.mod.json 不是 JSON 对象")
    if meta.get("schemaVersion") != 1:
        raise fail(f"schemaVersion 不是 1：{meta.get('schemaVersion')!r}")
    if not isinstance(meta.get("id"), str) or not ID_RE.match(meta["id"]):
        raise fail(f"id 不合法：{meta.get('id')!r}")
    if not isinstance(meta.get("version"), str) or not meta["version"]:
        raise fail(f"version 缺失或不是字符串：{meta.get('version')!r}")
    if "depends" in meta and not isinstance(meta["depends"], dict):
        raise fail("depends 不是对象")
    return Mod(path, meta, entries)


def merge_environment(mods):
    """取各 mod environment 的交集，返回单一值；无交集则报错。"""
    sides = None
    for mod in mods:
        values = mod.environment if isinstance(mod.environment, list) else [mod.environment]
        own = set()
        for value in values:
            if value not in _ENV_SIDES:
                raise PackError(f"{mod.path} 的 environment 不认识：{value!r}")
            own |= _ENV_SIDES[value]
        sides = own if sides is None else sides & own
    if not sides:
        raise PackError("environment 无交集（存在分别要求 client 和 server 的 mod），无法合并")
    return "*" if sides == {"client", "server"} else sides.pop()


def merge_depends(mods):
    """depends 交集：只保留所有输入都声明的依赖，版本要求尽量合并。

    返回 (合并结果, 警告列表)；无法合并的版本要求保留第一个输入的写法并记录警告。
    """
    common = set(mods[0].depends)
    for mod in mods[1:]:
        common &= set(mod.depends)
    merged, warnings = {}, []
    for dep_id in mods[0].depends:
        if dep_id not in common:
            continue
        values = [mod.depends[dep_id] for mod in mods]
        result = versions.merge_specs(values)
        if result is None:
            warnings.append(
                f"依赖 {dep_id} 的版本要求无法自动合并，保留第一个输入的写法"
                f"（各输入依次为：{values}）"
            )
            result = values[0]
        merged[dep_id] = result
    return merged, warnings


def check_conflicts(mods):
    """打包前检查：mod id 重复、嵌套文件重名直接报错；条目路径重复返回警告列表。"""
    seen = {}
    for mod in mods:
        if mod.id in seen:
            raise PackError(f"mod id 重复：{mod.id} 同时来自 {seen[mod.id]} 和 {mod.path}")
        seen[mod.id] = mod.path
    names = {}
    for mod in mods:
        if mod.path.name in names:
            raise PackError(
                f"输入文件重名：{names[mod.path.name]} 与 {mod.path}，嵌入同一合并包会冲突"
            )
        names[mod.path.name] = mod.path
    owner = {}
    duplicated = {}
    for mod in mods:
        for entry in mod.entries:
            # fabric.mod.json 与 META-INF/ 是每个 mod 必有的，不算冲突；目录条目跳过
            if entry.endswith("/") or entry == "fabric.mod.json" or entry.startswith("META-INF/"):
                continue
            if entry in owner:
                duplicated.setdefault(entry, [owner[entry]]).append(str(mod.path))
            else:
                owner[entry] = str(mod.path)
    return [f"文件路径重复：{entry}（{'、'.join(paths)}）" for entry, paths in sorted(duplicated.items())]


class PackResult:
    """打包结果：输出路径、写入的元数据、输入 mod 列表与警告。"""

    def __init__(self, output, meta, mods, warnings):
        self.output = output
        self.meta = meta
        self.mods = mods
        self.warnings = warnings


def pack(paths, output=None):
    """把多个输入 jar 打包成一个 Jar-in-Jar 合并包，返回 PackResult。"""
    mods = [load_mod(path) for path in paths]
    if len(mods) < 2:
        raise PackError("至少需要 2 个输入 jar")
    warnings = check_conflicts(mods)
    count = len(mods) - 1
    meta = {
        "schemaVersion": 1,
        "id": f"{mods[0].id}-and-{count}-more",
        "version": f"{mods[0].version}-and-{count}-more",
        "name": f"{mods[0].name}-and-{count}-more",
        "environment": merge_environment(mods),
    }
    depends, dep_warnings = merge_depends(mods)
    warnings = warnings + dep_warnings
    if depends:
        meta["depends"] = depends
    meta["jars"] = [{"file": f"{NEST_DIR}/{mod.path.name}"} for mod in mods]

    out = Path(output) if output else Path(f"{meta['id']}.jar")
    if out.exists():
        raise PackError(f"输出文件已存在：{out}（换一个路径，或用 -o 指定）")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n\r\n")
        for mod in mods:
            zf.write(mod.path, f"{NEST_DIR}/{mod.path.name}")
        zf.writestr("fabric.mod.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    verify_output(out, meta)
    return PackResult(out, meta, mods, warnings)


def verify_output(out, meta):
    """打包后自检：重新打开产物，校验 CRC、元数据可读、声明的嵌套 jar 齐全。"""
    with zipfile.ZipFile(out) as zf:
        corrupt = zf.testzip()
        if corrupt:
            raise PackError(f"自检失败：{out} 中的 {corrupt} 已损坏")
        try:
            back = json.loads(zf.read("fabric.mod.json").decode("utf-8-sig"))
        except ValueError as exc:
            raise PackError(f"自检失败：{out} 的 fabric.mod.json 读不回来（{exc}）") from None
        if back.get("id") != meta["id"]:
            raise PackError(f"自检失败：{out} 的 id 与预期不符（{back.get('id')!r}）")
        present = set(zf.namelist())
        for entry in meta["jars"]:
            if entry["file"] not in present:
                raise PackError(f"自检失败：嵌套 jar 缺失：{entry['file']}")


def unpack(jar_path, output_dir=None):
    """把合并包 jars 字段声明的嵌套 jar 解到输出目录，返回 (目录, 文件路径列表)。"""
    jar_path = Path(jar_path)
    if not jar_path.is_file():
        raise PackError(f"文件不存在：{jar_path}")
    try:
        zf = zipfile.ZipFile(jar_path)
    except zipfile.BadZipFile:
        raise PackError(f"不是有效的 zip/jar 文件：{jar_path}") from None
    with zf:
        try:
            raw = zf.read("fabric.mod.json")
        except KeyError:
            raise PackError(f"不是 Fabric mod jar（根目录没有 fabric.mod.json）：{jar_path}") from None
        try:
            meta = json.loads(raw.decode("utf-8-sig"))
        except ValueError as exc:
            raise PackError(f"fabric.mod.json 解析失败：{jar_path}（{exc}）") from None
        entries = meta.get("jars")
        if not isinstance(entries, list) or not entries:
            raise PackError(
                f"该 jar 没有嵌套 mod（fabric.mod.json 没有 jars 字段），可能不是本工具打包的：{jar_path}"
            )
        out_dir = Path(output_dir) if output_dir else Path(jar_path.stem + "-unpacked")
        extracted = []
        for entry in entries:
            file = entry.get("file") if isinstance(entry, dict) else None
            if not isinstance(file, str) or not file:
                raise PackError(f"jars 条目缺少 file 字段：{entry!r}")
            try:
                data = zf.read(file)
            except KeyError:
                raise PackError(f"包里找不到嵌套 jar：{file}") from None
            dest = out_dir / Path(file).name
            if dest.exists():
                raise PackError(f"目标文件已存在：{dest}（换一个输出目录，或用 -o 指定）")
            extracted.append((dest, data))
    out_dir.mkdir(parents=True, exist_ok=True)
    for dest, data in extracted:
        dest.write_bytes(data)
    return out_dir, [dest for dest, _ in extracted]
