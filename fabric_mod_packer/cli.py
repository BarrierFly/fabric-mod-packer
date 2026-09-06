"""命令行入口：pack / unpack 两个子命令，支持通配符与交互式输入。"""

import argparse
import glob
import json
import shlex
import sys
from pathlib import Path

from . import core


def expand_patterns(patterns):
    """按输入顺序展开文件名/通配符模式；某模式没有匹配到任何文件则报错。"""
    files = []
    for pattern in patterns:
        matches = sorted(m for m in glob.glob(pattern) if Path(m).is_file())
        if not matches:
            raise core.PackError(f"没有文件匹配：{pattern}")
        files.extend(matches)
    return files


def _unquote(token):
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def interactive_patterns():
    """无参数运行时从控制台读一行：空格分隔多个文件/通配符，含空格的路径用引号括起。"""
    print("请输入 jar 文件名或通配符（空格分隔多个，含空格的路径用引号括起）：")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            sys.exit("输入中断")
        if line:
            return [_unquote(token) for token in shlex.split(line, posix=False)]
        print("没有输入任何内容，请重新输入。")


def cmd_pack(args):
    patterns = args.patterns if args.patterns else interactive_patterns()
    files = expand_patterns(patterns)
    print(f"共 {len(files)} 个输入 jar，开始打包...")
    result = core.pack(files, args.output)
    meta = result.meta
    print(f"打包完成：{result.output}")
    print(f"  id          {meta['id']}")
    print(f"  name        {meta['name']}")
    print(f"  version     {meta['version']}")
    print(f"  environment {meta['environment']}")
    print(f"  depends     {json.dumps(meta.get('depends', {}), ensure_ascii=False)}")
    print("  嵌入 mod:")
    for mod in result.mods:
        print(f"    {mod.id} {mod.version} <- {mod.path}")
    if result.warnings:
        print("警告:")
        for warning in result.warnings:
            print(f"  - {warning}")


def cmd_unpack(args):
    out_dir, files = core.unpack(args.jar, args.output)
    print(f"解包完成：{len(files)} 个 jar -> {out_dir}")
    for file in files:
        print(f"  - {file.name}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fabric-mod-packer",
        description="把多个 Fabric mod jar 打包成一个 Jar-in-Jar 合并包，或把合并包解包还原。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pack = sub.add_parser("pack", help="打包多个 mod jar")
    p_pack.add_argument("patterns", nargs="*", help="jar 文件名或通配符；省略则从控制台输入一行")
    p_pack.add_argument("-o", "--output", help="输出 jar 路径（默认 <合并id>.jar）")
    p_pack.set_defaults(func=cmd_pack)

    p_unpack = sub.add_parser("unpack", help="解包合并包")
    p_unpack.add_argument("jar", help="本工具打包出的 jar")
    p_unpack.add_argument("-o", "--output", help="输出目录（默认 <jar主名>-unpacked）")
    p_unpack.set_defaults(func=cmd_unpack)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except core.PackError as exc:
        sys.exit(f"错误：{exc}")
    except KeyboardInterrupt:
        sys.exit(130)
